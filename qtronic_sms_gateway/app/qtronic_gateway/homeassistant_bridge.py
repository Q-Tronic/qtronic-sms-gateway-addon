"""Bridge selected gateway events to the Home Assistant event bus."""

from __future__ import annotations

import asyncio
import json
import hashlib
import logging
import os
from time import time
from urllib import error as urllib_error
from urllib import request as urllib_request

from .gateway import GatewayService

_LOGGER = logging.getLogger(__name__)

EVENT_MAP: dict[str, str] = {
    "sms_received": "qtronic_sms_gateway_sms_received",
    "incoming_call": "qtronic_sms_gateway_incoming_call",
    "sms_sent": "qtronic_sms_gateway_sms_sent",
    "sms_batch_finished": "qtronic_sms_gateway_sms_batch_finished",
    "call_batch_finished": "qtronic_sms_gateway_call_batch_finished",
    "call_hung_up": "qtronic_sms_gateway_call_hung_up",
}


class HomeAssistantEventBridge:
    """Forward gateway events into the Home Assistant core event bus."""

    def __init__(self, gateway: GatewayService) -> None:
        self.gateway = gateway
        self._token = os.environ.get("SUPERVISOR_TOKEN", "").strip()
        self._base_url = os.environ.get(
            "QTRONIC_HA_API", "http://supervisor/core/api"
        ).rstrip("/")
        self._remove_listener = gateway.subscribe(self._on_gateway_event)
        self._enabled = bool(self._token)
        self._task: asyncio.Task[None] | None = None
        self._wakeup = asyncio.Event()
        self._enqueue_tasks: set[asyncio.Task[None]] = set()

    async def start(self) -> None:
        if not self._enabled:
            _LOGGER.warning(
                "Home Assistant event bridge is disabled because SUPERVISOR_TOKEN is missing"
            )
            return
        _LOGGER.info("Home Assistant event bridge is enabled")
        self._task = asyncio.create_task(
            self._delivery_worker(), name="qtronic-ha-event-outbox"
        )
        self._wakeup.set()

    async def stop(self) -> None:
        self._remove_listener()
        if self._enqueue_tasks:
            await asyncio.gather(*tuple(self._enqueue_tasks), return_exceptions=True)
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def _on_gateway_event(self, event: dict[str, object]) -> None:
        ha_event_type = EVENT_MAP.get(str(event.get("type")))
        if not self._enabled or ha_event_type is None:
            return
        task = asyncio.create_task(self._enqueue_event(ha_event_type, dict(event)))
        self._enqueue_tasks.add(task)
        task.add_done_callback(self._enqueue_done)

    def _enqueue_done(self, task: asyncio.Task[None]) -> None:
        self._enqueue_tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            _LOGGER.warning("Failed to persist Home Assistant event: %s", error)

    async def _enqueue_event(
        self, ha_event_type: str, payload: dict[str, object]
    ) -> None:
        enriched_payload = {
            **payload,
            "addon_hostname": os.environ.get("HOSTNAME", "").strip(),
            "gateway_host": self.gateway.host,
            "gateway_uuid": self.gateway.gateway_uuid,
        }
        canonical = json.dumps(
            {"event_type": ha_event_type, "payload": enriched_payload},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        idempotency_key = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        _, created = await self.gateway.store.enqueue_outbox(
            kind="ha_event",
            payload={"event_type": ha_event_type, "payload": enriched_payload},
            max_attempts=10,
            idempotency_key=idempotency_key,
        )
        if created:
            await self.gateway.async_refresh_outbox_depth()
            self._wakeup.set()

    async def _delivery_worker(self) -> None:
        while True:
            row = await self.gateway.store.claim_next_outbox(kind="ha_event")
            if row is None:
                self._wakeup.clear()
                try:
                    await asyncio.wait_for(self._wakeup.wait(), timeout=1.0)
                except TimeoutError:
                    pass
                continue
            job_id = row["job_id"]
            payload = row["payload"]
            try:
                await self._fire_event(
                    str(payload["event_type"]), dict(payload.get("payload") or {})
                )
                await self.gateway.store.update_outbox(
                    job_id,
                    "sent",
                    result={"status": "sent", "delivered_at": time()},
                )
            except Exception as err:
                current = await self.gateway.store.get_outbox(job_id)
                if (
                    current is not None
                    and current["attempts"] < current["max_attempts"]
                ):
                    delay = min(300, 2 ** max(0, current["attempts"] - 1))
                    await self.gateway.store.update_outbox(
                        job_id,
                        "retry",
                        last_error=str(err),
                        next_attempt_at=time() + delay,
                    )
                    self._wakeup.set()
                else:
                    await self.gateway.store.update_outbox(
                        job_id,
                        "failed",
                        last_error=str(err),
                        result={"status": "failed"},
                    )
            finally:
                await self.gateway.async_refresh_outbox_depth()

    async def _fire_event(self, ha_event_type: str, payload: dict[str, object]) -> None:
        await asyncio.to_thread(self._fire_event_sync, ha_event_type, payload)

    def _fire_event_sync(self, ha_event_type: str, payload: dict[str, object]) -> None:
        url = f"{self._base_url}/events/{ha_event_type}"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib_request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib_request.urlopen(request, timeout=10) as response:
                response.read()
        except (
            urllib_error.HTTPError
        ) as err:  # pragma: no cover - runtime logging guard
            details = err.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"HTTP {err.code}: {details or err.reason}") from err
        except urllib_error.URLError as err:  # pragma: no cover - runtime logging guard
            raise RuntimeError(str(err.reason)) from err
