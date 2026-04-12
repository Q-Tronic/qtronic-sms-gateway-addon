"""ESPHome gateway service for the Q-Tronic SMS Gateway add-on."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
import logging
import re
from time import time
from typing import Any
from uuid import uuid4

from aioesphomeapi import (
    APIClient,
    InvalidAuthAPIError,
    InvalidEncryptionKeyAPIError,
    ReconnectLogic,
    RequiresEncryptionAPIError,
)
from aioesphomeapi.model import (
    BinarySensorInfo,
    BinarySensorState,
    DeviceInfo,
    EntityInfo,
    EntityState,
    SensorInfo,
    SensorState,
    TextSensorInfo,
    TextSensorState,
    UserService,
)

from .config import AddonConfig
from .recipients import (
    SavedRecipient,
    deduplicate_phone_numbers,
    normalize_phone_number_loose,
    phone_match_key,
    phone_numbers_match,
)
from .sms import (
    ENCODING_AUTO,
    ENCODING_PASSTHROUGH,
    ENCODING_TRANSLITERATE,
    ENCODING_UCS2,
    encode_sms_ucs2,
    normalize_encoding,
    normalize_inbound_text,
    resolve_auto_encoding,
    transliterate_sms_text,
)

_LOGGER = logging.getLogger(__name__)

AUTH_ERRORS = (
    RequiresEncryptionAPIError,
    InvalidEncryptionKeyAPIError,
    InvalidAuthAPIError,
)

ROLE_RSSI = "rssi"
ROLE_REGISTERED = "registered"
ROLE_SMS_SENDER = "sms_sender"
ROLE_SMS_MESSAGE = "sms_message"
ROLE_INCOMING_CALL = "incoming_call"
ROLE_CALL_STATE = "call_state"
ROLE_USSD = "ussd"

AUTO_DETECT_OBJECT_IDS: dict[str, tuple[str, ...]] = {
    ROLE_RSSI: ("rssi", "signal", "signal_strength"),
    ROLE_REGISTERED: ("registered", "network_registered"),
    ROLE_SMS_SENDER: ("sms_sender", "sender"),
    ROLE_SMS_MESSAGE: ("sms_message", "message", "sms"),
    ROLE_INCOMING_CALL: ("incoming_call", "caller_id", "call"),
    ROLE_CALL_STATE: ("call_state", "gsm_call_state", "sim800_call_state"),
    ROLE_USSD: ("ussd", "ussd_message"),
}


def normalize_object_id(value: str | None) -> str | None:
    """Normalize an ESPHome object ID or entity name."""
    if value is None:
        return None
    normalized = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    return normalized or None


def state_as_float(state: EntityState | None) -> float | None:
    if not isinstance(state, SensorState) or state.missing_state:
        return None
    return float(state.state)


def state_as_bool(state: EntityState | None) -> bool | None:
    if not isinstance(state, BinarySensorState) or state.missing_state:
        return None
    return bool(state.state)


def state_as_text(state: EntityState | None) -> str | None:
    if not isinstance(state, TextSensorState) or state.missing_state:
        return None
    return state.state


def state_as_value(state: EntityState | None) -> Any:
    if isinstance(state, TextSensorState) and not state.missing_state:
        return state.state
    if isinstance(state, BinarySensorState) and not state.missing_state:
        return bool(state.state)
    if isinstance(state, SensorState) and not state.missing_state:
        return float(state.state)
    return None


@dataclass(frozen=True, slots=True)
class SmsBatchDiagnostics:
    status: str = "idle"
    batch_id: str | None = None
    started_at: float | None = None
    finished_at: float | None = None
    recipients: tuple[str, ...] = ()
    completed_recipients: tuple[str, ...] = ()
    failed_recipient: str | None = None
    last_error: str | None = None
    encoding: str | None = None
    message_length: int = 0


@dataclass(frozen=True, slots=True)
class CallBatchDiagnostics:
    status: str = "idle"
    batch_id: str | None = None
    started_at: float | None = None
    finished_at: float | None = None
    recipients: tuple[str, ...] = ()
    completed_recipients: tuple[str, ...] = ()
    failed_recipients: tuple[str, ...] = ()
    unknown_recipients: tuple[str, ...] = ()
    failed_recipient: str | None = None
    last_error: str | None = None
    ring_time_s: int = 0
    attempts: tuple[tuple[str, int], ...] = ()


class GatewayService:
    """Bridge between ESPHome Native API and add-on APIs."""

    def __init__(self, config: AddonConfig) -> None:
        self.config = config
        self.available = False
        self.device: DeviceInfo | None = None
        self.entity_infos: dict[int, EntityInfo] = {}
        self.states: dict[int, EntityState] = {}
        self.roles: dict[str, int] = {}
        self.user_services: dict[str, UserService] = {}
        self._client: APIClient | None = None
        self._reconnect_logic: ReconnectLogic | None = None
        self._listeners: set[Callable[[dict[str, Any]], Awaitable[None] | None]] = set()
        self._state_event = asyncio.Event()
        self._state_version = 0
        self._send_lock = asyncio.Lock()
        self._queued_job_count = 0
        self._active_job_kind: str | None = None
        self._active_job_id: str | None = None
        self._warmup_until = 0.0
        self._recent_events: deque[dict[str, Any]] = deque(maxlen=100)
        self._started_at = time()
        self._last_connect_error: str | None = None
        self._last_sms_batch = SmsBatchDiagnostics()
        self._last_call_batch = CallBatchDiagnostics()

    @property
    def host(self) -> str:
        return self.config.esphome.host

    @property
    def port(self) -> int:
        return self.config.esphome.port

    @property
    def saved_recipients(self) -> tuple[SavedRecipient, ...]:
        return self.config.recipients

    @property
    def last_sms_batch(self) -> SmsBatchDiagnostics:
        return self._last_sms_batch

    @property
    def last_call_batch(self) -> CallBatchDiagnostics:
        return self._last_call_batch

    @property
    def send_sms_action(self) -> str:
        return self.config.esphome.send_sms_action

    @property
    def unicode_send_sms_action(self) -> str:
        return self.config.esphome.unicode_send_sms_action

    @property
    def dial_action(self) -> str:
        return self.config.esphome.dial_action

    @property
    def disconnect_action(self) -> str:
        return self.config.esphome.disconnect_action

    def _service_supports_sms(self, service_name: str) -> bool:
        service = self.user_services.get(service_name)
        if service is None:
            return False
        arg_names = {arg.name for arg in service.args}
        return {"recipient", "message"}.issubset(arg_names)

    def _service_supports_recipient_only(self, service_name: str) -> bool:
        service = self.user_services.get(service_name)
        if service is None:
            return False
        arg_names = {arg.name for arg in service.args}
        return {"recipient"}.issubset(arg_names)

    def _service_exists(self, service_name: str) -> bool:
        return service_name in self.user_services

    @property
    def can_send_sms(self) -> bool:
        return self._service_supports_sms(self.send_sms_action)

    @property
    def can_send_unicode_sms(self) -> bool:
        return self._service_supports_sms(self.unicode_send_sms_action)

    @property
    def can_place_calls(self) -> bool:
        return self._service_supports_recipient_only(self.dial_action) and self._service_exists(
            self.disconnect_action
        )

    @property
    def has_call_state_tracking(self) -> bool:
        return self.entity_info_for_role(ROLE_CALL_STATE) is not None

    def subscribe(
        self, listener: Callable[[dict[str, Any]], Awaitable[None] | None]
    ) -> Callable[[], None]:
        """Subscribe to gateway events."""
        self._listeners.add(listener)

        def _remove() -> None:
            self._listeners.discard(listener)

        return _remove

    def _dispatch_event(self, event_type: str, payload: dict[str, Any], *, store: bool = True) -> None:
        event = {
            "type": event_type,
            "timestamp": time(),
            **payload,
        }
        if store:
            self._recent_events.appendleft(event)
        for listener in tuple(self._listeners):
            result = listener(event)
            if asyncio.iscoroutine(result):
                asyncio.create_task(result)

    def events_snapshot(self) -> list[dict[str, Any]]:
        return list(self._recent_events)

    def snapshot(self) -> dict[str, Any]:
        """Return a summary used by REST, web UI, and MQTT."""
        role_values: dict[str, Any] = {}
        for role in (
            ROLE_RSSI,
            ROLE_REGISTERED,
            ROLE_SMS_SENDER,
            ROLE_SMS_MESSAGE,
            ROLE_INCOMING_CALL,
            ROLE_CALL_STATE,
            ROLE_USSD,
        ):
            role_values[role] = state_as_value(self.state_for_role(role))

        return {
            "available": self.available,
            "host": self.host,
            "port": self.port,
            "device": {
                "name": self.device.name if self.device else None,
                "model": self.device.model if self.device else None,
                "manufacturer": self.device.manufacturer if self.device else None,
                "esphome_version": self.device.esphome_version if self.device else None,
            },
            "queue_depth": self._queued_job_count,
            "active_job_kind": self._active_job_kind,
            "active_job_id": self._active_job_id,
            "last_connect_error": self._last_connect_error,
            "started_at": self._started_at,
            "services": {
                "send_sms": self.can_send_sms,
                "send_sms_unicode": self.can_send_unicode_sms,
                "call": self.can_place_calls,
            },
            "states": role_values,
            "saved_recipients": [
                {
                    "id": recipient.id,
                    "name": recipient.name,
                    "phone": recipient.phone,
                    "masked_phone": recipient.masked_phone,
                }
                for recipient in self.saved_recipients
            ],
            "last_sms_batch": {
                "status": self.last_sms_batch.status,
                "batch_id": self.last_sms_batch.batch_id,
                "started_at": self.last_sms_batch.started_at,
                "finished_at": self.last_sms_batch.finished_at,
                "recipients": list(self.last_sms_batch.recipients),
                "completed_recipients": list(self.last_sms_batch.completed_recipients),
                "failed_recipient": self.last_sms_batch.failed_recipient,
                "last_error": self.last_sms_batch.last_error,
                "encoding": self.last_sms_batch.encoding,
                "message_length": self.last_sms_batch.message_length,
            },
            "last_call_batch": {
                "status": self.last_call_batch.status,
                "batch_id": self.last_call_batch.batch_id,
                "started_at": self.last_call_batch.started_at,
                "finished_at": self.last_call_batch.finished_at,
                "recipients": list(self.last_call_batch.recipients),
                "completed_recipients": list(self.last_call_batch.completed_recipients),
                "failed_recipients": list(self.last_call_batch.failed_recipients),
                "unknown_recipients": list(self.last_call_batch.unknown_recipients),
                "failed_recipient": self.last_call_batch.failed_recipient,
                "last_error": self.last_call_batch.last_error,
                "ring_time_s": self.last_call_batch.ring_time_s,
                "attempts": {name: count for name, count in self.last_call_batch.attempts},
            },
        }

    def entity_info_for_role(self, role: str) -> EntityInfo | None:
        key = self.roles.get(role)
        if key is None:
            return None
        return self.entity_infos.get(key)

    def state_for_role(self, role: str) -> EntityState | None:
        key = self.roles.get(role)
        if key is None:
            return None
        return self.states.get(key)

    def role_for_state_key(self, key: int) -> str | None:
        for role, role_key in self.roles.items():
            if role_key == key:
                return role
        return None

    def recipient_by_id(self, recipient_id: str) -> SavedRecipient | None:
        for recipient in self.saved_recipients:
            if recipient.id == recipient_id:
                return recipient
        return None

    def recipient_for_phone(self, phone: str) -> SavedRecipient | None:
        if not phone_match_key(phone):
            return None
        for recipient in self.saved_recipients:
            if phone_numbers_match(recipient.phone, phone):
                return recipient
        return None

    def resolve_recipient_numbers(
        self,
        *,
        recipient: str | None = None,
        recipient_id: str | None = None,
        recipients: list[str] | None = None,
        recipient_ids: list[str] | None = None,
    ) -> list[str]:
        numbers: list[str] = []
        if recipient:
            numbers.append(recipient)
        if recipient_id:
            saved = self.recipient_by_id(recipient_id)
            if saved is None:
                raise RuntimeError(f"Unknown recipient_id '{recipient_id}'")
            numbers.append(saved.phone)
        for phone in recipients or []:
            numbers.append(phone)
        for item_id in recipient_ids or []:
            saved = self.recipient_by_id(item_id)
            if saved is None:
                raise RuntimeError(f"Unknown recipient_id '{item_id}'")
            numbers.append(saved.phone)
        numbers = deduplicate_phone_numbers(numbers)
        if not numbers:
            raise RuntimeError("No recipients were resolved.")
        return numbers

    def describe_recipient(self, phone: str) -> str:
        normalized = normalize_phone_number_loose(phone)
        saved = self.recipient_for_phone(normalized)
        if saved is None:
            return normalized or phone
        return f"{saved.name} ({saved.masked_phone})"

    async def async_start(self) -> None:
        """Start reconnect logic without blocking add-on startup forever."""
        self._client = APIClient(
            self.host,
            self.port,
            None,
            noise_psk=self.config.esphome.encryption_key or None,
        )
        self._reconnect_logic = ReconnectLogic(
            client=self._client,
            on_connect=self._async_on_connect,
            on_disconnect=self._async_on_disconnect,
            on_connect_error=self._async_on_connect_error,
            name=self.host,
        )
        await self._reconnect_logic.start()

    async def async_stop(self) -> None:
        self.available = False
        self._dispatch_event("availability", {"available": False}, store=False)
        if self._reconnect_logic is not None:
            await self._reconnect_logic.stop()
            self._reconnect_logic = None
        if self._client is not None:
            await self._client.disconnect(force=True)
            self._client = None

    @asynccontextmanager
    async def _transport_job(self, kind: str, job_id: str):
        self._queued_job_count += 1
        self._dispatch_event(
            "queue_changed",
            {
                "queue_depth": self._queued_job_count,
                "active_job_kind": self._active_job_kind,
                "active_job_id": self._active_job_id,
            },
            store=False,
        )
        await self._send_lock.acquire()
        self._queued_job_count -= 1
        self._active_job_kind = kind
        self._active_job_id = job_id
        self._dispatch_event(
            "queue_changed",
            {
                "queue_depth": self._queued_job_count,
                "active_job_kind": self._active_job_kind,
                "active_job_id": self._active_job_id,
            },
            store=False,
        )
        try:
            yield
        finally:
            self._active_job_kind = None
            self._active_job_id = None
            self._send_lock.release()
            self._dispatch_event(
                "queue_changed",
                {
                    "queue_depth": self._queued_job_count,
                    "active_job_kind": self._active_job_kind,
                    "active_job_id": self._active_job_id,
                },
                store=False,
            )

    async def _execute_user_service(
        self, service_name: str, data: dict[str, Any] | None = None
    ) -> None:
        if self._client is None:
            raise RuntimeError("The gateway is currently unavailable.")
        service = self.user_services.get(service_name)
        if service is None:
            raise RuntimeError(f"ESPHome action '{service_name}' was not found on {self.host}.")
        await self._client.execute_service(service, data or {})

    async def _wait_for_call_connected(self, timeout_s: int) -> bool:
        deadline = asyncio.get_running_loop().time() + timeout_s
        while True:
            if (state_as_text(self.state_for_role(ROLE_CALL_STATE)) or "").lower().strip() == "connected":
                return True
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return (
                    (state_as_text(self.state_for_role(ROLE_CALL_STATE)) or "").lower().strip()
                    == "connected"
                )
            observed_version = self._state_version
            self._state_event.clear()
            try:
                await asyncio.wait_for(self._state_event.wait(), remaining)
            except TimeoutError:
                return (
                    (state_as_text(self.state_for_role(ROLE_CALL_STATE)) or "").lower().strip()
                    == "connected"
                )
            if self._state_version == observed_version:
                continue

    def _prepare_outgoing_sms(
        self,
        message: str,
        recipient: str,
        encoding: str | None = None,
    ) -> tuple[str, str, str, str]:
        mode = normalize_encoding(encoding or self.config.sms.default_encoding)
        if mode == ENCODING_AUTO:
            mode = resolve_auto_encoding(message, self.can_send_unicode_sms)

        service_name = self.send_sms_action
        outgoing_message = message
        target = recipient

        if mode == ENCODING_UCS2:
            if not self.can_send_unicode_sms:
                if not self.can_send_sms:
                    raise RuntimeError(
                        "Unicode SMS was requested, but neither the Unicode action nor the "
                        "standard SMS action is available."
                    )
                mode = ENCODING_TRANSLITERATE
            else:
                service_name = self.unicode_send_sms_action
                target = encode_sms_ucs2(target)
                outgoing_message = encode_sms_ucs2(message)
        if mode == ENCODING_TRANSLITERATE:
            if not self.can_send_sms:
                raise RuntimeError("The standard SMS action is not available.")
            outgoing_message = transliterate_sms_text(message)
        elif mode == ENCODING_PASSTHROUGH:
            if not self.can_send_sms:
                raise RuntimeError("The standard SMS action is not available.")
        elif mode not in (ENCODING_UCS2,):
            raise RuntimeError(f"Unsupported SMS encoding mode: {mode}")

        return service_name, target, outgoing_message, mode

    async def async_send_sms_batch(
        self,
        *,
        message: str,
        recipients: list[str],
        encoding: str | None = None,
        batch_id: str | None = None,
    ) -> dict[str, Any]:
        if not recipients:
            raise RuntimeError("No recipients were provided for the SMS batch.")
        batch_label = batch_id or uuid4().hex[:8]
        recipient_labels = tuple(self.describe_recipient(recipient) for recipient in recipients)
        batch_started = time()
        self._last_sms_batch = SmsBatchDiagnostics(
            status="in_progress",
            batch_id=batch_label,
            started_at=batch_started,
            recipients=recipient_labels,
            encoding=normalize_encoding(encoding or self.config.sms.default_encoding),
            message_length=len(message),
        )
        self._dispatch_event(
            "sms_batch_started",
            {
                "batch_id": batch_label,
                "recipients": list(recipient_labels),
                "encoding": self._last_sms_batch.encoding,
            },
        )

        completed: list[str] = []
        failed_recipient: str | None = None
        last_error: str | None = None

        async with self._transport_job("sms", batch_label):
            for index, recipient in enumerate(recipients):
                recipient_label = recipient_labels[index]
                try:
                    service_name, target, outgoing_message, resolved_mode = self._prepare_outgoing_sms(
                        message=message,
                        recipient=recipient,
                        encoding=encoding,
                    )
                    await self._execute_user_service(
                        service_name,
                        {"recipient": target, "message": outgoing_message},
                    )
                    completed.append(recipient_label)
                    self._dispatch_event(
                        "sms_sent",
                        {
                            "batch_id": batch_label,
                            "recipient": recipient,
                            "recipient_label": recipient_label,
                            "encoding": resolved_mode,
                        },
                    )
                except Exception as err:
                    failed_recipient = recipient_label
                    last_error = str(err)
                    break

                if index < len(recipients) - 1 and self.config.sms.send_delay_ms > 0:
                    await asyncio.sleep(self.config.sms.send_delay_ms / 1000)

        self._last_sms_batch = SmsBatchDiagnostics(
            status="failed" if failed_recipient else "success",
            batch_id=batch_label,
            started_at=batch_started,
            finished_at=time(),
            recipients=recipient_labels,
            completed_recipients=tuple(completed),
            failed_recipient=failed_recipient,
            last_error=last_error,
            encoding=self._last_sms_batch.encoding,
            message_length=len(message),
        )
        self._dispatch_event(
            "sms_batch_finished",
            {
                "batch_id": batch_label,
                "status": self._last_sms_batch.status,
                "completed_recipients": list(completed),
                "failed_recipient": failed_recipient,
                "last_error": last_error,
            },
        )
        if failed_recipient:
            raise RuntimeError(last_error or f"SMS failed for {failed_recipient}")
        return {
            "batch_id": batch_label,
            "status": "success",
            "completed_recipients": completed,
        }

    async def async_call_batch(
        self,
        *,
        recipients: list[str],
        ring_time_s: int | None = None,
        batch_id: str | None = None,
    ) -> dict[str, Any]:
        if not recipients:
            raise RuntimeError("No recipients were provided for the call batch.")
        if not self.can_place_calls:
            raise RuntimeError("Dial/disconnect ESPHome actions are not available.")

        batch_label = batch_id or uuid4().hex[:8]
        ring_time = max(1, int(ring_time_s or self.config.calling.default_ring_time_s))
        recipient_labels = tuple(self.describe_recipient(recipient) for recipient in recipients)
        batch_started = time()
        attempts: dict[str, int] = {label: 0 for label in recipient_labels}
        completed: list[str] = []
        failed: list[str] = []
        unknown: list[str] = []
        failed_recipient: str | None = None
        last_error: str | None = None

        self._last_call_batch = CallBatchDiagnostics(
            status="in_progress",
            batch_id=batch_label,
            started_at=batch_started,
            recipients=recipient_labels,
            ring_time_s=ring_time,
            attempts=tuple(attempts.items()),
        )
        self._dispatch_event(
            "call_batch_started",
            {
                "batch_id": batch_label,
                "recipients": list(recipient_labels),
                "ring_time_s": ring_time,
            },
        )

        async with self._transport_job("call", batch_label):
            for index, recipient in enumerate(recipients):
                recipient_label = recipient_labels[index]
                while True:
                    attempts[recipient_label] += 1
                    try:
                        outcome = await self._perform_single_call_attempt(
                            batch_id=batch_label,
                            recipient=recipient,
                            recipient_label=recipient_label,
                            ring_time_s=ring_time,
                            attempt=attempts[recipient_label],
                        )
                        last_error = None
                    except Exception as err:
                        outcome = "failed"
                        last_error = str(err)

                    if outcome == "connected":
                        completed.append(recipient_label)
                        break
                    if outcome == "unknown":
                        unknown.append(recipient_label)
                        break

                    last_error = last_error or (
                        f"Call to {recipient_label} did not connect within {ring_time}s."
                    )
                    retry_allowed = self.config.calling.retry_forever or (
                        attempts[recipient_label] <= self.config.calling.max_retries
                    )
                    if retry_allowed:
                        if self.config.calling.retry_delay_s > 0:
                            await asyncio.sleep(self.config.calling.retry_delay_s)
                        last_error = None
                        continue

                    failed_recipient = recipient_label
                    failed.append(recipient_label)
                    if self.config.calling.failure_action == "stop_batch":
                        break
                    break

                if failed_recipient and self.config.calling.failure_action == "stop_batch":
                    break
                if index < len(recipients) - 1 and self.config.calling.delay_between_calls_s > 0:
                    await asyncio.sleep(self.config.calling.delay_between_calls_s)

        final_status = "failed" if failed else "unknown" if unknown else "success"
        self._last_call_batch = CallBatchDiagnostics(
            status=final_status,
            batch_id=batch_label,
            started_at=batch_started,
            finished_at=time(),
            recipients=recipient_labels,
            completed_recipients=tuple(completed),
            failed_recipients=tuple(failed),
            unknown_recipients=tuple(unknown),
            failed_recipient=failed_recipient,
            last_error=last_error if failed else None,
            ring_time_s=ring_time,
            attempts=tuple(attempts.items()),
        )
        self._dispatch_event(
            "call_batch_finished",
            {
                "batch_id": batch_label,
                "status": final_status,
                "completed_recipients": list(completed),
                "failed_recipients": list(failed),
                "unknown_recipients": list(unknown),
                "failed_recipient": failed_recipient,
                "last_error": self._last_call_batch.last_error,
                "ring_time_s": ring_time,
            },
        )
        if failed and self.config.calling.failure_action == "stop_batch":
            raise RuntimeError(self._last_call_batch.last_error or "Call batch failed.")
        return {
            "batch_id": batch_label,
            "status": final_status,
            "completed_recipients": completed,
            "failed_recipients": failed,
            "unknown_recipients": unknown,
        }

    async def async_hangup(self) -> dict[str, Any]:
        if not self._service_exists(self.disconnect_action):
            raise RuntimeError("Disconnect action is not available.")
        async with self._transport_job("hangup", "hangup"):
            await self._execute_user_service(self.disconnect_action)
        self._dispatch_event("call_hung_up", {"action": self.disconnect_action})
        return {"status": "success"}

    async def _perform_single_call_attempt(
        self,
        *,
        batch_id: str,
        recipient: str,
        recipient_label: str,
        ring_time_s: int,
        attempt: int,
    ) -> str:
        _LOGGER.info(
            "Call batch %s on %s: dialing %s with action=%s attempt=%s ring_time=%ss",
            batch_id,
            self.host,
            recipient_label,
            self.dial_action,
            attempt,
            ring_time_s,
        )
        await self._execute_user_service(self.dial_action, {"recipient": recipient})

        if not self.has_call_state_tracking:
            await asyncio.sleep(ring_time_s)
            await self._execute_user_service(self.disconnect_action)
            return "unknown"

        connected = await self._wait_for_call_connected(ring_time_s)
        if connected:
            await asyncio.sleep(ring_time_s)
            await self._execute_user_service(self.disconnect_action)
            return "connected"

        await self._execute_user_service(self.disconnect_action)
        return "not_connected"

    async def _async_on_connect(self) -> None:
        if self._client is None:
            return
        try:
            device, entities, services = await self._client.device_info_and_list_entities()
            self.device = device
            self.entity_infos = {entity.key: entity for entity in entities}
            self.user_services = {service.name: service for service in services}
            self.roles = self._detect_roles(entities)
            self._client.subscribe_states(self._handle_state_callback)
            self.available = True
            self._last_connect_error = None
            self._warmup_until = asyncio.get_running_loop().time() + 5
            _LOGGER.info("Connected to ESPHome gateway %s:%s", self.host, self.port)
            self._dispatch_event(
                "availability",
                {
                    "available": True,
                    "host": self.host,
                    "port": self.port,
                    "device_name": device.name,
                },
                store=False,
            )
        except Exception as err:
            self._last_connect_error = str(err)
            _LOGGER.exception("Failed to initialize Q-Tronic gateway after connecting")
            await self._client.disconnect(force=True)

    async def _async_on_disconnect(self, expected_disconnect: bool) -> None:
        self.available = False
        self._warmup_until = 0.0
        _LOGGER.warning(
            "Q-Tronic gateway disconnected from ESPHome %s:%s (%s)",
            self.host,
            self.port,
            "expected" if expected_disconnect else "unexpected",
        )
        self._dispatch_event(
            "availability",
            {
                "available": False,
                "host": self.host,
                "port": self.port,
                "expected_disconnect": expected_disconnect,
            },
            store=False,
        )

    async def _async_on_connect_error(self, err: Exception) -> None:
        self._last_connect_error = str(err)
        _LOGGER.warning("ESPHome connect error for %s:%s: %s", self.host, self.port, err)

    def _handle_state_callback(self, state: EntityState) -> None:
        role = self.role_for_state_key(state.key)
        previous_state = self.states.get(state.key)
        self.states[state.key] = state
        self._state_version += 1
        self._state_event.set()

        if role is not None:
            self._dispatch_event(
                "state_changed",
                {
                    "role": role,
                    "value": state_as_value(state),
                },
                store=False,
            )

        if asyncio.get_running_loop().time() < self._warmup_until:
            return

        if role == ROLE_SMS_MESSAGE:
            message = state_as_text(state)
            previous_message = state_as_text(previous_state)
            sender = state_as_text(self.state_for_role(ROLE_SMS_SENDER))
            if message and sender and message != previous_message:
                saved = self.recipient_for_phone(sender)
                self._dispatch_event(
                    "sms_received",
                    {
                        "sender": sender,
                        "sender_normalized": phone_match_key(sender),
                        "saved_recipient_id": saved.id if saved else None,
                        "saved_recipient_name": saved.name if saved else None,
                        "message": message,
                        "message_search": normalize_inbound_text(message),
                    },
                )
        elif role == ROLE_INCOMING_CALL:
            caller = state_as_text(state)
            previous_caller = state_as_text(previous_state)
            if caller and caller != previous_caller:
                saved = self.recipient_for_phone(caller)
                self._dispatch_event(
                    "incoming_call",
                    {
                        "caller": caller,
                        "caller_normalized": phone_match_key(caller),
                        "saved_recipient_id": saved.id if saved else None,
                        "saved_recipient_name": saved.name if saved else None,
                    },
                )

    def _detect_roles(self, entities: list[EntityInfo]) -> dict[str, int]:
        overrides = {
            ROLE_RSSI: self.config.esphome.rssi_object_id,
            ROLE_REGISTERED: self.config.esphome.registered_object_id,
            ROLE_SMS_SENDER: self.config.esphome.sms_sender_object_id,
            ROLE_SMS_MESSAGE: self.config.esphome.sms_message_object_id,
            ROLE_INCOMING_CALL: self.config.esphome.incoming_call_object_id,
            ROLE_CALL_STATE: self.config.esphome.call_state_object_id,
            ROLE_USSD: self.config.esphome.ussd_object_id,
        }
        detected: dict[str, int] = {}
        for role, aliases in AUTO_DETECT_OBJECT_IDS.items():
            override = normalize_object_id(overrides.get(role))
            info = self._find_entity_for_role(role, entities, override, aliases)
            if info is not None:
                detected[role] = info.key
        return detected

    def _find_entity_for_role(
        self,
        role: str,
        entities: list[EntityInfo],
        override: str | None,
        aliases: tuple[str, ...],
    ) -> EntityInfo | None:
        if role == ROLE_RSSI:
            candidates = [entity for entity in entities if isinstance(entity, SensorInfo)]
        elif role == ROLE_REGISTERED:
            candidates = [entity for entity in entities if isinstance(entity, BinarySensorInfo)]
        else:
            candidates = [entity for entity in entities if isinstance(entity, TextSensorInfo)]

        normalized_aliases = {normalize_object_id(alias) for alias in aliases}

        if override:
            for entity in candidates:
                if normalize_object_id(entity.object_id) == override:
                    return entity

        for entity in candidates:
            if normalize_object_id(entity.object_id) in normalized_aliases:
                return entity

        for entity in candidates:
            if normalize_object_id(entity.name) in normalized_aliases:
                return entity
        return None
