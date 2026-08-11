"""MQTT bridge for the Q-Tronic SMS Gateway add-on."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
import inspect
import json
import logging
import re
from typing import Any
from uuid import uuid4

from aiomqtt import Client, MqttError, Will

from .gateway import GatewayService
from .sms import normalize_encoding
from .validation import CallRequest, SmsSendRequest, UssdRequest, parse_ring_time

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
        self._command_tasks: dict[asyncio.Task[None], tuple[str, str | None]] = {}
        self._command_operations: dict[
            asyncio.Task[None], Awaitable[dict[str, Any]]
        ] = {}
        self._remove_listener = gateway.subscribe(self._on_gateway_event)
        self._controls: dict[str, str] = {
            "sms_targets": "",
            "sms_message": "",
            "sms_encoding": gateway.config.sms.default_encoding,
            "call_targets": "",
            "call_ring_time": str(gateway.config.calling.default_ring_time_s),
            "ussd_code": "",
        }

    @property
    def topic_prefix(self) -> str:
        return self.config.topic_prefix.rstrip("/")

    @property
    def discovery_prefix(self) -> str:
        return self.config.discovery_prefix.rstrip("/")

    def _topic(self, suffix: str) -> str:
        return f"{self.topic_prefix}/{suffix.lstrip('/')}"

    def _device(self) -> dict[str, Any]:
        identifiers = ["qtronic_sms_gateway_addon"]
        if self.gateway.gateway_uuid:
            identifiers.append(f"qtronic_sms_gateway_{self.gateway.gateway_uuid}")
        return {
            "identifiers": identifiers,
            "name": "Q-Tronic SMS Gateway",
            "manufacturer": "Q-Tronic",
            "model": "ESPHome GSM Gateway",
        }

    def _availability(self) -> list[dict[str, str]]:
        return [{"topic": self._topic("status")}]

    async def start(self) -> None:
        if not self.config.enabled:
            _LOGGER.info("MQTT bridge is disabled in add-on configuration")
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._runner())

    async def stop(self) -> None:
        self._stop_event.set()
        if any(kind == "call" for kind, _ in self._command_tasks.values()):
            try:
                await self.gateway.async_hangup()
            except Exception as err:
                _LOGGER.warning(
                    "Failed to hang up scheduled MQTT call during stop: %s", err
                )
            finally:
                await self._cancel_and_wait_scheduled_commands(kind="call")
        await self._stop_command_tasks()
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
            try:
                self._event_queue.get_nowait()
                self._event_queue.task_done()
            except asyncio.QueueEmpty:
                pass
            try:
                self._event_queue.put_nowait(event)
            except asyncio.QueueFull:
                _LOGGER.warning(
                    "MQTT event queue remained full; latest event was dropped"
                )

    async def _runner(self) -> None:
        while not self._stop_event.is_set():
            try:
                async with Client(
                    hostname=self.config.host,
                    port=self.config.port,
                    username=self.config.username,
                    password=self.config.password,
                    will=Will(
                        topic=self._topic("status"),
                        payload="offline",
                        qos=1,
                        retain=True,
                    ),
                ) as client:
                    self._client = client
                    _LOGGER.info(
                        "Connected to MQTT broker %s:%s",
                        self.config.host,
                        self.config.port,
                    )
                    await self._publish_availability(True)
                    await self._publish_discovery()
                    await self._publish_control_snapshot()
                    await self._publish_snapshot()
                    await self._subscribe_command_topics(client)

                    commands_task = asyncio.create_task(self._command_loop(client))
                    events_task = asyncio.create_task(self._event_loop(client))
                    bridge_tasks = {commands_task, events_task}
                    try:
                        done, pending = await asyncio.wait(
                            bridge_tasks,
                            return_when=asyncio.FIRST_EXCEPTION,
                        )
                    finally:
                        for task in bridge_tasks:
                            if not task.done():
                                task.cancel()
                        await asyncio.gather(*bridge_tasks, return_exceptions=True)
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
                if self._is_notify_sms_topic(topic):
                    recipient_id = self._extract_notify_recipient_id(topic)
                    _LOGGER.info(
                        "Received MQTT notify send request for recipient_id=%s on topic %s",
                        recipient_id,
                        topic,
                    )
                    notify_message = payload.get(
                        "message", payload.get("value", payload_text)
                    )
                    result = await self._send_sms_from_notify(
                        str(notify_message),
                        recipient_id=recipient_id,
                        idempotency_key=(
                            str(payload["idempotency_key"])
                            if payload.get("idempotency_key") is not None
                            else None
                        ),
                    )
                    await self._publish_json(self._topic("result/send_sms"), result)
                elif topic == self._topic("send_sms/set"):
                    _LOGGER.info("Received MQTT send_sms request on topic %s", topic)
                    command = SmsSendRequest.parse(
                        payload,
                        default_encoding=self.gateway.config.sms.default_encoding,
                    )
                    recipients = self.gateway.resolve_recipient_numbers(
                        **command.recipient_kwargs()
                    )
                    _, message_segments = command.segments(
                        unicode_available=self.gateway.can_send_unicode_sms,
                        max_segments=self.gateway.config.sms.max_segments,
                        split_long=self.gateway.config.sms.split_long,
                    )
                    result = await self.gateway.async_enqueue_sms_batch(
                        message=command.message,
                        recipients=recipients,
                        encoding=command.encoding,
                        idempotency_key=command.idempotency_key,
                        message_segments=message_segments,
                    )
                    await self._publish_json(self._topic("result/send_sms"), result)
                elif topic == self._topic("call/set"):
                    _LOGGER.info("Received MQTT call request on topic %s", topic)
                    command = CallRequest.parse(
                        payload,
                        default_ring_time_s=self.gateway.config.calling.default_ring_time_s,
                    )
                    recipients = self.gateway.resolve_recipient_numbers(
                        **command.recipient_kwargs()
                    )
                    batch_id = command.idempotency_key or uuid4().hex
                    self._schedule_command(
                        self.gateway.async_call_batch(
                            recipients=recipients,
                            ring_time_s=command.ring_time_s,
                            batch_id=batch_id,
                        ),
                        result_topic=self._topic("result/call"),
                        source_topic=topic,
                        kind="call",
                        job_id=batch_id,
                    )
                elif topic == self._topic("hangup/set"):
                    _LOGGER.info("Received MQTT hangup request on topic %s", topic)
                    try:
                        result = await self.gateway.async_hangup()
                    finally:
                        await self._cancel_and_wait_scheduled_commands(kind="call")
                    await self._publish_json(self._topic("result/hangup"), result)
                elif topic == self._topic("send_ussd/set"):
                    _LOGGER.info("Received MQTT USSD request on topic %s", topic)
                    command = UssdRequest.parse(
                        {
                            "code": payload.get(
                                "code", payload.get("value", payload_text)
                            )
                        }
                    )
                    self._schedule_command(
                        self.gateway.async_send_ussd(command.code),
                        result_topic=self._topic("result/send_ussd"),
                        source_topic=topic,
                        kind="ussd",
                    )
                elif topic == self._topic("cancel/set"):
                    _LOGGER.info("Received MQTT cancel request on topic %s", topic)
                    job_id = (
                        payload.get("job_id") if isinstance(payload, dict) else None
                    )
                    normalized_job_id = str(job_id).strip() if job_id else None
                    scheduled_tasks = self._matching_scheduled_commands(
                        job_id=normalized_job_id
                    )
                    scheduled_ids = self._scheduled_job_ids(scheduled_tasks)
                    scheduled_call = any(
                        self._command_tasks.get(task, (None, None))[0] == "call"
                        for task in scheduled_tasks
                    )
                    try:
                        try:
                            result = await self.gateway.async_cancel(normalized_job_id)
                        except RuntimeError:
                            if not scheduled_tasks:
                                raise
                            result = {
                                "status": "success",
                                "canceled": True,
                                "job_id": normalized_job_id,
                                "canceled_jobs": scheduled_ids,
                                "disconnect_sent": False,
                            }
                        if scheduled_call and not result.get("disconnect_sent"):
                            hangup_result = await self.gateway.async_hangup()
                            result["disconnect_sent"] = bool(
                                hangup_result.get("disconnect_sent")
                            )
                        result["canceled_jobs"] = sorted(
                            set(result.get("canceled_jobs", [])) | set(scheduled_ids)
                        )
                    finally:
                        await self._cancel_and_wait_scheduled_tasks(scheduled_tasks)
                    await self._publish_json(self._topic("result/cancel"), result)
                elif topic == self._topic("request_status"):
                    await self._publish_snapshot()
                elif topic == self._topic("control/sms_targets/set"):
                    await self._update_control(
                        "sms_targets", self._payload_value(payload, payload_text)
                    )
                elif topic == self._topic("control/sms_message/set"):
                    await self._update_control(
                        "sms_message", self._payload_value(payload, payload_text)
                    )
                elif topic == self._topic("control/sms_encoding/set"):
                    await self._update_control(
                        "sms_encoding",
                        normalize_encoding(
                            self._payload_value(payload, payload_text) or "auto"
                        ),
                    )
                elif topic == self._topic("control/call_targets/set"):
                    await self._update_control(
                        "call_targets", self._payload_value(payload, payload_text)
                    )
                elif topic == self._topic("control/call_ring_time/set"):
                    ring_time = parse_ring_time(
                        self._payload_value(payload, payload_text) or "1"
                    )
                    await self._update_control("call_ring_time", str(ring_time))
                elif topic == self._topic("control/ussd_code/set"):
                    code = UssdRequest.parse(
                        {"code": self._payload_value(payload, payload_text)}
                    ).code
                    await self._update_control("ussd_code", code)
                elif topic == self._topic("action/send_sms/press"):
                    result = await self._send_sms_from_controls()
                    await self._publish_json(self._topic("result/send_sms"), result)
                elif topic == self._topic("action/call/press"):
                    batch_id = uuid4().hex
                    self._schedule_command(
                        self._call_from_controls(batch_id=batch_id),
                        result_topic=self._topic("result/call"),
                        source_topic=topic,
                        kind="call",
                        job_id=batch_id,
                    )
                elif topic == self._topic("action/hangup/press"):
                    try:
                        result = await self.gateway.async_hangup()
                    finally:
                        await self._cancel_and_wait_scheduled_commands(kind="call")
                    await self._publish_json(self._topic("result/hangup"), result)
                elif topic == self._topic("action/send_ussd/press"):
                    self._schedule_command(
                        self._send_ussd_from_controls(),
                        result_topic=self._topic("result/send_ussd"),
                        source_topic=topic,
                        kind="ussd",
                    )
                elif self._is_saved_recipient_button(topic, "send_sms_to"):
                    recipient_id = self._extract_saved_recipient_id(
                        topic, "send_sms_to"
                    )
                    result = await self._send_sms_from_controls(
                        recipient_id=recipient_id
                    )
                    await self._publish_json(self._topic("result/send_sms"), result)
                elif self._is_saved_recipient_button(topic, "call_to"):
                    recipient_id = self._extract_saved_recipient_id(topic, "call_to")
                    batch_id = uuid4().hex
                    self._schedule_command(
                        self._call_from_controls(
                            recipient_id=recipient_id, batch_id=batch_id
                        ),
                        result_topic=self._topic("result/call"),
                        source_topic=topic,
                        kind="call",
                        job_id=batch_id,
                    )
            except Exception as err:
                _LOGGER.warning("MQTT command on %s failed: %s", topic, err)
                await self._publish_json(
                    self._topic("result/error"),
                    {"error": str(err), "source_topic": topic},
                )

    def _schedule_command(
        self,
        operation: Awaitable[dict[str, Any]],
        *,
        result_topic: str,
        source_topic: str,
        kind: str,
        job_id: str | None = None,
    ) -> None:
        if len(self._command_tasks) >= 100:
            if asyncio.iscoroutine(operation):
                operation.close()
            raise RuntimeError("Too many concurrent MQTT commands.")
        task = asyncio.create_task(
            self._run_scheduled_command(operation, result_topic, source_topic)
        )
        self._command_tasks[task] = (kind, job_id)
        self._command_operations[task] = operation
        task.add_done_callback(self._scheduled_command_done)

    async def _run_scheduled_command(
        self,
        operation: Awaitable[dict[str, Any]],
        result_topic: str,
        source_topic: str,
    ) -> None:
        try:
            result = await operation
            await self._publish_json(result_topic, result)
        except asyncio.CancelledError:
            raise
        except Exception as err:
            _LOGGER.warning(
                "Scheduled MQTT command on %s failed: %s", source_topic, err
            )
            await self._publish_json(
                self._topic("result/error"),
                {"error": str(err), "source_topic": source_topic},
            )

    def _scheduled_command_done(self, task: asyncio.Task[None]) -> None:
        self._command_tasks.pop(task, None)
        operation = self._command_operations.pop(task, None)
        if (
            task.cancelled()
            and inspect.iscoroutine(operation)
            and inspect.getcoroutinestate(operation) == inspect.CORO_CREATED
        ):
            operation.close()
        if not task.cancelled():
            task.exception()

    def _cancel_scheduled_commands(
        self, *, kind: str | None = None, job_id: str | None = None
    ) -> list[str]:
        tasks = self._matching_scheduled_commands(kind=kind, job_id=job_id)
        canceled = self._scheduled_job_ids(tasks)
        for task in tasks:
            task.cancel()
        return canceled

    def _matching_scheduled_commands(
        self, *, kind: str | None = None, job_id: str | None = None
    ) -> tuple[asyncio.Task[None], ...]:
        return tuple(
            task
            for task, (task_kind, task_job_id) in self._command_tasks.items()
            if (kind is None or task_kind == kind)
            and (job_id is None or task_job_id == job_id)
        )

    def _scheduled_job_ids(self, tasks: tuple[asyncio.Task[None], ...]) -> list[str]:
        return sorted(
            {
                task_job_id
                for task in tasks
                if (metadata := self._command_tasks.get(task)) is not None
                and (task_job_id := metadata[1])
            }
        )

    async def _cancel_and_wait_scheduled_commands(
        self, *, kind: str | None = None, job_id: str | None = None
    ) -> None:
        await self._cancel_and_wait_scheduled_tasks(
            self._matching_scheduled_commands(kind=kind, job_id=job_id)
        )

    @staticmethod
    async def _cancel_and_wait_scheduled_tasks(
        tasks: tuple[asyncio.Task[None], ...],
    ) -> None:
        if not tasks:
            return
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _stop_command_tasks(self) -> None:
        tasks = tuple(self._command_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _event_loop(self, client: Client) -> None:
        while True:
            event = await self._event_queue.get()
            try:
                event_type = str(event.get("type") or "unknown")
                if event_type == "availability":
                    await self._publish_availability(bool(event.get("available")))
                    await self._publish_component_status()
                elif event_type == "state_changed":
                    role = event.get("role")
                    await self._publish_state(role, event.get("value"))
                    if role in {"registered", "modem_online"}:
                        await self._publish_component_status()
                elif event_type in {"sms_received", "incoming_call"}:
                    await self._publish_json(self._topic(f"event/{event_type}"), event)
                elif (
                    event_type.startswith("sms_")
                    or event_type.startswith("call_")
                    or event_type == "call_hung_up"
                ):
                    await self._publish_json(self._topic(f"event/{event_type}"), event)
                    await self._publish_state(
                        "outbox_depth",
                        self.gateway.snapshot().get("persistent_outbox_depth", 0),
                    )
            finally:
                self._event_queue.task_done()

    async def _publish(self, topic: str, payload: str, *, retain: bool = False) -> None:
        if self._client is None:
            return
        await self._client.publish(topic, payload, retain=retain)

    async def _publish_json(
        self, topic: str, payload: dict[str, Any], *, retain: bool = False
    ) -> None:
        await self._publish(
            topic, json.dumps(payload, ensure_ascii=False), retain=retain
        )

    async def _publish_availability(self, available: bool) -> None:
        await self._publish(
            self._topic("status"), "online" if available else "offline", retain=True
        )

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
        await self._publish_component_status(snapshot)
        for role, value in snapshot["states"].items():
            await self._publish_state(role, value)
        await self._publish_state(
            "outbox_depth", snapshot.get("persistent_outbox_depth", 0)
        )

    async def _publish_component_status(
        self, snapshot: dict[str, Any] | None = None
    ) -> None:
        snapshot = snapshot or self.gateway.snapshot()
        component_status = snapshot.get("component_status", {})
        await self._publish(
            self._topic("state/esp_status"),
            str(component_status.get("esp", "unknown")),
            retain=True,
        )
        await self._publish(
            self._topic("state/sim800_status"),
            str(component_status.get("sim800", "unknown")),
            retain=True,
        )

    async def _publish_control_snapshot(self) -> None:
        for key in self._controls:
            await self._publish(
                self._topic(f"control/{key}/state"), self._controls[key], retain=True
            )

    async def _subscribe_command_topics(self, client: Client) -> None:
        topics = [
            self._topic("send_sms/set"),
            self._topic("call/set"),
            self._topic("hangup/set"),
            self._topic("send_ussd/set"),
            self._topic("cancel/set"),
            self._topic("notify/send_sms/+"),
            self._topic("request_status"),
            self._topic("control/sms_targets/set"),
            self._topic("control/sms_message/set"),
            self._topic("control/sms_encoding/set"),
            self._topic("control/call_targets/set"),
            self._topic("control/call_ring_time/set"),
            self._topic("control/ussd_code/set"),
            self._topic("action/send_sms/press"),
            self._topic("action/call/press"),
            self._topic("action/hangup/press"),
            self._topic("action/send_ussd/press"),
            self._topic("action/send_sms_to/+/press"),
            self._topic("action/call_to/+/press"),
        ]
        for topic in topics:
            await client.subscribe(topic)

    def _payload_value(self, payload: dict[str, Any], payload_text: str) -> str:
        value = payload.get("value", payload_text)
        return str(value or "").strip()

    async def _update_control(self, key: str, value: str) -> None:
        self._controls[key] = value
        await self._publish(self._topic(f"control/{key}/state"), value, retain=True)

    def _resolve_targets(self, raw_value: str) -> list[str]:
        return self.gateway.resolve_recipient_input(raw_value)

    async def _send_sms_from_controls(
        self, *, recipient_id: str | None = None
    ) -> dict[str, Any]:
        message = self._controls["sms_message"]
        if not message:
            raise RuntimeError("SMS message is empty.")
        if recipient_id:
            recipients = self.gateway.resolve_recipient_numbers(
                recipient_ids=[recipient_id]
            )
        else:
            recipients = self._resolve_targets(self._controls["sms_targets"])
        command = SmsSendRequest.parse(
            {
                "message": message,
                "recipients": recipients,
                "encoding": self._controls["sms_encoding"],
                "wait": False,
            },
            default_encoding=self.gateway.config.sms.default_encoding,
        )
        _, message_segments = command.segments(
            unicode_available=self.gateway.can_send_unicode_sms,
            max_segments=self.gateway.config.sms.max_segments,
            split_long=self.gateway.config.sms.split_long,
        )
        return await self.gateway.async_enqueue_sms_batch(
            message=message,
            recipients=recipients,
            encoding=self._controls["sms_encoding"],
            message_segments=message_segments,
        )

    async def _send_sms_from_notify(
        self,
        message: str,
        *,
        recipient_id: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if not message.strip():
            raise RuntimeError("SMS message is empty.")
        recipients = self.gateway.resolve_recipient_numbers(
            recipient_ids=[recipient_id]
        )
        command = SmsSendRequest.parse(
            {
                "message": message,
                "recipient_ids": [recipient_id],
                "encoding": self.gateway.config.sms.default_encoding,
                "wait": False,
                "idempotency_key": idempotency_key,
            },
            default_encoding=self.gateway.config.sms.default_encoding,
        )
        _, message_segments = command.segments(
            unicode_available=self.gateway.can_send_unicode_sms,
            max_segments=self.gateway.config.sms.max_segments,
            split_long=self.gateway.config.sms.split_long,
        )
        return await self.gateway.async_enqueue_sms_batch(
            message=message,
            recipients=recipients,
            encoding=self.gateway.config.sms.default_encoding,
            idempotency_key=command.idempotency_key,
            message_segments=message_segments,
        )

    async def _call_from_controls(
        self, *, recipient_id: str | None = None, batch_id: str | None = None
    ) -> dict[str, Any]:
        ring_time = parse_ring_time(self._controls["call_ring_time"] or "1")
        if recipient_id:
            recipients = self.gateway.resolve_recipient_numbers(
                recipient_ids=[recipient_id]
            )
        else:
            recipients = self._resolve_targets(self._controls["call_targets"])
        return await self.gateway.async_call_batch(
            recipients=recipients,
            ring_time_s=ring_time,
            batch_id=batch_id,
        )

    async def _send_ussd_from_controls(self) -> dict[str, Any]:
        code = UssdRequest.parse({"code": self._controls["ussd_code"]}).code
        return await self.gateway.async_send_ussd(code)

    def _is_saved_recipient_button(self, topic: str, action: str) -> bool:
        return bool(
            re.fullmatch(
                re.escape(self._topic(f"action/{action}/")) + r"[^/]+/press", topic
            )
        )

    def _extract_saved_recipient_id(self, topic: str, action: str) -> str:
        prefix = self._topic(f"action/{action}/")
        suffix = "/press"
        return topic[len(prefix) : -len(suffix)]

    def _is_notify_sms_topic(self, topic: str) -> bool:
        return bool(
            re.fullmatch(re.escape(self._topic("notify/send_sms/")) + r"[^/]+", topic)
        )

    def _extract_notify_recipient_id(self, topic: str) -> str:
        prefix = self._topic("notify/send_sms/")
        return topic[len(prefix) :]

    async def _publish_discovery(self) -> None:
        if not self.config.discovery_enabled:
            await self._sync_discovery_tombstones(set())
            return

        device = self._device()
        availability = self._availability()

        discovery_items = [
            (
                "sensor",
                "esp_status",
                {
                    "name": "Q-Tronic ESP Status",
                    "default_entity_id": "sensor.qtronic_sms_gateway_esp_status",
                    "state_topic": self._topic("state/esp_status"),
                    "icon": "mdi:chip",
                    "entity_category": "diagnostic",
                },
            ),
            (
                "sensor",
                "sim800_status",
                {
                    "name": "Q-Tronic SIM800C Status",
                    "default_entity_id": "sensor.qtronic_sms_gateway_sim800_status",
                    "state_topic": self._topic("state/sim800_status"),
                    "icon": "mdi:sim-alert",
                    "entity_category": "diagnostic",
                },
            ),
            (
                "sensor",
                "rssi",
                {
                    "name": "Q-Tronic RSSI",
                    "default_entity_id": "sensor.qtronic_sms_gateway_rssi",
                    "state_topic": self._topic("state/rssi"),
                    "unit_of_measurement": "dBm",
                    "icon": "mdi:signal",
                },
            ),
            (
                "binary_sensor",
                "modem_online",
                {
                    "name": "Q-Tronic Modem Online",
                    "default_entity_id": "binary_sensor.qtronic_sms_gateway_modem_online",
                    "state_topic": self._topic("state/modem_online"),
                    "payload_on": "true",
                    "payload_off": "false",
                    "device_class": "connectivity",
                    "icon": "mdi:modem",
                },
            ),
            (
                "binary_sensor",
                "registered",
                {
                    "name": "Q-Tronic Registered",
                    "default_entity_id": "binary_sensor.qtronic_sms_gateway_registered",
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
                    "default_entity_id": "sensor.qtronic_sms_gateway_sms_sender",
                    "state_topic": self._topic("state/sms_sender"),
                    "icon": "mdi:account-arrow-left",
                },
            ),
            (
                "sensor",
                "sms_message",
                {
                    "name": "Q-Tronic SMS Message",
                    "default_entity_id": "sensor.qtronic_sms_gateway_sms_message",
                    "state_topic": self._topic("state/sms_message"),
                    "icon": "mdi:message-text",
                },
            ),
            (
                "sensor",
                "incoming_call",
                {
                    "name": "Q-Tronic Incoming Call",
                    "default_entity_id": "sensor.qtronic_sms_gateway_incoming_call",
                    "state_topic": self._topic("state/incoming_call"),
                    "icon": "mdi:phone-incoming",
                },
            ),
            (
                "sensor",
                "call_state",
                {
                    "name": "Q-Tronic Call State",
                    "default_entity_id": "sensor.qtronic_sms_gateway_call_state",
                    "state_topic": self._topic("state/call_state"),
                    "icon": "mdi:phone",
                },
            ),
            (
                "sensor",
                "ussd",
                {
                    "name": "Q-Tronic USSD",
                    "default_entity_id": "sensor.qtronic_sms_gateway_ussd",
                    "state_topic": self._topic("state/ussd"),
                    "icon": "mdi:card-text",
                },
            ),
            (
                "sensor",
                "sms_status",
                {
                    "name": "Q-Tronic SMS Status",
                    "default_entity_id": "sensor.qtronic_sms_gateway_sms_status",
                    "state_topic": self._topic("state/sms_status"),
                    "icon": "mdi:message-processing-outline",
                    "entity_category": "diagnostic",
                },
            ),
            (
                "sensor",
                "sms_last_error",
                {
                    "name": "Q-Tronic SMS Last Error",
                    "default_entity_id": "sensor.qtronic_sms_gateway_sms_last_error",
                    "state_topic": self._topic("state/sms_last_error"),
                    "icon": "mdi:message-alert-outline",
                    "entity_category": "diagnostic",
                },
            ),
            (
                "sensor",
                "sms_queue_depth",
                {
                    "name": "Q-Tronic Firmware SMS Queue Depth",
                    "default_entity_id": "sensor.qtronic_sms_gateway_sms_queue_depth",
                    "state_topic": self._topic("state/sms_queue_depth"),
                    "icon": "mdi:tray-full",
                    "entity_category": "diagnostic",
                },
            ),
            (
                "sensor",
                "outbox_depth",
                {
                    "name": "Q-Tronic Persistent Outbox Depth",
                    "default_entity_id": "sensor.qtronic_sms_gateway_outbox_depth",
                    "state_topic": self._topic("state/outbox_depth"),
                    "icon": "mdi:database-clock-outline",
                    "entity_category": "diagnostic",
                },
            ),
            (
                "sensor",
                "sim800_state",
                {
                    "name": "Q-Tronic SIM800 State",
                    "default_entity_id": "sensor.qtronic_sms_gateway_sim800_state",
                    "state_topic": self._topic("state/sim800_state"),
                    "icon": "mdi:state-machine",
                    "entity_category": "diagnostic",
                },
            ),
            (
                "text",
                "sms_targets",
                {
                    "name": "Q-Tronic SMS Targets",
                    "default_entity_id": "text.qtronic_sms_gateway_sms_targets",
                    "state_topic": self._topic("control/sms_targets/state"),
                    "command_topic": self._topic("control/sms_targets/set"),
                    "icon": "mdi:account-multiple",
                },
            ),
            (
                "text",
                "sms_message_input",
                {
                    "name": "Q-Tronic SMS Message Input",
                    "default_entity_id": "text.qtronic_sms_gateway_sms_message_input",
                    "state_topic": self._topic("control/sms_message/state"),
                    "command_topic": self._topic("control/sms_message/set"),
                    "icon": "mdi:message-text-edit",
                },
            ),
            (
                "select",
                "sms_encoding",
                {
                    "name": "Q-Tronic SMS Encoding",
                    "default_entity_id": "select.qtronic_sms_gateway_sms_encoding",
                    "state_topic": self._topic("control/sms_encoding/state"),
                    "command_topic": self._topic("control/sms_encoding/set"),
                    "options": ["auto", "passthrough", "transliterate", "ucs2"],
                    "icon": "mdi:alphabetical-variant",
                },
            ),
            (
                "button",
                "send_sms",
                {
                    "name": "Q-Tronic Send SMS",
                    "default_entity_id": "button.qtronic_sms_gateway_send_sms",
                    "command_topic": self._topic("action/send_sms/press"),
                    "payload_press": "PRESS",
                    "icon": "mdi:send",
                },
            ),
            (
                "text",
                "call_targets",
                {
                    "name": "Q-Tronic Call Targets",
                    "default_entity_id": "text.qtronic_sms_gateway_call_targets",
                    "state_topic": self._topic("control/call_targets/state"),
                    "command_topic": self._topic("control/call_targets/set"),
                    "icon": "mdi:phone-outgoing",
                },
            ),
            (
                "number",
                "call_ring_time",
                {
                    "name": "Q-Tronic Call Ring Time",
                    "default_entity_id": "number.qtronic_sms_gateway_call_ring_time",
                    "state_topic": self._topic("control/call_ring_time/state"),
                    "command_topic": self._topic("control/call_ring_time/set"),
                    "min": 1,
                    "max": 3600,
                    "step": 1,
                    "mode": "box",
                    "icon": "mdi:timer-outline",
                },
            ),
            (
                "button",
                "call",
                {
                    "name": "Q-Tronic Call",
                    "default_entity_id": "button.qtronic_sms_gateway_call",
                    "command_topic": self._topic("action/call/press"),
                    "payload_press": "PRESS",
                    "icon": "mdi:phone",
                },
            ),
            (
                "button",
                "hangup",
                {
                    "name": "Q-Tronic Hang Up",
                    "default_entity_id": "button.qtronic_sms_gateway_hangup",
                    "command_topic": self._topic("action/hangup/press"),
                    "payload_press": "PRESS",
                    "icon": "mdi:phone-hangup",
                },
            ),
            (
                "text",
                "ussd_code",
                {
                    "name": "Q-Tronic USSD Code",
                    "default_entity_id": "text.qtronic_sms_gateway_ussd_code",
                    "state_topic": self._topic("control/ussd_code/state"),
                    "command_topic": self._topic("control/ussd_code/set"),
                    "icon": "mdi:dialpad",
                },
            ),
            (
                "button",
                "send_ussd",
                {
                    "name": "Q-Tronic Send USSD",
                    "default_entity_id": "button.qtronic_sms_gateway_send_ussd",
                    "command_topic": self._topic("action/send_ussd/press"),
                    "payload_press": "PRESS",
                    "icon": "mdi:card-text-outline",
                },
            ),
        ]

        for object_id, name in (
            ("sms_sent_count", "SMS Sent Count"),
            ("sms_failed_count", "SMS Failed Count"),
            ("sms_unknown_count", "SMS Unknown Count"),
            ("sim800_timeout_count", "SIM800 Timeout Count"),
            ("sim800_recovery_count", "SIM800 Recovery Count"),
            ("uart_rx_overflow_count", "UART RX Overflow Count"),
        ):
            discovery_items.append(
                (
                    "sensor",
                    object_id,
                    {
                        "name": f"Q-Tronic {name}",
                        "default_entity_id": f"sensor.qtronic_sms_gateway_{object_id}",
                        "state_topic": self._topic(f"state/{object_id}"),
                        "state_class": "total_increasing",
                        "icon": "mdi:counter",
                        "entity_category": "diagnostic",
                    },
                )
            )
        discovery_items.append(
            (
                "sensor",
                "sim800_last_response_age",
                {
                    "name": "Q-Tronic SIM800 Last Response Age",
                    "default_entity_id": "sensor.qtronic_sms_gateway_sim800_last_response_age",
                    "state_topic": self._topic("state/sim800_last_response_age"),
                    "unit_of_measurement": "s",
                    "device_class": "duration",
                    "state_class": "measurement",
                    "icon": "mdi:timer-sand",
                    "entity_category": "diagnostic",
                },
            )
        )

        for recipient in self.gateway.saved_recipients:
            discovery_items.extend(
                [
                    (
                        "button",
                        f"send_sms_to_{recipient.id}",
                        {
                            "name": f"Q-Tronic Send SMS to {recipient.name}",
                            "default_entity_id": (
                                f"button.qtronic_sms_gateway_send_sms_to_{recipient.id}"
                            ),
                            "command_topic": self._topic(
                                f"action/send_sms_to/{recipient.id}/press"
                            ),
                            "payload_press": "PRESS",
                            "icon": "mdi:message-arrow-right",
                        },
                    ),
                    (
                        "button",
                        f"call_to_{recipient.id}",
                        {
                            "name": f"Q-Tronic Call {recipient.name}",
                            "default_entity_id": f"button.qtronic_sms_gateway_call_{recipient.id}",
                            "command_topic": self._topic(
                                f"action/call_to/{recipient.id}/press"
                            ),
                            "payload_press": "PRESS",
                            "icon": "mdi:phone-forward",
                        },
                    ),
                    (
                        "notify",
                        f"sms_{recipient.id}",
                        {
                            "name": f"Q-Tronic SMS {recipient.name}",
                            "default_entity_id": f"notify.qtronic_sms_gateway_sms_{recipient.id}",
                            "command_topic": self._topic(
                                f"notify/send_sms/{recipient.id}"
                            ),
                            "icon": "mdi:message-text",
                        },
                    ),
                ]
            )

        _LOGGER.info(
            "Publishing MQTT discovery for %s base entities and %s saved recipients",
            len(discovery_items),
            len(self.gateway.saved_recipients),
        )
        current_topics: set[str] = set()
        for platform, object_id, payload in discovery_items:
            discovery_topic = f"{self.discovery_prefix}/{platform}/qtronic_sms_gateway/{object_id}/config"
            current_topics.add(discovery_topic)
            payload.update(
                {
                    "unique_id": f"qtronic_sms_gateway_{object_id}",
                    "device": device,
                }
            )
            if object_id not in {"esp_status", "sim800_status"}:
                payload["availability"] = availability
            _LOGGER.info("Publishing MQTT discovery topic %s", discovery_topic)
            await self._publish_json(discovery_topic, payload, retain=True)

        trigger_items = [
            (
                "sms_received",
                {
                    "automation_type": "trigger",
                    "platform": "device_automation",
                    "topic": self._topic("event/sms_received"),
                    "type": "sms_received",
                    "subtype": "message",
                    "device": device,
                },
            ),
            (
                "incoming_call",
                {
                    "automation_type": "trigger",
                    "platform": "device_automation",
                    "topic": self._topic("event/incoming_call"),
                    "type": "incoming_call",
                    "subtype": "phone",
                    "device": device,
                },
            ),
        ]

        for object_id, payload in trigger_items:
            discovery_topic = f"{self.discovery_prefix}/device_automation/qtronic_sms_gateway/{object_id}/config"
            current_topics.add(discovery_topic)
            _LOGGER.info("Publishing MQTT discovery topic %s", discovery_topic)
            await self._publish_json(discovery_topic, payload, retain=True)

        await self._sync_discovery_tombstones(current_topics)

    async def _sync_discovery_tombstones(self, current_topics: set[str]) -> None:
        """Remove retained discovery configs which disappeared since the last run."""
        previous_raw = await self.gateway.store.metadata_get("mqtt_discovery_topics")
        try:
            previous = set(json.loads(previous_raw)) if previous_raw else set()
        except (TypeError, json.JSONDecodeError):
            previous = set()
        for stale_topic in sorted(previous - current_topics):
            _LOGGER.info("Publishing MQTT discovery tombstone %s", stale_topic)
            await self._publish(stale_topic, "", retain=True)
        await self.gateway.store.metadata_set(
            "mqtt_discovery_topics", json.dumps(sorted(current_topics))
        )
