from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest


APP_PATH = Path(__file__).resolve().parents[2] / "qtronic_sms_gateway" / "app"
sys.path.insert(0, str(APP_PATH))

from qtronic_gateway.config import (  # noqa: E402
    AddonConfig,
    CallingConfig,
    ESPHomeConfig,
    HistoryConfig,
    MQTTConfig,
    SMSConfig,
)
from qtronic_gateway.gateway import GatewayService  # noqa: E402
from qtronic_gateway.mqtt_bridge import MQTTBridge  # noqa: E402
from qtronic_gateway.storage import PersistentStore  # noqa: E402


def make_config(*, retry_forever: bool = False) -> AddonConfig:
    return AddonConfig(
        esphome=ESPHomeConfig(
            host="esp.local",
            port=6053,
            encryption_key="",
            send_sms_action="send_sms",
            unicode_send_sms_action="send_sms_unicode",
            dial_action="dial",
            disconnect_action="disconnect",
            rssi_object_id="rssi",
            registered_object_id="registered",
            modem_online_object_id="modem_online",
            sms_sender_object_id="sms_sender",
            sms_message_object_id="sms_message",
            incoming_call_object_id="incoming_call",
            call_state_object_id="call_state",
            ussd_object_id="ussd",
        ),
        sms=SMSConfig(default_encoding="auto", send_delay_ms=0),
        calling=CallingConfig(
            default_ring_time_s=1,
            delay_between_calls_s=0,
            max_retries=0,
            retry_delay_s=100,
            retry_forever=retry_forever,
            failure_action="next_recipient",
        ),
        mqtt=MQTTConfig(
            enabled=False,
            host="localhost",
            port=1883,
            username=None,
            password=None,
            topic_prefix="test",
            discovery_enabled=False,
            discovery_prefix="homeassistant",
        ),
        recipients=(),
    )


class FakeClient:
    def __init__(
        self,
        *,
        fail_dial: bool = False,
        block_dial: bool = False,
        fail_sms_messages: set[str] | None = None,
    ) -> None:
        self.fail_dial = fail_dial
        self.block_dial = block_dial
        self.fail_sms_messages = set(fail_sms_messages or set())
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.dial_seen = asyncio.Event()
        self.dial_release = asyncio.Event()

    async def execute_service(self, service, data) -> None:
        self.calls.append((service.name, data))
        if service.name == "dial":
            self.dial_seen.set()
            if self.fail_dial:
                raise RuntimeError("dial failed")
            if self.block_dial:
                await self.dial_release.wait()
        if service.name == "send_sms" and data.get("message") in self.fail_sms_messages:
            self.fail_sms_messages.remove(str(data["message"]))
            raise RuntimeError("sms part failed")


def service(name: str, *arguments: str):
    return SimpleNamespace(
        name=name,
        args=[SimpleNamespace(name=argument) for argument in arguments],
    )


class GatewayRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_hangup_preempts_retry_forever_without_waiting_for_lock(self) -> None:
        gateway = GatewayService(make_config(retry_forever=True))
        client = FakeClient(fail_dial=True)
        gateway._client = client
        gateway.user_services = {
            "dial": service("dial", "recipient"),
            "disconnect": service("disconnect"),
        }
        task = asyncio.create_task(
            gateway.async_call_batch(recipients=["+48535000111"], ring_time_s=1)
        )
        await asyncio.wait_for(client.dial_seen.wait(), timeout=1)
        result = await asyncio.wait_for(gateway.async_hangup(), timeout=1)
        call_result = await asyncio.wait_for(task, timeout=1)
        self.assertTrue(result["disconnect_sent"])
        self.assertEqual(call_result["status"], "canceled")
        self.assertIn("disconnect", [name for name, _ in client.calls])

    async def test_unconfirmed_service_call_is_unknown_not_sent(self) -> None:
        gateway = GatewayService(make_config())
        client = FakeClient()
        gateway._client = client
        gateway.user_services = {
            "send_sms": service("send_sms", "recipient", "message")
        }
        result = await gateway._async_send_sms_batch_now(
            message="hello",
            recipients=["+48535000111"],
            encoding="auto",
            batch_id="test-job",
        )
        self.assertEqual(result["status"], "unknown")
        event_types = [event["type"] for event in gateway.events_snapshot()]
        self.assertIn("sms_transport_accepted", event_types)
        self.assertNotIn("sms_sent", event_types)

    async def test_persistent_sms_can_be_canceled_before_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = PersistentStore(
                HistoryConfig(enabled=True, retention_days=30, max_entries=100),
                Path(temporary) / "store.sqlite3",
            )
            await store.initialize()
            gateway = GatewayService(make_config(), store=store)
            row, _ = await store.enqueue_outbox(
                kind="sms",
                payload={"message": "hello"},
                max_attempts=3,
            )
            result = await gateway.async_cancel(row["job_id"])
            self.assertEqual(result["canceled_persistent"], 1)
            self.assertEqual(
                (await store.get_outbox(row["job_id"]))["status"], "canceled"
            )
            await store.close()

    async def test_wait_is_bounded_while_gateway_is_offline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = PersistentStore(
                HistoryConfig(enabled=True, retention_days=30, max_entries=100),
                Path(temporary) / "store.sqlite3",
            )
            await store.initialize()
            gateway = GatewayService(make_config(), store=store)
            row, _ = await store.enqueue_outbox(
                kind="sms", payload={"message": "queued"}, max_attempts=3
            )
            result = await asyncio.wait_for(
                gateway.async_wait_outbox_job(row["job_id"], timeout_s=0.05),
                timeout=0.5,
            )
            self.assertEqual(result["status"], "accepted")
            self.assertEqual(result["wait_status"], "timeout")
            self.assertEqual(result["delivery_status"], "accepted")
            await store.close()

    async def test_retry_resumes_at_failed_segment_checkpoint(self) -> None:
        gateway = GatewayService(make_config())
        client = FakeClient(fail_sms_messages={"part-2"})
        gateway._client = client
        gateway.user_services = {
            "send_sms": service("send_sms", "recipient", "message")
        }

        async def confirmed(_version, _cancel_event):
            return "sent", None

        gateway._wait_for_sms_confirmation = confirmed  # type: ignore[method-assign]
        checkpoints: list[dict[str, object]] = []

        async def save_checkpoint(checkpoint):
            checkpoints.append(dict(checkpoint))

        with self.assertRaisesRegex(RuntimeError, "sms part failed"):
            await gateway._async_send_sms_batch_now(
                message="part-1part-2",
                message_segments=["part-1", "part-2"],
                recipients=["+48535000111"],
                encoding="auto",
                batch_id="checkpoint-job",
                progress_callback=save_checkpoint,
            )
        self.assertEqual(checkpoints[-1]["recipient_index"], 0)
        self.assertEqual(checkpoints[-1]["part_index"], 1)

        result = await gateway._async_send_sms_batch_now(
            message="part-1part-2",
            message_segments=["part-1", "part-2"],
            recipients=["+48535000111"],
            encoding="auto",
            batch_id="checkpoint-job",
            checkpoint=checkpoints[-1],
            progress_callback=save_checkpoint,
        )
        sent_messages = [
            str(data["message"]) for name, data in client.calls if name == "send_sms"
        ]
        self.assertEqual(sent_messages, ["part-1", "part-2", "part-2"])
        self.assertEqual(result["status"], "sent")

    async def test_mqtt_hangup_is_not_blocked_by_retry_forever_call(self) -> None:
        gateway = GatewayService(make_config(retry_forever=True))
        client = FakeClient(block_dial=True)
        gateway._client = client
        gateway.user_services = {
            "dial": service("dial", "recipient"),
            "disconnect": service("disconnect"),
        }
        bridge = MQTTBridge(gateway)

        class Messages:
            def __init__(self):
                self.index = 0

            def __aiter__(self):
                return self

            async def __anext__(self):
                self.index += 1
                if self.index == 1:
                    return SimpleNamespace(
                        topic="test/call/set",
                        payload=json.dumps(
                            {
                                "recipient": "+48535000111",
                                "ring_time_s": 1,
                            }
                        ).encode(),
                    )
                if self.index == 2:
                    await asyncio.wait_for(client.dial_seen.wait(), timeout=1)
                    return SimpleNamespace(topic="test/hangup/set", payload=b"{}")
                raise StopAsyncIteration

        mqtt_client = SimpleNamespace(messages=Messages())
        await asyncio.wait_for(bridge._command_loop(mqtt_client), timeout=0.5)
        await bridge._stop_command_tasks()
        self.assertEqual([name for name, _ in client.calls].count("disconnect"), 1)

    async def test_mqtt_cancel_disconnects_active_call_before_finishing_task(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = PersistentStore(
                HistoryConfig(enabled=False, retention_days=30, max_entries=100),
                Path(temporary) / "store.sqlite3",
            )
            await store.initialize()
            gateway = GatewayService(make_config(retry_forever=True), store=store)
            client = FakeClient(block_dial=True)
            gateway._client = client
            gateway.user_services = {
                "dial": service("dial", "recipient"),
                "disconnect": service("disconnect"),
            }
            bridge = MQTTBridge(gateway)
            published: list[tuple[str, dict[str, object]]] = []

            async def capture_result(topic, payload, *, retain=False):
                published.append((topic, payload))

            bridge._publish_json = capture_result  # type: ignore[method-assign]

            class Messages:
                def __init__(self):
                    self.index = 0

                def __aiter__(self):
                    return self

                async def __anext__(self):
                    self.index += 1
                    if self.index == 1:
                        return SimpleNamespace(
                            topic="test/call/set",
                            payload=json.dumps(
                                {
                                    "recipient": "+48535000111",
                                    "ring_time_s": 1,
                                }
                            ).encode(),
                        )
                    if self.index == 2:
                        await asyncio.wait_for(client.dial_seen.wait(), timeout=1)
                        return SimpleNamespace(topic="test/cancel/set", payload=b"{}")
                    raise StopAsyncIteration

            mqtt_client = SimpleNamespace(messages=Messages())
            await asyncio.wait_for(bridge._command_loop(mqtt_client), timeout=2)
            await asyncio.sleep(0)

            call_services = [name for name, _ in client.calls]
            self.assertIn("dial", call_services)
            self.assertEqual(call_services.count("disconnect"), 1)
            self.assertFalse(bridge._command_tasks)
            cancel_results = [
                payload for topic, payload in published if topic == "test/result/cancel"
            ]
            self.assertEqual(cancel_results[-1]["status"], "success")
            self.assertTrue(cancel_results[-1]["disconnect_sent"])
            await store.close()

    async def test_cancelled_call_task_performs_disconnect_cleanup(self) -> None:
        gateway = GatewayService(make_config(retry_forever=True))
        client = FakeClient()
        gateway._client = client
        gateway.user_services = {
            "dial": service("dial", "recipient"),
            "disconnect": service("disconnect"),
        }
        task = asyncio.create_task(
            gateway.async_call_batch(recipients=["+48535000111"], ring_time_s=1)
        )
        await asyncio.wait_for(client.dial_seen.wait(), timeout=1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual([name for name, _ in client.calls].count("disconnect"), 1)
        self.assertIsNone(gateway._active_job_kind)

    async def test_inbound_info_log_contains_no_phone_or_message(self) -> None:
        gateway = GatewayService(make_config())
        with self.assertLogs("qtronic_gateway.gateway", level="INFO") as captured:
            gateway._log_event_summary(
                {
                    "type": "sms_received",
                    "sender": "+48535000111",
                    "message": "sekretna wiadomosc",
                }
            )
        output = "\n".join(captured.output)
        self.assertNotIn("+48535000111", output)
        self.assertNotIn("sekretna wiadomosc", output)
        self.assertIn("0111", output)


if __name__ == "__main__":
    unittest.main()
