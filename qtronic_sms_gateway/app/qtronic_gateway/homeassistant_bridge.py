"""Bridge selected gateway events to the Home Assistant event bus."""

from __future__ import annotations

import asyncio
import json
import logging
import os
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
        self._base_url = os.environ.get("QTRONIC_HA_API", "http://supervisor/core/api").rstrip("/")
        self._remove_listener = gateway.subscribe(self._on_gateway_event)
        self._enabled = bool(self._token)

    async def start(self) -> None:
        if not self._enabled:
            _LOGGER.warning(
                "Home Assistant event bridge is disabled because SUPERVISOR_TOKEN is missing"
            )
            return
        _LOGGER.info("Home Assistant event bridge is enabled")

    async def stop(self) -> None:
        self._remove_listener()

    def _on_gateway_event(self, event: dict[str, object]) -> None:
        ha_event_type = EVENT_MAP.get(str(event.get("type")))
        if not self._enabled or ha_event_type is None:
            return
        asyncio.create_task(self._fire_event(ha_event_type, event))

    async def _fire_event(self, ha_event_type: str, payload: dict[str, object]) -> None:
        enriched_payload = {
            **payload,
            "addon_hostname": os.environ.get("HOSTNAME", "").strip(),
            "gateway_host": self.gateway.host,
        }
        try:
            await asyncio.to_thread(
                self._fire_event_sync, ha_event_type, enriched_payload
            )
        except Exception as err:  # pragma: no cover - runtime logging guard
            _LOGGER.warning("Failed to fire Home Assistant event %s: %s", ha_event_type, err)

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
        except urllib_error.HTTPError as err:  # pragma: no cover - runtime logging guard
            details = err.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"HTTP {err.code}: {details or err.reason}") from err
        except urllib_error.URLError as err:  # pragma: no cover - runtime logging guard
            raise RuntimeError(str(err.reason)) from err
