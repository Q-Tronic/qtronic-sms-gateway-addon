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
    sms_segment_info,
    split_sms_message,
    transliterate_sms_text,
)
from .storage import PersistentStore, TERMINAL_OUTBOX_STATES
from .validation import (
    MAX_SMS_CHARACTERS,
    UssdRequest,
    parse_ring_time,
    validate_resolved_recipients,
)

_LOGGER = logging.getLogger(__name__)
SMS_EVENT_DEBOUNCE_S = 2.0
INCOMING_CALL_EVENT_DEBOUNCE_S = 5.0

AUTH_ERRORS = (
    RequiresEncryptionAPIError,
    InvalidEncryptionKeyAPIError,
    InvalidAuthAPIError,
)

ROLE_RSSI = "rssi"
ROLE_REGISTERED = "registered"
ROLE_MODEM_ONLINE = "modem_online"
ROLE_SMS_SENDER = "sms_sender"
ROLE_SMS_MESSAGE = "sms_message"
ROLE_INCOMING_CALL = "incoming_call"
ROLE_CALL_STATE = "call_state"
ROLE_USSD = "ussd"
ROLE_SMS_STATUS = "sms_status"
ROLE_SMS_LAST_ERROR = "sms_last_error"
ROLE_SMS_QUEUE_DEPTH = "sms_queue_depth"
ROLE_SMS_SENT_COUNT = "sms_sent_count"
ROLE_SMS_FAILED_COUNT = "sms_failed_count"
ROLE_SMS_UNKNOWN_COUNT = "sms_unknown_count"
ROLE_SIM800_STATE = "sim800_state"
ROLE_SIM800_TIMEOUT_COUNT = "sim800_timeout_count"
ROLE_SIM800_RECOVERY_COUNT = "sim800_recovery_count"
ROLE_UART_RX_OVERFLOW_COUNT = "uart_rx_overflow_count"
ROLE_SIM800_LAST_RESPONSE_AGE = "sim800_last_response_age"

AUTO_DETECT_OBJECT_IDS: dict[str, tuple[str, ...]] = {
    ROLE_RSSI: ("rssi", "signal", "signal_strength"),
    ROLE_REGISTERED: ("registered", "network_registered"),
    ROLE_MODEM_ONLINE: ("modem_online", "sim800_online", "modem_available"),
    ROLE_SMS_SENDER: ("sms_sender", "sender"),
    ROLE_SMS_MESSAGE: ("sms_message", "message", "sms"),
    ROLE_INCOMING_CALL: ("incoming_call", "caller_id", "call"),
    ROLE_CALL_STATE: ("call_state", "gsm_call_state", "sim800_call_state"),
    ROLE_USSD: ("ussd", "ussd_message"),
    ROLE_SMS_STATUS: ("sms_status",),
    ROLE_SMS_LAST_ERROR: ("sms_last_error",),
    ROLE_SMS_QUEUE_DEPTH: ("sms_queue_depth",),
    ROLE_SMS_SENT_COUNT: ("sms_sent_count",),
    ROLE_SMS_FAILED_COUNT: ("sms_failed_count",),
    ROLE_SMS_UNKNOWN_COUNT: ("sms_unknown_count",),
    ROLE_SIM800_STATE: ("sim800_state",),
    ROLE_SIM800_TIMEOUT_COUNT: ("sim800_timeout_count",),
    ROLE_SIM800_RECOVERY_COUNT: ("sim800_recovery_count",),
    ROLE_UART_RX_OVERFLOW_COUNT: ("uart_rx_overflow_count",),
    ROLE_SIM800_LAST_RESPONSE_AGE: ("sim800_last_response_age",),
}

TEXT_ROLES = {
    ROLE_SMS_SENDER,
    ROLE_SMS_MESSAGE,
    ROLE_INCOMING_CALL,
    ROLE_CALL_STATE,
    ROLE_USSD,
    ROLE_SMS_STATUS,
    ROLE_SMS_LAST_ERROR,
    ROLE_SIM800_STATE,
}
NUMERIC_ROLES = {
    ROLE_RSSI,
    ROLE_SMS_QUEUE_DEPTH,
    ROLE_SMS_SENT_COUNT,
    ROLE_SMS_FAILED_COUNT,
    ROLE_SMS_UNKNOWN_COUNT,
    ROLE_SIM800_TIMEOUT_COUNT,
    ROLE_SIM800_RECOVERY_COUNT,
    ROLE_UART_RX_OVERFLOW_COUNT,
    ROLE_SIM800_LAST_RESPONSE_AGE,
}


class JobCancelled(RuntimeError):
    """Raised internally when an active or queued transport job is canceled."""


def normalize_object_id(value: str | None) -> str | None:
    """Normalize an ESPHome object ID or entity name."""
    if value is None:
        return None
    normalized = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    return normalized or None


def mask_phone_for_log(value: str | None) -> str:
    """Return a non-reversible phone label suitable for INFO logs."""
    digits = re.sub(r"\D", "", value or "")
    if not digits:
        return "unknown"
    suffix = digits[-4:]
    prefix = "+" if (value or "").strip().startswith("+") else ""
    return prefix + "*" * max(3, len(digits) - len(suffix)) + suffix


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

    def __init__(
        self, config: AddonConfig, store: PersistentStore | None = None
    ) -> None:
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
        self._role_versions: dict[str, int] = {}
        self._sms_status_results: deque[tuple[int, str]] = deque(maxlen=32)
        self._send_lock = asyncio.Lock()
        self._queued_job_count = 0
        self._active_job_kind: str | None = None
        self._active_job_id: str | None = None
        self._active_cancel_event: asyncio.Event | None = None
        self._queued_cancel_events: dict[str, tuple[str, asyncio.Event]] = {}
        self._canceled_job_ids: set[str] = set()
        self._disconnect_completed_job_ids: set[str] = set()
        self._warmup_until = 0.0
        self._recent_events: deque[dict[str, Any]] = deque(maxlen=100)
        self._started_at = time()
        self._last_connect_error: str | None = None
        self._last_sms_batch = SmsBatchDiagnostics()
        self._last_call_batch = CallBatchDiagnostics()
        self._last_sms_signature: str | None = None
        self._last_sms_event_at = 0.0
        self._last_incoming_call_signature: str | None = None
        self._last_incoming_call_event_at = 0.0
        self.store = store or PersistentStore(config.history)
        self.gateway_uuid: str | None = None
        self._store_ready = False
        self._outbox_task: asyncio.Task[None] | None = None
        self._outbox_wakeup = asyncio.Event()
        self._outbox_changed = asyncio.Event()
        self._outbox_depth_cache = 0
        self._history_tasks: set[asyncio.Task[None]] = set()

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

    @property
    def send_ussd_action(self) -> str:
        return self.config.esphome.send_ussd_action

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
        return self._service_supports_recipient_only(
            self.dial_action
        ) and self._service_exists(self.disconnect_action)

    @property
    def can_send_ussd(self) -> bool:
        service = self.user_services.get(self.send_ussd_action)
        if service is None:
            return False
        return any(arg.name in {"code", "ussd", "request"} for arg in service.args)

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

    def _dispatch_event(
        self, event_type: str, payload: dict[str, Any], *, store: bool = True
    ) -> None:
        event = {
            "type": event_type,
            "timestamp": time(),
            **payload,
        }
        if store:
            self._recent_events.appendleft(event)
            if self._store_ready:
                task = asyncio.create_task(self.store.append_history(event))
                self._history_tasks.add(task)
                task.add_done_callback(self._history_task_done)
        self._log_event_summary(event)
        for listener in tuple(self._listeners):
            result = listener(event)
            if asyncio.iscoroutine(result):
                asyncio.create_task(result)

    def _history_task_done(self, task: asyncio.Task[None]) -> None:
        self._history_tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            _LOGGER.warning("Failed to persist gateway history event: %s", error)

    def _log_event_summary(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == "sms_received":
            sender = event.get("saved_recipient_name") or mask_phone_for_log(
                str(event.get("sender") or "")
            )
            _LOGGER.info(
                "SMS received from %s (length=%s)",
                sender,
                len(str(event.get("message") or "")),
            )
        elif event_type == "incoming_call":
            caller = event.get("saved_recipient_name") or mask_phone_for_log(
                str(event.get("caller") or "")
            )
            _LOGGER.info("Incoming call from %s", caller)
        elif event_type == "sms_batch_started":
            _LOGGER.info(
                "SMS batch %s started for %s (encoding=%s, len=%s)",
                event.get("batch_id"),
                len(event.get("recipients", [])),
                event.get("encoding"),
                event.get("message_length"),
            )
        elif event_type == "sms_sent":
            _LOGGER.info(
                "SMS batch %s confirmed for %s (encoding=%s, length=%s)",
                event.get("batch_id"),
                mask_phone_for_log(str(event.get("recipient") or "")),
                event.get("encoding"),
                event.get("message_length"),
            )
        elif event_type == "sms_batch_finished":
            _LOGGER.info(
                "SMS batch %s finished with status=%s completed_count=%s failed=%s",
                event.get("batch_id"),
                event.get("status"),
                len(event.get("completed_recipients", [])),
                bool(event.get("failed_recipient")),
            )
        elif event_type == "call_batch_started":
            _LOGGER.info(
                "Call batch %s started for %s (ring_time=%ss)",
                event.get("batch_id"),
                len(event.get("recipients", [])),
                event.get("ring_time_s"),
            )
        elif event_type == "call_batch_finished":
            _LOGGER.info(
                "Call batch %s finished with status=%s completed_count=%s failed_count=%s unknown_count=%s",
                event.get("batch_id"),
                event.get("status"),
                len(event.get("completed_recipients", [])),
                len(event.get("failed_recipients", [])),
                len(event.get("unknown_recipients", [])),
            )
        elif event_type == "call_hung_up":
            _LOGGER.info(
                "Active call has been hung up using action=%s", event.get("action")
            )

    def events_snapshot(self) -> list[dict[str, Any]]:
        return list(self._recent_events)

    def snapshot(self) -> dict[str, Any]:
        """Return a summary used by REST, web UI, and MQTT."""
        role_values: dict[str, Any] = {}
        for role in (
            ROLE_RSSI,
            ROLE_REGISTERED,
            ROLE_MODEM_ONLINE,
            ROLE_SMS_SENDER,
            ROLE_SMS_MESSAGE,
            ROLE_INCOMING_CALL,
            ROLE_CALL_STATE,
            ROLE_USSD,
            ROLE_SMS_STATUS,
            ROLE_SMS_LAST_ERROR,
            ROLE_SMS_QUEUE_DEPTH,
            ROLE_SMS_SENT_COUNT,
            ROLE_SMS_FAILED_COUNT,
            ROLE_SMS_UNKNOWN_COUNT,
            ROLE_SIM800_STATE,
            ROLE_SIM800_TIMEOUT_COUNT,
            ROLE_SIM800_RECOVERY_COUNT,
            ROLE_UART_RX_OVERFLOW_COUNT,
            ROLE_SIM800_LAST_RESPONSE_AGE,
        ):
            role_values[role] = state_as_value(self.state_for_role(role))

        registered = role_values[ROLE_REGISTERED]
        modem_online = role_values[ROLE_MODEM_ONLINE]
        esp_status = "ok" if self.available else "offline"
        if not self.available:
            sim800_status = "unknown"
        elif modem_online is False:
            sim800_status = "offline"
        elif modem_online is not True:
            sim800_status = "unknown"
        elif registered is True:
            sim800_status = "online"
        elif registered is False:
            sim800_status = "not_registered"
        else:
            sim800_status = "unknown"

        return {
            "gateway_uuid": self.gateway_uuid,
            "api_version": 2,
            "available": self.available,
            "component_status": {
                "esp": esp_status,
                "sim800": sim800_status,
            },
            "host": self.host,
            "port": self.port,
            "device": {
                "name": self.device.name if self.device else None,
                "model": self.device.model if self.device else None,
                "manufacturer": self.device.manufacturer if self.device else None,
                "esphome_version": self.device.esphome_version if self.device else None,
            },
            "queue_depth": self._queued_job_count + self._outbox_depth_cache,
            "persistent_outbox_depth": self._outbox_depth_cache,
            "active_job_kind": self._active_job_kind,
            "active_job_id": self._active_job_id,
            "last_connect_error": self._last_connect_error,
            "started_at": self._started_at,
            "services": {
                "send_sms": self.can_send_sms,
                "send_sms_unicode": self.can_send_unicode_sms,
                "call": self.can_place_calls,
                "send_ussd": self.can_send_ussd,
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
                "attempts": {
                    name: count for name, count in self.last_call_batch.attempts
                },
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

    def resolve_recipient_input(self, raw_value: str | None) -> list[str]:
        tokens = [
            token.strip()
            for token in re.split(r"[,\n;]+", raw_value or "")
            if isinstance(token, str) and token.strip()
        ]
        recipients: list[str] = []
        recipient_ids: list[str] = []
        for token in tokens:
            if self.recipient_by_id(token) is not None:
                recipient_ids.append(token)
            else:
                recipients.append(token)
        return self.resolve_recipient_numbers(
            recipients=recipients, recipient_ids=recipient_ids
        )

    def describe_recipient(self, phone: str) -> str:
        normalized = normalize_phone_number_loose(phone)
        saved = self.recipient_for_phone(normalized)
        if saved is None:
            return normalized or phone
        return f"{saved.name} ({saved.masked_phone})"

    async def async_start(self) -> None:
        """Start reconnect logic without blocking add-on startup forever."""
        await self.store.initialize()
        self._store_ready = True
        self.gateway_uuid = await self.store.gateway_uuid()
        self._outbox_depth_cache = await self.store.outbox_depth()
        self._outbox_task = asyncio.create_task(
            self._outbox_worker(), name="qtronic-sms-outbox"
        )
        self._outbox_wakeup.set()
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
        if self._outbox_task is not None:
            self._outbox_task.cancel()
            try:
                await self._outbox_task
            except asyncio.CancelledError:
                pass
            self._outbox_task = None
        if self._reconnect_logic is not None:
            await self._reconnect_logic.stop()
            self._reconnect_logic = None
        if self._client is not None:
            await self._client.disconnect(force=True)
            self._client = None
        if self._history_tasks:
            await asyncio.gather(*tuple(self._history_tasks), return_exceptions=True)
        if self._store_ready:
            await self.store.close()
            self._store_ready = False

    @asynccontextmanager
    async def _transport_job(self, kind: str, job_id: str):
        cancel_event = asyncio.Event()
        self._queued_cancel_events[job_id] = (kind, cancel_event)
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
        try:
            await self._send_lock.acquire()
        except BaseException:
            self._queued_job_count -= 1
            self._queued_cancel_events.pop(job_id, None)
            self._disconnect_completed_job_ids.discard(job_id)
            raise
        self._queued_job_count -= 1
        self._active_job_kind = kind
        self._active_job_id = job_id
        self._active_cancel_event = cancel_event
        self._queued_cancel_events.pop(job_id, None)
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
            self._raise_if_canceled(cancel_event)
            yield cancel_event
        finally:
            self._active_job_kind = None
            self._active_job_id = None
            self._active_cancel_event = None
            self._disconnect_completed_job_ids.discard(job_id)
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

    @staticmethod
    def _raise_if_canceled(cancel_event: asyncio.Event | None) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise JobCancelled("Transport job was canceled.")

    async def _sleep_or_cancel(
        self, delay_s: float, cancel_event: asyncio.Event | None
    ) -> None:
        self._raise_if_canceled(cancel_event)
        if delay_s <= 0:
            return
        if cancel_event is None:
            await asyncio.sleep(delay_s)
            return
        try:
            await asyncio.wait_for(cancel_event.wait(), timeout=delay_s)
        except TimeoutError:
            return
        raise JobCancelled("Transport job was canceled.")

    async def _execute_user_service(
        self, service_name: str, data: dict[str, Any] | None = None
    ) -> None:
        if self._client is None:
            raise RuntimeError("The gateway is currently unavailable.")
        service = self.user_services.get(service_name)
        if service is None:
            raise RuntimeError(
                f"ESPHome action '{service_name}' was not found on {self.host}."
            )
        await self._client.execute_service(service, data or {})

    async def _wait_for_call_connected(
        self, timeout_s: int, cancel_event: asyncio.Event | None = None
    ) -> bool:
        deadline = asyncio.get_running_loop().time() + timeout_s
        while True:
            self._raise_if_canceled(cancel_event)
            if (
                state_as_text(self.state_for_role(ROLE_CALL_STATE)) or ""
            ).lower().strip() == "connected":
                return True
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return (
                    state_as_text(self.state_for_role(ROLE_CALL_STATE)) or ""
                ).lower().strip() == "connected"
            observed_version = self._state_version
            self._state_event.clear()
            state_wait = asyncio.create_task(self._state_event.wait())
            tasks: set[asyncio.Task[Any]] = {state_wait}
            cancel_wait: asyncio.Task[Any] | None = None
            if cancel_event is not None:
                cancel_wait = asyncio.create_task(cancel_event.wait())
                tasks.add(cancel_wait)
            done, pending = await asyncio.wait(
                tasks, timeout=remaining, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            if not done:
                return False
            if (
                cancel_wait is not None
                and cancel_wait in done
                and cancel_event.is_set()
            ):
                raise JobCancelled("Transport job was canceled.")
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
        idempotency_key: str | None = None,
        wait_timeout_s: float = 30,
    ) -> dict[str, Any]:
        accepted = await self.async_enqueue_sms_batch(
            message=message,
            recipients=recipients,
            encoding=encoding,
            batch_id=batch_id,
            idempotency_key=idempotency_key,
        )
        return await self.async_wait_outbox_job(
            accepted["job_id"], timeout_s=max(0.1, min(300.0, wait_timeout_s))
        )

    async def async_enqueue_sms_batch(
        self,
        *,
        message: str,
        recipients: list[str],
        encoding: str | None = None,
        batch_id: str | None = None,
        idempotency_key: str | None = None,
        message_segments: list[str] | None = None,
    ) -> dict[str, Any]:
        """Persist an SMS job before acknowledging it to the caller."""
        if not isinstance(message, str) or not message.strip():
            raise RuntimeError("SMS message must be a non-empty string.")
        if len(message) > MAX_SMS_CHARACTERS:
            raise RuntimeError(
                f"SMS message cannot exceed {MAX_SMS_CHARACTERS} characters."
            )
        normalized_recipients = validate_resolved_recipients(recipients)
        resolved_encoding = normalize_encoding(
            encoding or self.config.sms.default_encoding
        )
        info = sms_segment_info(
            message,
            resolved_encoding,
            unicode_available=self.can_send_unicode_sms,
        )
        if info.segments > self.config.sms.max_segments:
            raise RuntimeError(
                f"Message needs {info.segments} SMS segments; configured maximum is "
                f"{self.config.sms.max_segments}."
            )
        if info.segments > 1 and not self.config.sms.split_long:
            raise RuntimeError(
                f"Message needs {info.segments} SMS segments, but sms.split_long is disabled."
            )
        parts = message_segments
        if parts is None:
            parts = (
                split_sms_message(message, info)
                if self.config.sms.split_long
                else [message]
            )
        if (
            not parts
            or any(not isinstance(part, str) or not part for part in parts)
            or "".join(parts) != message
            or len(parts) != info.segments
        ):
            raise RuntimeError(
                "SMS message segments do not match the original message."
            )
        payload = {
            "message": message,
            "message_segments": parts,
            "segment_info": info.as_dict(),
            "recipients": normalized_recipients,
            "encoding": resolved_encoding,
            "checkpoint": {
                "recipient_index": 0,
                "part_index": 0,
                "completed_recipients": [],
                "unknown_recipients": [],
            },
        }
        row, created = await self.store.enqueue_outbox(
            kind="sms",
            payload=payload,
            max_attempts=self.config.sms.retries + 1,
            idempotency_key=idempotency_key,
            job_id=batch_id,
        )
        self._outbox_depth_cache = await self.store.outbox_depth()
        if created:
            self._dispatch_event(
                "sms_accepted",
                {
                    "job_id": row["job_id"],
                    "batch_id": row["job_id"],
                    "recipient_count": len(normalized_recipients),
                    "segments_per_recipient": len(parts),
                    "encoding": info.encoding,
                    "message_length": len(message),
                },
            )
            self._outbox_wakeup.set()
        return {
            "job_id": row["job_id"],
            "batch_id": row["job_id"],
            "status": row["status"],
            "created": created,
            "idempotent_replay": not created,
            "segment_info": info.as_dict(),
        }

    async def async_wait_outbox_job(
        self, job_id: str, timeout_s: float | None = 30
    ) -> dict[str, Any]:
        bounded_timeout = 30.0 if timeout_s is None else max(0.1, min(300.0, timeout_s))
        deadline = asyncio.get_running_loop().time() + bounded_timeout
        while True:
            row = await self.store.get_outbox(job_id)
            if row is None:
                raise RuntimeError(f"Unknown outbox job '{job_id}'.")
            if row["status"] in TERMINAL_OUTBOX_STATES:
                result = row.get("result") or {}
                return {
                    "job_id": job_id,
                    "batch_id": job_id,
                    "status": row["status"],
                    **result,
                    "attempts": row["attempts"],
                    "last_error": row["last_error"],
                }
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return {
                    "job_id": job_id,
                    "batch_id": job_id,
                    "status": "accepted",
                    "delivery_status": row["status"],
                    "wait_status": "timeout",
                    "wait_timed_out": True,
                    "attempts": row["attempts"],
                }
            self._outbox_changed.clear()
            try:
                await asyncio.wait_for(
                    self._outbox_changed.wait(), timeout=min(1.0, remaining)
                )
            except TimeoutError:
                pass

    async def _outbox_worker(self) -> None:
        while True:
            if not self.available:
                self._outbox_wakeup.clear()
                try:
                    await asyncio.wait_for(self._outbox_wakeup.wait(), timeout=1.0)
                except TimeoutError:
                    pass
                continue
            row = await self.store.claim_next_outbox(kind="sms")
            if row is None:
                await self.async_refresh_outbox_depth()
                self._outbox_wakeup.clear()
                try:
                    await asyncio.wait_for(self._outbox_wakeup.wait(), timeout=1.0)
                except TimeoutError:
                    pass
                continue

            self._outbox_depth_cache = await self.store.outbox_depth()
            self._outbox_changed.set()
            job_id = row["job_id"]
            try:
                if job_id in self._canceled_job_ids:
                    raise JobCancelled(
                        "Outbox job was canceled before transport started."
                    )
                if row["kind"] != "sms":
                    raise RuntimeError(f"Unsupported outbox job kind '{row['kind']}'.")
                payload = row["payload"]

                async def save_checkpoint(checkpoint: dict[str, Any]) -> None:
                    payload["checkpoint"] = checkpoint
                    updated = await self.store.update_outbox_payload(job_id, payload)
                    if updated is None or updated["status"] == "canceled":
                        raise JobCancelled("Outbox job was canceled while sending.")

                result = await self._async_send_sms_batch_now(
                    message=str(payload["message"]),
                    message_segments=[
                        str(item) for item in payload.get("message_segments", [])
                    ],
                    recipients=[str(item) for item in payload["recipients"]],
                    encoding=str(
                        payload.get("encoding") or self.config.sms.default_encoding
                    ),
                    batch_id=job_id,
                    checkpoint=dict(payload.get("checkpoint") or {}),
                    progress_callback=save_checkpoint,
                )
                final_status = str(result.get("status") or "unknown")
                if final_status not in {"sent", "unknown"}:
                    final_status = "unknown"
                await self.store.update_outbox(job_id, final_status, result=result)
            except JobCancelled as err:
                await self.store.update_outbox(
                    job_id,
                    "canceled",
                    last_error=str(err),
                    result={"status": "canceled"},
                )
            except Exception as err:
                current = await self.store.get_outbox(job_id)
                if (
                    current is not None
                    and current["attempts"] < current["max_attempts"]
                ):
                    delay = self.config.sms.retry_backoff_s * (
                        2 ** max(0, current["attempts"] - 1)
                    )
                    await self.store.update_outbox(
                        job_id,
                        "retry",
                        last_error=str(err),
                        next_attempt_at=time() + delay,
                    )
                    self._outbox_wakeup.set()
                else:
                    await self.store.update_outbox(
                        job_id,
                        "failed",
                        last_error=str(err),
                        result={"status": "failed"},
                    )
            finally:
                self._canceled_job_ids.discard(job_id)
                self._outbox_depth_cache = await self.store.outbox_depth()
                self._outbox_changed.set()

    async def async_refresh_outbox_depth(self) -> int:
        self._outbox_depth_cache = await self.store.outbox_depth()
        self._outbox_changed.set()
        return self._outbox_depth_cache

    async def _wait_for_sms_confirmation(
        self,
        version_before_send: int,
        cancel_event: asyncio.Event | None,
    ) -> tuple[str, str | None]:
        if self.entity_info_for_role(ROLE_SMS_STATUS) is None:
            return "unknown", None
        deadline = (
            asyncio.get_running_loop().time() + self.config.sms.confirmation_timeout_s
        )
        while True:
            self._raise_if_canceled(cancel_event)
            for version, status in tuple(self._sms_status_results):
                if version <= version_before_send:
                    continue
                if status == "sent":
                    return "sent", None
                if status == "failed":
                    error = state_as_text(self.state_for_role(ROLE_SMS_LAST_ERROR))
                    return "failed", error or "SIM800 reported SMS delivery failure."
                if status == "unknown":
                    return "unknown", None
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return "unknown", "Timed out waiting for SIM800 +CMGS confirmation."
            observed_version = self._state_version
            self._state_event.clear()
            state_wait = asyncio.create_task(self._state_event.wait())
            waits: set[asyncio.Task[Any]] = {state_wait}
            cancel_wait: asyncio.Task[Any] | None = None
            if cancel_event is not None:
                cancel_wait = asyncio.create_task(cancel_event.wait())
                waits.add(cancel_wait)
            done, pending = await asyncio.wait(
                waits, timeout=remaining, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            if (
                cancel_wait is not None
                and cancel_wait in done
                and cancel_event.is_set()
            ):
                raise JobCancelled("SMS batch was canceled.")
            if not done:
                return "unknown", "Timed out waiting for SIM800 +CMGS confirmation."
            if self._state_version == observed_version:
                continue

    async def _async_send_sms_batch_now(
        self,
        *,
        message: str,
        recipients: list[str],
        encoding: str | None = None,
        batch_id: str | None = None,
        message_segments: list[str] | None = None,
        checkpoint: dict[str, Any] | None = None,
        progress_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        batch_label = batch_id or uuid4().hex[:8]
        recipient_labels = tuple(
            self.describe_recipient(recipient) for recipient in recipients
        )
        parts = message_segments or [message]
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
                "message_length": len(message),
            },
        )

        checkpoint = checkpoint or {}
        start_recipient = max(
            0, min(len(recipients), int(checkpoint.get("recipient_index", 0)))
        )
        start_part = max(0, min(len(parts) - 1, int(checkpoint.get("part_index", 0))))
        completed = [
            str(label)
            for label in checkpoint.get("completed_recipients", [])
            if str(label) in recipient_labels
        ]
        unknown = [
            str(label)
            for label in checkpoint.get("unknown_recipients", [])
            if str(label) in recipient_labels
        ]
        failed_recipient: str | None = None
        last_error: str | None = None

        async with self._transport_job("sms", batch_label) as cancel_event:
            for index in range(start_recipient, len(recipients)):
                recipient = recipients[index]
                recipient_label = recipient_labels[index]
                try:
                    recipient_outcome = (
                        "unknown" if recipient_label in unknown else "sent"
                    )
                    first_part = start_part if index == start_recipient else 0
                    for part_index in range(first_part, len(parts)):
                        part = parts[part_index]
                        self._raise_if_canceled(cancel_event)
                        service_name, target, outgoing_message, resolved_mode = (
                            self._prepare_outgoing_sms(
                                message=part,
                                recipient=recipient,
                                encoding=encoding,
                            )
                        )
                        confirmation_version = self._role_versions.get(
                            ROLE_SMS_STATUS, 0
                        )
                        await self._execute_user_service(
                            service_name,
                            {"recipient": target, "message": outgoing_message},
                        )
                        self._dispatch_event(
                            "sms_transport_accepted",
                            {
                                "batch_id": batch_label,
                                "recipient": recipient,
                                "recipient_label": recipient_label,
                                "encoding": resolved_mode,
                                "part": part_index + 1,
                                "parts": len(parts),
                                "message_length": len(part),
                            },
                        )
                        (
                            outcome,
                            confirmation_error,
                        ) = await self._wait_for_sms_confirmation(
                            confirmation_version, cancel_event
                        )
                        if outcome == "failed":
                            raise RuntimeError(
                                confirmation_error or "SIM800 reported SMS failure."
                            )
                        if outcome == "unknown":
                            recipient_outcome = "unknown"
                            last_error = confirmation_error
                            if recipient_label not in unknown:
                                unknown.append(recipient_label)
                        elif outcome == "sent":
                            self._dispatch_event(
                                "sms_sent",
                                {
                                    "batch_id": batch_label,
                                    "recipient": recipient,
                                    "recipient_label": recipient_label,
                                    "encoding": resolved_mode,
                                    "part": part_index + 1,
                                    "parts": len(parts),
                                    "message_length": len(part),
                                    "confirmed_by": "sms_status",
                                },
                            )

                        next_recipient = index
                        next_part = part_index + 1
                        if next_part >= len(parts):
                            next_recipient = index + 1
                            next_part = 0
                            if (
                                recipient_outcome == "sent"
                                and recipient_label not in completed
                            ):
                                completed.append(recipient_label)
                        if progress_callback is not None:
                            await progress_callback(
                                {
                                    "recipient_index": next_recipient,
                                    "part_index": next_part,
                                    "completed_recipients": list(completed),
                                    "unknown_recipients": list(unknown),
                                }
                            )
                        if part_index < len(parts) - 1:
                            await self._sleep_or_cancel(
                                self.config.sms.send_delay_ms / 1000, cancel_event
                            )
                except JobCancelled:
                    raise
                except Exception as err:
                    failed_recipient = recipient_label
                    last_error = str(err)
                    break

                if index < len(recipients) - 1 and self.config.sms.send_delay_ms > 0:
                    await self._sleep_or_cancel(
                        self.config.sms.send_delay_ms / 1000, cancel_event
                    )

        final_status = (
            "failed" if failed_recipient else "unknown" if unknown else "sent"
        )
        self._last_sms_batch = SmsBatchDiagnostics(
            status=final_status,
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
                "unknown_recipients": unknown,
                "last_error": last_error,
            },
        )
        if failed_recipient:
            raise RuntimeError(last_error or f"SMS failed for {failed_recipient}")
        return {
            "batch_id": batch_label,
            "status": final_status,
            "completed_recipients": completed,
            "unknown_recipients": unknown,
        }

    async def async_call_batch(
        self,
        *,
        recipients: list[str],
        ring_time_s: int | None = None,
        batch_id: str | None = None,
    ) -> dict[str, Any]:
        recipients = validate_resolved_recipients(recipients)
        if not self.can_place_calls:
            raise RuntimeError("Dial/disconnect ESPHome actions are not available.")

        batch_label = batch_id or uuid4().hex[:8]
        ring_time = parse_ring_time(
            self.config.calling.default_ring_time_s
            if ring_time_s is None
            else ring_time_s
        )
        recipient_labels = tuple(
            self.describe_recipient(recipient) for recipient in recipients
        )
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

        canceled = False
        try:
            async with self._transport_job("call", batch_label) as cancel_event:
                for index, recipient in enumerate(recipients):
                    recipient_label = recipient_labels[index]
                    while True:
                        self._raise_if_canceled(cancel_event)
                        attempts[recipient_label] += 1
                        try:
                            outcome = await self._perform_single_call_attempt(
                                batch_id=batch_label,
                                recipient=recipient,
                                recipient_label=recipient_label,
                                ring_time_s=ring_time,
                                attempt=attempts[recipient_label],
                                cancel_event=cancel_event,
                            )
                            last_error = None
                        except JobCancelled:
                            raise
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
                            await self._sleep_or_cancel(
                                self.config.calling.retry_delay_s, cancel_event
                            )
                            last_error = None
                            continue

                        failed_recipient = recipient_label
                        failed.append(recipient_label)
                        break

                    if (
                        failed_recipient
                        and self.config.calling.failure_action == "stop_batch"
                    ):
                        break
                    if index < len(recipients) - 1:
                        await self._sleep_or_cancel(
                            self.config.calling.delay_between_calls_s, cancel_event
                        )
        except JobCancelled as err:
            canceled = True
            last_error = str(err)

        final_status = (
            "canceled"
            if canceled
            else "failed"
            if failed
            else "unknown"
            if unknown
            else "success"
        )
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
            last_error=last_error if failed or canceled else None,
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
        canceled_jobs: list[str] = []
        for queued_id, (kind, cancel_event) in tuple(
            self._queued_cancel_events.items()
        ):
            if kind == "call":
                cancel_event.set()
                canceled_jobs.append(queued_id)
        if self._active_job_kind == "call" and self._active_cancel_event is not None:
            self._active_cancel_event.set()
            if self._active_job_id:
                canceled_jobs.append(self._active_job_id)
        if not self._service_exists(self.disconnect_action):
            raise RuntimeError("Disconnect action is not available.")
        await self._execute_user_service(self.disconnect_action)
        self._mark_disconnect_completed(canceled_jobs)
        self._dispatch_event(
            "call_hung_up",
            {
                "action": self.disconnect_action,
                "canceled_jobs": sorted(set(canceled_jobs)),
            },
        )
        return {
            "status": "success",
            "canceled": bool(canceled_jobs),
            "canceled_jobs": sorted(set(canceled_jobs)),
            "disconnect_sent": True,
        }

    async def async_send_ussd(self, code: str) -> dict[str, Any]:
        code = UssdRequest.parse({"code": code}).code
        service = self.user_services.get(self.send_ussd_action)
        if service is None:
            raise RuntimeError(
                f"ESPHome action '{self.send_ussd_action}' is not available."
            )
        argument_names = {argument.name for argument in service.args}
        argument = next(
            (name for name in ("code", "ussd", "request") if name in argument_names),
            None,
        )
        if argument is None:
            raise RuntimeError(
                f"ESPHome action '{self.send_ussd_action}' has no code/ussd/request argument."
            )
        job_id = uuid4().hex[:8]
        async with self._transport_job("ussd", job_id) as cancel_event:
            self._raise_if_canceled(cancel_event)
            await self._execute_user_service(self.send_ussd_action, {argument: code})
        self._dispatch_event(
            "ussd_requested",
            {"job_id": job_id, "action": self.send_ussd_action, "code": code},
        )
        return {"job_id": job_id, "status": "accepted"}

    async def async_cancel(self, job_id: str | None = None) -> dict[str, Any]:
        """Cancel queued/active work immediately and issue disconnect outside the send lock."""
        canceled_runtime: list[str] = []
        canceled_call_jobs: list[str] = []
        cancel_call = False
        for queued_id, (kind, cancel_event) in tuple(
            self._queued_cancel_events.items()
        ):
            if job_id is None or queued_id == job_id:
                cancel_event.set()
                canceled_runtime.append(queued_id)
                cancel_call = cancel_call or kind == "call"
                if kind == "call":
                    canceled_call_jobs.append(queued_id)
        if self._active_cancel_event is not None and (
            job_id is None or self._active_job_id == job_id
        ):
            self._active_cancel_event.set()
            cancel_call = cancel_call or self._active_job_kind == "call"
            if self._active_job_id:
                canceled_runtime.append(self._active_job_id)
                if self._active_job_kind == "call":
                    canceled_call_jobs.append(self._active_job_id)

        # Signal in-memory work before the first database await. In particular,
        # retain the active call kind so cancellation is guaranteed to issue a
        # modem disconnect even if the call coroutine unwinds concurrently.
        pending_rows = await self.store.list_outbox(limit=1000)
        for row in pending_rows:
            if (
                row["kind"] == "sms"
                and row["status"] not in TERMINAL_OUTBOX_STATES
                and (job_id is None or row["job_id"] == job_id)
            ):
                self._canceled_job_ids.add(row["job_id"])

        canceled_persistent = await self.store.cancel_outbox(
            job_id, kind=None if job_id else "sms"
        )
        self._outbox_depth_cache = await self.store.outbox_depth()
        self._outbox_changed.set()

        if job_id is not None and not canceled_runtime and canceled_persistent == 0:
            raise RuntimeError(f"Unknown or already completed job '{job_id}'.")

        disconnected = False
        if cancel_call and not self._service_exists(self.disconnect_action):
            if not canceled_runtime:
                raise RuntimeError("Disconnect action is not available.")
        elif cancel_call:
            # Deliberately bypass _transport_job: hangup must preempt retry_forever immediately.
            await self._execute_user_service(self.disconnect_action)
            self._mark_disconnect_completed(canceled_call_jobs)
            disconnected = True
        self._dispatch_event(
            "call_hung_up" if disconnected else "transport_canceled",
            {
                "action": self.disconnect_action,
                "job_id": job_id,
                "canceled_jobs": sorted(set(canceled_runtime)),
                "canceled_persistent": canceled_persistent,
            },
        )
        return {
            "status": "success",
            "canceled": True,
            "job_id": job_id,
            "canceled_jobs": sorted(set(canceled_runtime)),
            "canceled_persistent": canceled_persistent,
            "disconnect_sent": disconnected,
        }

    def _mark_disconnect_completed(self, job_ids: list[str]) -> None:
        for job_id in job_ids:
            if job_id == self._active_job_id or job_id in self._queued_cancel_events:
                self._disconnect_completed_job_ids.add(job_id)

    async def _perform_single_call_attempt(
        self,
        *,
        batch_id: str,
        recipient: str,
        recipient_label: str,
        ring_time_s: int,
        attempt: int,
        cancel_event: asyncio.Event | None = None,
    ) -> str:
        _LOGGER.info(
            "Call batch %s on %s: dialing %s with action=%s attempt=%s ring_time=%ss",
            batch_id,
            self.host,
            mask_phone_for_log(recipient),
            self.dial_action,
            attempt,
            ring_time_s,
        )
        dial_started = False
        try:
            dial_started = True
            await self._execute_user_service(self.dial_action, {"recipient": recipient})
            self._raise_if_canceled(cancel_event)

            if not self.has_call_state_tracking:
                await self._sleep_or_cancel(ring_time_s, cancel_event)
                await self._execute_user_service(self.disconnect_action)
                _LOGGER.info(
                    "Call batch %s on %s: finished dialing %s without call state tracking",
                    batch_id,
                    self.host,
                    mask_phone_for_log(recipient),
                )
                return "unknown"

            connected = await self._wait_for_call_connected(ring_time_s, cancel_event)
            if connected:
                await self._sleep_or_cancel(ring_time_s, cancel_event)
                await self._execute_user_service(self.disconnect_action)
                _LOGGER.info(
                    "Call batch %s on %s: call with %s connected and was disconnected after %ss",
                    batch_id,
                    self.host,
                    mask_phone_for_log(recipient),
                    ring_time_s,
                )
                return "connected"

            await self._execute_user_service(self.disconnect_action)
            _LOGGER.info(
                "Call batch %s on %s: call to %s did not connect within %ss",
                batch_id,
                self.host,
                mask_phone_for_log(recipient),
                ring_time_s,
            )
            return "not_connected"
        except asyncio.CancelledError:
            if (
                dial_started
                and batch_id not in self._disconnect_completed_job_ids
                and self._service_exists(self.disconnect_action)
            ):
                try:
                    await self._execute_user_service(self.disconnect_action)
                except Exception as err:
                    _LOGGER.warning(
                        "Failed to disconnect canceled call batch %s: %s",
                        batch_id,
                        err,
                    )
            raise

    async def _async_on_connect(self) -> None:
        if self._client is None:
            return
        try:
            (
                device,
                entities,
                services,
            ) = await self._client.device_info_and_list_entities()
            self.device = device
            self.entity_infos = {entity.key: entity for entity in entities}
            self.user_services = {service.name: service for service in services}
            self.roles = self._detect_roles(entities)
            self._client.subscribe_states(self._handle_state_callback)
            self.available = True
            self._last_connect_error = None
            self._warmup_until = asyncio.get_running_loop().time() + 5
            self._outbox_wakeup.set()
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
        _LOGGER.warning(
            "ESPHome connect error for %s:%s: %s", self.host, self.port, err
        )

    def _handle_state_callback(self, state: EntityState) -> None:
        role = self.role_for_state_key(state.key)
        self.states[state.key] = state
        self._state_version += 1
        if role is not None:
            self._role_versions[role] = self._role_versions.get(role, 0) + 1
            if role == ROLE_SMS_STATUS:
                status = (state_as_text(state) or "").strip().lower()
                if status in {"idle", "queued", "sending", "sent", "failed", "unknown"}:
                    self._sms_status_results.append((self._role_versions[role], status))
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
            sender = state_as_text(self.state_for_role(ROLE_SMS_SENDER))
            if message and sender:
                saved = self.recipient_for_phone(sender)
                sender_key = phone_match_key(sender)
                message_search = normalize_inbound_text(message)
                signature = f"{sender_key}|{message_search}"
                now = time()
                if (
                    signature == self._last_sms_signature
                    and now - self._last_sms_event_at < SMS_EVENT_DEBOUNCE_S
                ):
                    _LOGGER.info(
                        "SMS event suppressed for %s due to %ss debounce window",
                        mask_phone_for_log(sender),
                        SMS_EVENT_DEBOUNCE_S,
                    )
                    return

                self._last_sms_signature = signature
                self._last_sms_event_at = now
                payload = {
                    "sender": sender,
                    "sender_normalized": sender_key,
                    "saved_recipient_id": saved.id if saved else None,
                    "saved_recipient_name": saved.name if saved else None,
                    "message": message,
                    "message_search": message_search,
                }
                _LOGGER.info(
                    "SMS event emitted: sender=%s saved_recipient=%s length=%s",
                    mask_phone_for_log(sender),
                    payload["saved_recipient_id"],
                    len(message),
                )
                self._dispatch_event("sms_received", payload)
        elif role == ROLE_INCOMING_CALL:
            caller = state_as_text(state)
            if caller:
                saved = self.recipient_for_phone(caller)
                signature = phone_match_key(caller) or caller
                now = time()
                if (
                    signature == self._last_incoming_call_signature
                    and now - self._last_incoming_call_event_at
                    < INCOMING_CALL_EVENT_DEBOUNCE_S
                ):
                    _LOGGER.info(
                        "Incoming call event suppressed for %s due to %ss debounce window",
                        mask_phone_for_log(caller),
                        INCOMING_CALL_EVENT_DEBOUNCE_S,
                    )
                    return

                self._last_incoming_call_signature = signature
                self._last_incoming_call_event_at = now
                payload = {
                    "caller": caller,
                    "caller_normalized": signature,
                    "saved_recipient_id": saved.id if saved else None,
                    "saved_recipient_name": saved.name if saved else None,
                }
                _LOGGER.info(
                    "Incoming call event emitted: caller=%s saved_recipient_id=%s",
                    mask_phone_for_log(caller),
                    payload["saved_recipient_id"],
                )
                self._dispatch_event("incoming_call", payload)

    def _detect_roles(self, entities: list[EntityInfo]) -> dict[str, int]:
        overrides = {
            ROLE_RSSI: self.config.esphome.rssi_object_id,
            ROLE_REGISTERED: self.config.esphome.registered_object_id,
            ROLE_MODEM_ONLINE: self.config.esphome.modem_online_object_id,
            ROLE_SMS_SENDER: self.config.esphome.sms_sender_object_id,
            ROLE_SMS_MESSAGE: self.config.esphome.sms_message_object_id,
            ROLE_INCOMING_CALL: self.config.esphome.incoming_call_object_id,
            ROLE_CALL_STATE: self.config.esphome.call_state_object_id,
            ROLE_USSD: self.config.esphome.ussd_object_id,
            ROLE_SMS_STATUS: self.config.esphome.sms_status_object_id,
            ROLE_SMS_LAST_ERROR: self.config.esphome.sms_last_error_object_id,
            ROLE_SMS_QUEUE_DEPTH: self.config.esphome.sms_queue_depth_object_id,
            ROLE_SMS_SENT_COUNT: self.config.esphome.sms_sent_count_object_id,
            ROLE_SMS_FAILED_COUNT: self.config.esphome.sms_failed_count_object_id,
            ROLE_SMS_UNKNOWN_COUNT: self.config.esphome.sms_unknown_count_object_id,
            ROLE_SIM800_STATE: self.config.esphome.sim800_state_object_id,
            ROLE_SIM800_TIMEOUT_COUNT: self.config.esphome.sim800_timeout_count_object_id,
            ROLE_SIM800_RECOVERY_COUNT: self.config.esphome.sim800_recovery_count_object_id,
            ROLE_UART_RX_OVERFLOW_COUNT: self.config.esphome.uart_rx_overflow_count_object_id,
            ROLE_SIM800_LAST_RESPONSE_AGE: self.config.esphome.sim800_last_response_age_object_id,
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
        if role in NUMERIC_ROLES:
            candidates = [
                entity for entity in entities if isinstance(entity, SensorInfo)
            ]
        elif role in (ROLE_REGISTERED, ROLE_MODEM_ONLINE):
            candidates = [
                entity for entity in entities if isinstance(entity, BinarySensorInfo)
            ]
        else:
            candidates = [
                entity for entity in entities if isinstance(entity, TextSensorInfo)
            ]

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
