from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from time import time
import unittest


APP_PATH = Path(__file__).resolve().parents[2] / "qtronic_sms_gateway" / "app"
sys.path.insert(0, str(APP_PATH))

from qtronic_gateway.config import HistoryConfig, load_config  # noqa: E402
from qtronic_gateway.security import (  # noqa: E402
    APIAuthenticator,
    is_supervisor_ingress_request,
    load_or_create_api_token,
)
from qtronic_gateway.storage import PersistentStore  # noqa: E402
from qtronic_gateway.validation import HistoryQuery  # noqa: E402


class SecurityTests(unittest.TestCase):
    def test_confirmation_timeout_is_clamped_to_firmware_safe_minimum(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "options.json"
            path.write_text(
                json.dumps(
                    {
                        "esphome": {"host": "esp.local"},
                        "sms": {"confirmation_timeout_s": 1},
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(load_config(path).sms.confirmation_timeout_s, 75)

    def test_token_is_persistent_and_private(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "secret" / "api_token"
            first = load_or_create_api_token(path)
            second = load_or_create_api_token(path)
            self.assertEqual(first, second)
            self.assertGreaterEqual(len(first), 32)
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_auth_and_ingress_origin_checks(self) -> None:
        auth = APIAuthenticator("x" * 43)
        self.assertTrue(
            auth.authorized({"Authorization": "Bearer " + "x" * 43}, "10.0.0.2")
        )
        self.assertFalse(auth.authorized({"Authorization": "Bearer wrong"}, "10.0.0.2"))
        ingress = {"X-Ingress-Path": "/api/hassio_ingress/example/"}
        self.assertTrue(is_supervisor_ingress_request(ingress, "172.30.32.1"))
        self.assertTrue(is_supervisor_ingress_request(ingress, "172.30.32.2"))
        self.assertFalse(is_supervisor_ingress_request(ingress, "172.30.33.7"))
        self.assertFalse(is_supervisor_ingress_request({}, "172.30.32.2"))

    def test_write_rate_limit_is_bounded(self) -> None:
        auth = APIAuthenticator("token", rate_limit_requests=2, rate_limit_window_s=60)
        self.assertTrue(auth.allow_request("client"))
        self.assertTrue(auth.allow_request("client"))
        self.assertFalse(auth.allow_request("client"))


class StorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "gateway.sqlite3"
        self.store = PersistentStore(
            HistoryConfig(
                enabled=True, retention_days=30, max_entries=100, privacy="masked"
            ),
            self.path,
        )
        await self.store.initialize()

    async def asyncTearDown(self) -> None:
        await self.store.close()
        self.temporary.cleanup()

    async def test_uuid_survives_reopen(self) -> None:
        first = await self.store.gateway_uuid()
        await self.store.close()
        self.store = PersistentStore(
            HistoryConfig(
                enabled=True, retention_days=30, max_entries=100, privacy="masked"
            ),
            self.path,
        )
        await self.store.initialize()
        self.assertEqual(first, await self.store.gateway_uuid())

    async def test_outbox_is_idempotent_and_recovers_sending(self) -> None:
        first, created = await self.store.enqueue_outbox(
            kind="sms",
            payload={"message": "hello"},
            max_attempts=3,
            idempotency_key="request-1",
        )
        second, created_again = await self.store.enqueue_outbox(
            kind="sms",
            payload={"message": "different"},
            max_attempts=3,
            idempotency_key="request-1",
        )
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first["job_id"], second["job_id"])
        claimed = await self.store.claim_next_outbox(kind="sms")
        self.assertEqual(claimed["status"], "sending")
        await self.store.close()
        self.store = PersistentStore(
            HistoryConfig(
                enabled=True, retention_days=30, max_entries=100, privacy="masked"
            ),
            self.path,
        )
        await self.store.initialize()
        recovered = await self.store.get_outbox(first["job_id"])
        self.assertEqual(recovered["status"], "unknown")
        self.assertIn("uncertain", recovered["last_error"])

    async def test_history_masks_phone_but_not_iso_date_and_exports(self) -> None:
        await self.store.append_history(
            {
                "type": "sms_received",
                "timestamp": time(),
                "sender": "+48535000111",
                "message": "date 2026-08-11, formula =2+2",
                "message_segments": ["secret part one", "secret part two"],
                "note": "date 2026-08-11",
            }
        )
        rows = await self.store.query_history(HistoryQuery(limit=10))
        payload = rows[0]["payload"]
        self.assertEqual(payload["sender"], "+*******0111")
        self.assertNotIn("formula", payload["message"])
        self.assertNotIn("secret part", payload["message_segments"])
        self.assertIn("2026-08-11", payload["note"])
        exported, media_type = await self.store.export_history(
            HistoryQuery(limit=10), "json"
        )
        self.assertEqual(media_type, "application/json")
        self.assertEqual(json.loads(exported)[0]["type"], "sms_received")

    async def test_metadata_removes_all_message_content(self) -> None:
        metadata_store = PersistentStore(
            HistoryConfig(
                enabled=True, retention_days=30, max_entries=100, privacy="metadata"
            ),
            Path(self.temporary.name) / "metadata.sqlite3",
        )
        await metadata_store.initialize()
        await metadata_store.append_history(
            {
                "type": "sms_received",
                "timestamp": time(),
                "message": "top secret",
                "message_search": "top secret",
                "nested": {"message_segments": ["part one", "part two"]},
            }
        )
        payload = (await metadata_store.query_history(HistoryQuery(limit=10)))[0][
            "payload"
        ]
        serialized = json.dumps(payload)
        self.assertNotIn("top secret", serialized)
        self.assertNotIn("part one", serialized)
        self.assertNotIn("message_segments", serialized)
        await metadata_store.close()

    async def test_terminal_outbox_is_redacted_and_cancel_wins_race(self) -> None:
        row, _ = await self.store.enqueue_outbox(
            kind="sms",
            payload={
                "message": "terminal secret",
                "message_segments": ["terminal", " secret"],
                "recipients": ["+48535000111"],
            },
            max_attempts=3,
        )
        await self.store.claim_next_outbox(kind="sms")
        self.assertEqual(await self.store.cancel_outbox(row["job_id"]), 1)
        await self.store.update_outbox(row["job_id"], "retry")
        await self.store.update_outbox(
            row["job_id"],
            "sent",
            result={
                "completed_recipients": ["+48535000111"],
                "message": "terminal secret result",
                "message_segments": ["terminal", " secret result"],
            },
        )
        terminal = await self.store.get_outbox(row["job_id"])
        self.assertEqual(terminal["status"], "canceled")
        serialized = json.dumps(
            {"payload": terminal["payload"], "result": terminal["result"]}
        )
        self.assertNotIn("terminal secret", serialized)
        self.assertNotIn("+48535000111", serialized)

        sent_row, _ = await self.store.enqueue_outbox(
            kind="sms",
            payload={
                "message": "sent payload secret",
                "message_segments": ["sent", " payload secret"],
                "recipients": ["+48535000222"],
            },
            max_attempts=1,
        )
        await self.store.update_outbox(
            sent_row["job_id"],
            "sent",
            result={
                "completed_recipients": ["+48535000222"],
                "message": "sent result secret",
                "message_segments": ["sent", " result secret"],
            },
        )
        sent_terminal = await self.store.get_outbox(sent_row["job_id"])
        sent_serialized = json.dumps(sent_terminal)
        self.assertNotIn("sent payload secret", sent_serialized)
        self.assertNotIn("sent result secret", sent_serialized)
        self.assertNotIn("+48535000222", sent_serialized)


class StaticUiTests(unittest.TestCase):
    def test_dashboard_does_not_use_html_injection_sinks(self) -> None:
        server = (APP_PATH / "server.py").read_text(encoding="utf-8")
        self.assertNotIn("innerHTML", server)
        self.assertNotIn("insertAdjacentHTML", server)


if __name__ == "__main__":
    unittest.main()
