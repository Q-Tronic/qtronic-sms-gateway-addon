"""HTTP-backed Q-Tronic SMS Gateway hub using the local add-on as backend."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
import logging
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo as HADeviceInfo
from yarl import URL

from .const import (
    CONF_CALL_FAILURE_ACTION,
    CONF_CALL_MAX_RETRIES,
    CONF_CALL_RETRY_DELAY_S,
    CONF_CALL_RETRY_FOREVER,
    CONF_DEFAULT_RECIPIENT,
    CONF_DEFAULT_RECIPIENT_IDS,
    CONF_DEFAULT_RING_TIME_S,
    CONF_DELAY_BETWEEN_CALLS_S,
    CONF_DIAL_ACTION,
    CONF_DISCONNECT_ACTION,
    CONF_ENCRYPTION_KEY,
    CONF_EXPECTED_MAC,
    CONF_EXPECTED_NAME,
    CONF_SAVED_RECIPIENTS,
    CONF_SEND_DELAY_MS,
    CONF_SEND_SMS_ACTION,
    CONF_SMS_ENCODING,
    CONF_UNICODE_SEND_SMS_ACTION,
    DEFAULT_CALL_FAILURE_ACTION,
    DEFAULT_CALL_MAX_RETRIES,
    DEFAULT_CALL_RETRY_DELAY_S,
    DEFAULT_CALL_RETRY_FOREVER,
    DEFAULT_DELAY_BETWEEN_CALLS_S,
    DEFAULT_DIAL_ACTION,
    DEFAULT_DISCONNECT_ACTION,
    DEFAULT_RING_TIME_S,
    DEFAULT_SEND_DELAY_MS,
    DEFAULT_SMS_ENCODING,
    DEFAULT_SEND_SMS_ACTION,
    DEFAULT_UNICODE_SEND_SMS_ACTION,
    DOMAIN,
    ROLE_CALL_STATE,
    ROLE_INCOMING_CALL,
    ROLE_REGISTERED,
    ROLE_RSSI,
    ROLE_SMS_MESSAGE,
    ROLE_SMS_SENDER,
    ROLE_USSD,
)
from .recipients import SavedRecipient, deduplicate_phone_numbers, load_saved_recipients
from .recipients import mask_phone_number
from .restart_issue import async_sync_restart_issue
from .sms import normalize_encoding

_LOGGER = logging.getLogger(__name__)

ADDON_HTTP_PORT = 8099
POLL_INTERVAL_S = 5


class GatewayConnectionError(HomeAssistantError):
    """Raised when the add-on backend cannot be reached."""


class GatewayAuthenticationError(HomeAssistantError):
    """Raised when backend authentication or access is rejected."""


@dataclass(frozen=True, slots=True)
class GatewayEntityInfo:
    """Simple entity description used by local entities."""

    object_id: str
    name: str
    icon: str | None = None
    unit_of_measurement: str | None = None
    accuracy_decimals: int = 0


@dataclass(frozen=True, slots=True)
class GatewayValidationInfo:
    """Connection-check payload used by the config flow."""

    name: str
    mac_address: str = ""


@dataclass(frozen=True, slots=True)
class SmsBatchDiagnostics:
    """Last known SMS batch state for one gateway."""

    status: str = "idle"
    batch_id: str | None = None
    gateway_host: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    recipient_count: int = 0
    recipients: tuple[str, ...] = ()
    completed_recipients: tuple[str, ...] = ()
    failed_recipient: str | None = None
    last_error: str | None = None
    encoding: str | None = None
    message_length: int = 0


@dataclass(frozen=True, slots=True)
class CallBatchDiagnostics:
    """Last known call batch state for one gateway."""

    status: str = "idle"
    batch_id: str | None = None
    gateway_host: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    recipient_count: int = 0
    recipients: tuple[str, ...] = ()
    completed_recipients: tuple[str, ...] = ()
    failed_recipients: tuple[str, ...] = ()
    unknown_recipients: tuple[str, ...] = ()
    failed_recipient: str | None = None
    last_error: str | None = None
    ring_time_s: int = 0
    attempts: tuple[tuple[str, int], ...] = ()
    state_tracking_available: bool = False


ENTITY_INFOS: dict[str, GatewayEntityInfo] = {
    ROLE_RSSI: GatewayEntityInfo("rssi", "RSSI", "mdi:signal", "dBm", 0),
    ROLE_REGISTERED: GatewayEntityInfo("registered", "Registered", "mdi:sim"),
    ROLE_SMS_SENDER: GatewayEntityInfo("sms_sender", "SMS Sender", "mdi:account-arrow-left"),
    ROLE_SMS_MESSAGE: GatewayEntityInfo("sms_message", "SMS Message", "mdi:message-text"),
    ROLE_INCOMING_CALL: GatewayEntityInfo(
        "incoming_call", "Incoming Call", "mdi:phone-incoming"
    ),
    ROLE_CALL_STATE: GatewayEntityInfo("call_state", "Call State", "mdi:phone"),
    ROLE_USSD: GatewayEntityInfo("ussd", "USSD", "mdi:card-text"),
}


def normalize_mac(value: str | None) -> str | None:
    """Normalize a MAC address-like identifier."""
    if not value:
        return None
    compact = "".join(char for char in value.lower() if char in "0123456789abcdef")
    return compact or None


def entry_value(entry: ConfigEntry, key: str, default: Any = None) -> Any:
    """Read a config value, letting options override setup data."""
    if key in entry.options:
        return entry.options[key]
    return entry.data.get(key, default)


def state_as_float(state: Any) -> float | None:
    """Extract a numeric state."""
    if state in (None, "", "unknown", "unavailable"):
        return None
    try:
        return float(state)
    except (TypeError, ValueError):
        return None


def state_as_bool(state: Any) -> bool | None:
    """Extract a boolean state."""
    if state in (None, "", "unknown", "unavailable"):
        return None
    if isinstance(state, bool):
        return state
    if isinstance(state, str):
        lowered = state.strip().lower()
        if lowered in {"true", "on", "1", "yes", "enabled"}:
            return True
        if lowered in {"false", "off", "0", "no", "disabled"}:
            return False
    return bool(state)


def state_as_text(state: Any) -> str | None:
    """Extract a text state."""
    if state in (None, "", "unknown", "unavailable"):
        return None
    return str(state)


class QTronicSmsGatewayHub:
    """Manage an HTTP connection to the local Q-Tronic add-on."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.available = False
        self.device_name = entry.title or "Q-Tronic SMS Gateway"
        self._listeners: set[Callable[[], None]] = set()
        self._poll_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._last_connect_error: str | None = None
        self._states: dict[str, Any] = {}
        self._service_flags: dict[str, bool] = {}
        self._queue_depth = 0
        self._active_job_kind: str | None = None
        self._active_job_id: str | None = None
        self._addon_saved_recipients: tuple[SavedRecipient, ...] = ()
        self._last_sms_batch = SmsBatchDiagnostics(gateway_host=self.host)
        self._last_call_batch = CallBatchDiagnostics(gateway_host=self.host)
        self._recent_status_hashes: deque[str] = deque(maxlen=5)

    @property
    def unique_id_prefix(self) -> str:
        return normalize_mac(entry_value(self.entry, CONF_EXPECTED_MAC)) or self.entry.unique_id or self.entry.entry_id

    @property
    def addon_base_url(self) -> str:
        configured_host = str(entry_value(self.entry, CONF_HOST, "")).strip()
        configured_port = int(entry_value(self.entry, CONF_PORT, 0) or 0)
        if configured_host and configured_port == ADDON_HTTP_PORT:
            return f"http://{configured_host}:{configured_port}"

        for candidate in (
            getattr(self.hass.config, "internal_url", None),
            getattr(self.hass.config, "external_url", None),
        ):
            if not candidate:
                continue
            try:
                url = URL(candidate)
                if url.host:
                    return str(url.with_port(ADDON_HTTP_PORT).with_path("").with_query(None).with_fragment(None)).rstrip("/")
            except Exception:
                continue

        api_config = getattr(self.hass.config, "api", None)
        if api_config is not None:
            scheme = "https" if bool(getattr(api_config, "use_ssl", False)) else "http"
            for attribute in ("local_ip", "host"):
                host = getattr(api_config, attribute, None)
                if host and str(host) not in {"0.0.0.0", "::"}:
                    return f"{scheme}://{host}:{ADDON_HTTP_PORT}"

        return f"http://homeassistant.local:{ADDON_HTTP_PORT}"

    @property
    def host(self) -> str:
        try:
            return URL(self.addon_base_url).host or "qtronic-sms-gateway"
        except Exception:
            return "qtronic-sms-gateway"

    @property
    def port(self) -> int:
        try:
            url = URL(self.addon_base_url)
        except Exception:
            return ADDON_HTTP_PORT
        if url.port:
            return url.port
        return 443 if url.scheme == "https" else 80

    @property
    def expected_name(self) -> str | None:
        value = entry_value(self.entry, CONF_EXPECTED_NAME)
        return value or None

    @property
    def default_recipient(self) -> str | None:
        value = entry_value(self.entry, CONF_DEFAULT_RECIPIENT)
        return value or None

    @property
    def default_recipient_ids(self) -> tuple[str, ...]:
        value = entry_value(self.entry, CONF_DEFAULT_RECIPIENT_IDS, ())
        if isinstance(value, (list, tuple)):
            return tuple(str(item) for item in value if item)
        return ()

    @property
    def configured_saved_recipients(self) -> tuple[SavedRecipient, ...]:
        return load_saved_recipients(entry_value(self.entry, CONF_SAVED_RECIPIENTS, []))

    @property
    def saved_recipients(self) -> tuple[SavedRecipient, ...]:
        return self._addon_saved_recipients or self.configured_saved_recipients

    @property
    def saved_recipient_map(self) -> dict[str, SavedRecipient]:
        return {recipient.id: recipient for recipient in self.saved_recipients}

    @property
    def send_delay_ms(self) -> int:
        try:
            return max(0, int(entry_value(self.entry, CONF_SEND_DELAY_MS, DEFAULT_SEND_DELAY_MS)))
        except (TypeError, ValueError):
            return DEFAULT_SEND_DELAY_MS

    @property
    def default_phone_numbers(self) -> list[str]:
        recipients = self.saved_recipient_map
        numbers = [
            recipients[recipient_id].phone
            for recipient_id in self.default_recipient_ids
            if recipient_id in recipients
        ]
        if self.default_recipient:
            numbers.append(self.default_recipient)
        return deduplicate_phone_numbers(numbers)

    @property
    def send_sms_action(self) -> str:
        return str(entry_value(self.entry, CONF_SEND_SMS_ACTION, DEFAULT_SEND_SMS_ACTION))

    @property
    def unicode_send_sms_action(self) -> str:
        return str(
            entry_value(
                self.entry,
                CONF_UNICODE_SEND_SMS_ACTION,
                DEFAULT_UNICODE_SEND_SMS_ACTION,
            )
        )

    @property
    def sms_encoding(self) -> str:
        return str(entry_value(self.entry, CONF_SMS_ENCODING, DEFAULT_SMS_ENCODING))

    @property
    def dial_action(self) -> str:
        return str(entry_value(self.entry, CONF_DIAL_ACTION, DEFAULT_DIAL_ACTION))

    @property
    def disconnect_action(self) -> str:
        return str(entry_value(self.entry, CONF_DISCONNECT_ACTION, DEFAULT_DISCONNECT_ACTION))

    @property
    def default_ring_time_s(self) -> int:
        try:
            return max(1, int(entry_value(self.entry, CONF_DEFAULT_RING_TIME_S, DEFAULT_RING_TIME_S)))
        except (TypeError, ValueError):
            return DEFAULT_RING_TIME_S

    @property
    def delay_between_calls_s(self) -> int:
        try:
            return max(
                0,
                int(
                    entry_value(
                        self.entry,
                        CONF_DELAY_BETWEEN_CALLS_S,
                        DEFAULT_DELAY_BETWEEN_CALLS_S,
                    )
                ),
            )
        except (TypeError, ValueError):
            return DEFAULT_DELAY_BETWEEN_CALLS_S

    @property
    def call_max_retries(self) -> int:
        try:
            return max(
                0,
                int(entry_value(self.entry, CONF_CALL_MAX_RETRIES, DEFAULT_CALL_MAX_RETRIES)),
            )
        except (TypeError, ValueError):
            return DEFAULT_CALL_MAX_RETRIES

    @property
    def call_retry_delay_s(self) -> int:
        try:
            return max(
                0,
                int(
                    entry_value(
                        self.entry,
                        CONF_CALL_RETRY_DELAY_S,
                        DEFAULT_CALL_RETRY_DELAY_S,
                    )
                ),
            )
        except (TypeError, ValueError):
            return DEFAULT_CALL_RETRY_DELAY_S

    @property
    def call_retry_forever(self) -> bool:
        return bool(entry_value(self.entry, CONF_CALL_RETRY_FOREVER, DEFAULT_CALL_RETRY_FOREVER))

    @property
    def call_failure_action(self) -> str:
        return str(
            entry_value(
                self.entry,
                CONF_CALL_FAILURE_ACTION,
                DEFAULT_CALL_FAILURE_ACTION,
            )
        )

    @property
    def last_sms_batch(self) -> SmsBatchDiagnostics:
        return self._last_sms_batch

    @property
    def last_call_batch(self) -> CallBatchDiagnostics:
        return self._last_call_batch

    @property
    def queued_job_count(self) -> int:
        return self._queue_depth

    @property
    def active_job_kind(self) -> str | None:
        return self._active_job_kind

    @property
    def active_job_id(self) -> str | None:
        return self._active_job_id

    @property
    def can_send_sms(self) -> bool:
        return bool(self._service_flags.get("send_sms", True))

    @property
    def can_send_unicode_sms(self) -> bool:
        return bool(self._service_flags.get("send_sms_unicode", True))

    @property
    def can_place_calls(self) -> bool:
        return bool(self._service_flags.get("call", True))

    @property
    def can_send_with_default_encoding(self) -> bool:
        mode = normalize_encoding(self.sms_encoding)
        if mode == "ucs2":
            return self.can_send_unicode_sms or self.can_send_sms
        return self.can_send_sms

    @property
    def ha_device_info(self) -> HADeviceInfo:
        return HADeviceInfo(
            identifiers={(DOMAIN, self.unique_id_prefix)},
            name=self.device_name,
            manufacturer="Q-Tronic",
            model="SMS Gateway (Add-on backend)",
            configuration_url=self.addon_base_url,
        )

    def entity_info_for_role(self, role: str) -> GatewayEntityInfo | None:
        return ENTITY_INFOS.get(role)

    def state_for_role(self, role: str) -> Any:
        return self._states.get(role)

    def notify_unique_id_for_recipient(self, recipient_id: str) -> str:
        return f"{self.unique_id_prefix}_recipient_notify_{recipient_id}"

    def saved_recipient_for_notify_unique_id(self, unique_id: str) -> SavedRecipient | None:
        marker = "_recipient_notify_"
        if marker not in unique_id:
            return None
        return self.saved_recipient_map.get(unique_id.split(marker, 1)[1])

    async def async_start(self) -> None:
        self._stop_event.clear()
        _LOGGER.info("Q-Tronic integration using add-on backend at %s", self.addon_base_url)
        await async_sync_restart_issue(self.hass)
        await self._async_refresh_status(require_success=True)
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def async_stop(self) -> None:
        self._stop_event.set()
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None

    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.add(listener)

        def _remove() -> None:
            self._listeners.discard(listener)

        return _remove

    def _notify_listeners(self) -> None:
        for listener in tuple(self._listeners):
            listener()

    async def async_send_sms(self, *, message: str, recipient: str) -> None:
        await self.async_send_sms_batch(message=message, recipients=[recipient])

    async def async_send_sms_batch(
        self,
        *,
        message: str,
        recipients: list[str],
        encoding: str | None = None,
        batch_id: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "message": message,
            "recipients": deduplicate_phone_numbers(recipients),
            "encoding": encoding or self.sms_encoding,
        }
        _LOGGER.info(
            "HTTP SMS batch requested via %s for %s",
            self.addon_base_url,
            [mask_phone_number(phone) for phone in payload["recipients"]],
        )
        result = await self._request_json("post", "/api/send-sms", payload, timeout_s=None)
        await self._async_refresh_status(require_success=False)
        return result

    async def async_call_batch(
        self,
        *,
        recipients: list[str],
        ring_time_s: int | None = None,
        batch_id: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "recipients": deduplicate_phone_numbers(recipients),
            "ring_time_s": int(ring_time_s or self.default_ring_time_s),
        }
        _LOGGER.info(
            "HTTP call batch requested via %s for %s",
            self.addon_base_url,
            [mask_phone_number(phone) for phone in payload["recipients"]],
        )
        result = await self._request_json("post", "/api/call", payload, timeout_s=None)
        await self._async_refresh_status(require_success=False)
        return result

    async def async_hangup(self) -> dict[str, Any]:
        _LOGGER.info("HTTP hangup requested via %s", self.addon_base_url)
        result = await self._request_json("post", "/api/hangup", {}, timeout_s=None)
        await self._async_refresh_status(require_success=False)
        return result

    async def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await asyncio.sleep(POLL_INTERVAL_S)
                await async_sync_restart_issue(self.hass)
                await self._async_refresh_status(require_success=False)
            except asyncio.CancelledError:
                raise
            except Exception as err:  # pragma: no cover - runtime guard
                _LOGGER.warning("Q-Tronic HTTP poll failed: %s", err)

    async def _async_refresh_status(self, *, require_success: bool) -> None:
        try:
            status = await self._request_json("get", "/api/status", timeout_s=15)
        except GatewayConnectionError:
            self.available = False
            self._notify_listeners()
            if require_success:
                raise
            return

        self._apply_status_payload(status)

    def _apply_status_payload(self, payload: dict[str, Any]) -> None:
        payload_hash = repr(payload)
        if self._recent_status_hashes and self._recent_status_hashes[-1] == payload_hash:
            self.available = bool(payload.get("available", False))
            return

        self._recent_status_hashes.append(payload_hash)
        self.available = bool(payload.get("available", False))
        self.device_name = (
            payload.get("device", {}).get("name")
            or self.entry.title
            or "Q-Tronic SMS Gateway"
        )
        self._last_connect_error = payload.get("last_connect_error")
        self._queue_depth = int(payload.get("queue_depth") or 0)
        self._active_job_kind = payload.get("active_job_kind")
        self._active_job_id = payload.get("active_job_id")
        self._service_flags = {
            "send_sms": bool(payload.get("services", {}).get("send_sms", True)),
            "send_sms_unicode": bool(
                payload.get("services", {}).get("send_sms_unicode", True)
            ),
            "call": bool(payload.get("services", {}).get("call", True)),
        }
        self._states = dict(payload.get("states") or {})

        recipients_payload = payload.get("saved_recipients") or []
        addon_recipients: list[SavedRecipient] = []
        for item in recipients_payload:
            if not isinstance(item, dict):
                continue
            recipient_id = str(item.get("id", "")).strip()
            name = str(item.get("name", "")).strip()
            phone = str(item.get("phone", "")).strip()
            if recipient_id and name and phone:
                addon_recipients.append(
                    SavedRecipient(id=recipient_id, name=name, phone=phone)
                )
        self._addon_saved_recipients = tuple(addon_recipients)

        last_sms = payload.get("last_sms_batch") or {}
        self._last_sms_batch = SmsBatchDiagnostics(
            status=str(last_sms.get("status", "idle")),
            batch_id=last_sms.get("batch_id"),
            gateway_host=payload.get("host") or self.host,
            started_at=last_sms.get("started_at"),
            finished_at=last_sms.get("finished_at"),
            recipient_count=int(last_sms.get("recipient_count") or len(last_sms.get("recipients") or [])),
            recipients=tuple(str(item) for item in (last_sms.get("recipients") or [])),
            completed_recipients=tuple(
                str(item) for item in (last_sms.get("completed_recipients") or [])
            ),
            failed_recipient=last_sms.get("failed_recipient"),
            last_error=last_sms.get("last_error"),
            encoding=last_sms.get("encoding"),
            message_length=int(last_sms.get("message_length") or 0),
        )

        last_call = payload.get("last_call_batch") or {}
        attempts_payload = last_call.get("attempts") or {}
        attempts_items = (
            attempts_payload.items()
            if isinstance(attempts_payload, dict)
            else attempts_payload
            if isinstance(attempts_payload, list)
            else []
        )
        self._last_call_batch = CallBatchDiagnostics(
            status=str(last_call.get("status", "idle")),
            batch_id=last_call.get("batch_id"),
            gateway_host=payload.get("host") or self.host,
            started_at=last_call.get("started_at"),
            finished_at=last_call.get("finished_at"),
            recipient_count=int(
                last_call.get("recipient_count") or len(last_call.get("recipients") or [])
            ),
            recipients=tuple(str(item) for item in (last_call.get("recipients") or [])),
            completed_recipients=tuple(
                str(item) for item in (last_call.get("completed_recipients") or [])
            ),
            failed_recipients=tuple(
                str(item) for item in (last_call.get("failed_recipients") or [])
            ),
            unknown_recipients=tuple(
                str(item) for item in (last_call.get("unknown_recipients") or [])
            ),
            failed_recipient=last_call.get("failed_recipient"),
            last_error=last_call.get("last_error"),
            ring_time_s=int(last_call.get("ring_time_s") or 0),
            attempts=tuple((str(name), int(count)) for name, count in attempts_items),
            state_tracking_available=bool(last_call.get("state_tracking_available", True)),
        )
        self._notify_listeners()

    async def _request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout_s: float | None = 15,
    ) -> dict[str, Any]:
        session = async_get_clientsession(self.hass)
        url = f"{self.addon_base_url.rstrip('/')}{path}"
        timeout = aiohttp.ClientTimeout(total=timeout_s)
        try:
            async with session.request(
                method.upper(),
                url,
                json=payload,
                timeout=timeout,
            ) as response:
                body = await response.json(content_type=None)
                if response.status == 401 or response.status == 403:
                    raise GatewayAuthenticationError(f"HTTP {response.status} from add-on backend")
                if response.status >= 400:
                    detail = body.get("detail") if isinstance(body, dict) else None
                    raise GatewayConnectionError(detail or f"HTTP {response.status} from add-on backend")
                return body if isinstance(body, dict) else {}
        except GatewayAuthenticationError:
            raise
        except aiohttp.ClientError as err:
            raise GatewayConnectionError(
                f"Cannot reach Q-Tronic add-on backend at {url}: {err}"
            ) from err


async def validate_gateway_connection(data: dict[str, Any]) -> GatewayValidationInfo:
    """Validate that the HTTP add-on backend can be reached."""
    host = str(data.get(CONF_HOST, "")).strip()
    port = int(data.get(CONF_PORT, ADDON_HTTP_PORT))
    if not host:
        raise GatewayConnectionError("Host is required.")

    url = f"http://{host}:{port}/health"
    timeout = aiohttp.ClientTimeout(total=15)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as response:
                if response.status >= 400:
                    raise GatewayConnectionError(
                        f"HTTP {response.status} returned from Q-Tronic add-on backend."
                    )
                payload = await response.json(content_type=None)
    except aiohttp.ClientError as err:
        raise GatewayConnectionError(
            f"Cannot connect to the Q-Tronic add-on backend at {url}"
        ) from err

    if not isinstance(payload, dict) or payload.get("status") != "ok":
        raise GatewayConnectionError("The Q-Tronic add-on backend did not return a healthy response.")

    return GatewayValidationInfo(name="Q-Tronic SMS Gateway", mac_address="")
