"""MQTT bridge for the Q-Tronic SMS Gateway add-on."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from aiomqtt import Client, MqttError

from .gateway import GatewayService

_LOGGER = logging.getLogger(__name__)


class MQTTBridge:
    """Publish gateway state to MQTT and accept MQTT commands."""

    def __init__(self, gateway: GatewayService) -> None:
        self.gateway = gateway
        self.config = gateway.config.mqtt
        self._task: asyncio.Task | None = None
        self._client: Client | None = None
        self._stop_event = asyncio.Event()
        self._event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=200)
        self._remove_listener = gateway.subscribe(self._on_gateway_event)

    @property
    def topic_prefix(self) -> str:
        return self.config.topic_prefix.rstrip("/")

    @property
    def discovery_prefix(self) -> str:
        return self.config.discovery_prefix.rstrip("/")

    def _topic(self, suffix: str) -> str:
        return f"{self.topic_prefix}/{suffix.lstrip('/')}"

    async def start(self) -> None:
        if not self.config.enabled:
            _LOGGER.info("MQTT bridge is disabled in add-on configuration")
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._runner())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._remove_listener()

    def _on_gateway_event(self, event: dict[str, Any]) -> None:
        if not self.config.enabled:
            return
        try:
            self._event_queue.put_nowait(event)
        except asyncio.QueueFull:
            _LOGGER.warning("MQTT event queue is full, dropping oldest event")

    async def _runner(self) -> None:
        while not self._stop_event.is_set():
            try:
                async with Client(
                    hostname=self.config.host,
                    port=self.config.port,
                    username=self.config.username,
                    password=self.config.password,
                ) as client:
                    self._client = client
                    _LOGGER.info("Connected to MQTT broker %s:%s", self.config.host, self.config.port)
                    await self._publish_availability(True)
                    await self._publish_discovery()
                    await self._publish_snapshot()
                    await client.subscribe(self._topic("send_sms/set"))
                    await client.subscribe(self._topic("call/set"))
                    await client.subscribe(self._topic("hangup/set"))
                    await client.subscribe(self._topic("request_status"))

                    commands_task = asyncio.create_task(self._command_loop(client))
                    events_task = asyncio.create_task(self._event_loop(client))
                    done, pending = await asyncio.wait(
                        {commands_task, events_task},
                        return_when=asyncio.FIRST_EXCEPTION,
                    )
                    for task in pending:
                        task.cancel()
                    for task in done:
                        exc = task.exception()
                        if exc is not None:
                            raise exc
            except asyncio.CancelledError:
                break
            except MqttError as err:
                _LOGGER.warning("MQTT bridge error: %s", err)
            except Exception as err:  # pragma: no cover - defensive runtime logging
                _LOGGER.exception("Unexpected MQTT bridge failure: %s", err)
            finally:
                if self._client is not None:
                    try:
                        await self._publish_availability(False)
                    except Exception:
                        pass
                self._client = None

            if not self._stop_event.is_set():
                await asyncio.sleep(5)

    async def _command_loop(self, client: Client) -> None:
        async for message in client.messages:
            topic = str(message.topic)
            payload_text = message.payload.decode("utf-8", errors="ignore")
            try:
                payload = json.loads(payload_text) if payload_text else {}
            except json.JSONDecodeError:
                payload = {"value": payload_text}

            try:
                if topic == self._topic("send_sms/set"):
                    recipients = self.gateway.resolve_recipient_numbers(
                        recipient=payload.get("recipient"),
                        recipient_id=payload.get("recipient_id"),
                        recipients=payload.get("recipients"),
                        recipient_ids=payload.get("recipient_ids"),
                    )
                    result = await self.gateway.async_send_sms_batch(
                        message=str(payload.get("message", "")),
                        recipients=recipients,
                        encoding=payload.get("encoding"),
                    )
                    await self._publish_json(self._topic("result/send_sms"), result)
                elif topic == self._topic("call/set"):
                    recipients = self.gateway.resolve_recipient_numbers(
                        recipient=payload.get("recipient"),
                        recipient_id=payload.get("recipient_id"),
                        recipients=payload.get("recipients"),
                        recipient_ids=payload.get("recipient_ids"),
                    )
                    result = await self.gateway.async_call_batch(
                        recipients=recipients,
                        ring_time_s=payload.get("ring_time_s"),
                    )
                    await self._publish_json(self._topic("result/call"), result)
                elif topic == self._topic("hangup/set"):
                    result = await self.gateway.async_hangup()
                    await self._publish_json(self._topic("result/hangup"), result)
                elif topic == self._topic("request_status"):
                    await self._publish_snapshot()
            except Exception as err:
                await self._publish_json(
                    self._topic("result/error"),
                    {"error": str(err), "source_topic": topic},
                )

    async def _event_loop(self, client: Client) -> None:
        while True:
            event = await self._event_queue.get()
            event_type = event.get("type")
            if event_type == "availability":
                await self._publish_availability(bool(event.get("available")))
            elif event_type == "state_changed":
                role = event.get("role")
                await self._publish_state(role, event.get("value"))
            elif event_type == "sms_received":
                await self._publish_json(self._topic("event/sms_received"), event)
            elif event_type == "incoming_call":
                await self._publish_json(self._topic("event/incoming_call"), event)
            elif event_type.endswith("_finished") or event_type.endswith("_started") or event_type == "call_hung_up":
                await self._publish_json(self._topic(f"event/{event_type}"), event)

    async def _publish(self, topic: str, payload: str, *, retain: bool = False) -> None:
        if self._client is None:
            return
        await self._client.publish(topic, payload, retain=retain)

    async def _publish_json(self, topic: str, payload: dict[str, Any], *, retain: bool = False) -> None:
        await self._publish(topic, json.dumps(payload, ensure_ascii=False), retain=retain)

    async def _publish_availability(self, available: bool) -> None:
        await self._publish(self._topic("status"), "online" if available else "offline", retain=True)

    async def _publish_state(self, role: str | None, value: Any) -> None:
        if role is None:
            return
        if isinstance(value, bool):
            payload = "true" if value else "false"
        elif value is None:
            payload = ""
        else:
            payload = str(value)
        await self._publish(self._topic(f"state/{role}"), payload, retain=True)

    async def _publish_snapshot(self) -> None:
        snapshot = self.gateway.snapshot()
        await self._publish_json(self._topic("snapshot"), snapshot, retain=False)
        await self._publish_availability(snapshot["available"])
        for role, value in snapshot["states"].items():
            await self._publish_state(role, value)

    async def _publish_discovery(self) -> None:
        if not self.config.discovery_enabled:
            return

        device = {
            "identifiers": ["qtronic_sms_gateway_addon"],
            "name": "Q-Tronic SMS Gateway",
            "manufacturer": "Q-Tronic",
            "model": "ESPHome GSM Gateway",
        }

        discovery_items = [
            (
                "sensor",
                "rssi",
                {
                    "name": "Q-Tronic RSSI",
                    "state_topic": self._topic("state/rssi"),
                    "unit_of_measurement": "dBm",
                    "icon": "mdi:signal",
                },
            ),
            (
                "binary_sensor",
                "registered",
                {
                    "name": "Q-Tronic Registered",
                    "state_topic": self._topic("state/registered"),
                    "payload_on": "true",
                    "payload_off": "false",
                    "icon": "mdi:sim",
                },
            ),
            (
                "sensor",
                "sms_sender",
                {
                    "name": "Q-Tronic SMS Sender",
                    "state_topic": self._topic("state/sms_sender"),
                    "icon": "mdi:account-arrow-left",
                },
            ),
            (
                "sensor",
                "sms_message",
                {
                    "name": "Q-Tronic SMS Message",
                    "state_topic": self._topic("state/sms_message"),
                    "icon": "mdi:message-text",
                },
            ),
            (
                "sensor",
                "incoming_call",
                {
                    "name": "Q-Tronic Incoming Call",
                    "state_topic": self._topic("state/incoming_call"),
                    "icon": "mdi:phone-incoming",
                },
            ),
            (
                "sensor",
                "call_state",
                {
                    "name": "Q-Tronic Call State",
                    "state_topic": self._topic("state/call_state"),
                    "icon": "mdi:phone",
                },
            ),
            (
                "sensor",
                "ussd",
                {
                    "name": "Q-Tronic USSD",
                    "state_topic": self._topic("state/ussd"),
                    "icon": "mdi:card-text",
                },
            ),
        ]

        availability = [{"topic": self._topic("status")}]
        for platform, object_id, payload in discovery_items:
            discovery_topic = (
                f"{self.discovery_prefix}/{platform}/qtronic_sms_gateway/{object_id}/config"
            )
            payload.update(
                {
                    "unique_id": f"qtronic_sms_gateway_{object_id}",
                    "availability": availability,
                    "device": device,
                }
            )
            await self._publish_json(discovery_topic, payload, retain=True)
