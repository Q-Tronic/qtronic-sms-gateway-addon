"""Forward inbound SMS messages and calls without creating self-echo loops."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import re
from string import Formatter
from typing import Any

from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .const import (
    CONF_FORWARD_CALLS_ENABLED,
    CONF_FORWARD_CALL_TEMPLATE,
    CONF_FORWARD_COMMAND_MESSAGES,
    CONF_FORWARD_EXCLUDED_NUMBERS,
    CONF_FORWARD_EXCLUDED_RECIPIENT_IDS,
    CONF_FORWARD_RECIPIENT_IDS,
    CONF_FORWARD_SMS_ENABLED,
    CONF_FORWARD_SMS_TEMPLATE,
    EVENT_ATTR_CALLER,
    EVENT_ATTR_MESSAGE,
    EVENT_ATTR_SAVED_RECIPIENT_ID,
    EVENT_ATTR_SAVED_RECIPIENT_NAME,
    EVENT_ATTR_SENDER,
    EVENT_INCOMING_CALL,
    EVENT_SMS_RECEIVED,
)
from .event_source import event_belongs_to_hub
from .hub import QTronicSmsGatewayHub
from .recipients import (
    mask_phone_number,
    normalize_phone_number,
    phone_numbers_match,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_FORWARD_SMS_TEMPLATE = "{data_czas}\nOD: {nadawca}\nSMS: {wiadomosc}"
DEFAULT_FORWARD_CALL_TEMPLATE = "{data_czas}\nPOŁĄCZENIE OD: {dzwoniacy}"

FORWARD_TEMPLATE_FIELDS = frozenset(
    {
        "data_czas",
        "nadawca",
        "nazwa_nadawcy",
        "wiadomosc",
        "dzwoniacy",
        "typ",
    }
)

_PHONE_LIST_SEPARATOR = re.compile(r"[,;\r\n]+")


@dataclass(frozen=True, slots=True)
class ForwardingSettings:
    """Validated forwarding options for one config entry."""

    sms_enabled: bool
    calls_enabled: bool
    forward_command_messages: bool
    recipient_ids: tuple[str, ...]
    excluded_recipient_ids: frozenset[str]
    excluded_numbers: tuple[str, ...]
    sms_template: str
    call_template: str


def validate_forward_template(template: str) -> None:
    """Validate placeholders supported by forwarding templates."""
    try:
        parsed = list(Formatter().parse(template))
        fields = {field_name for _, field_name, _, _ in parsed if field_name}
    except ValueError as err:
        raise ValueError("Invalid forwarding template syntax.") from err
    unknown = fields - FORWARD_TEMPLATE_FIELDS
    if unknown:
        raise ValueError(f"Unsupported forwarding template field: {sorted(unknown)[0]}")
    if any(field_name == "" for _, field_name, _, _ in parsed):
        raise ValueError("Positional forwarding template fields are not supported.")
    if any(format_spec or conversion for _, _, format_spec, conversion in parsed):
        raise ValueError("Forwarding template formatting modifiers are not supported.")


def parse_forward_excluded_numbers(raw_value: Any) -> tuple[str, ...]:
    """Parse newline, comma, or semicolon separated excluded phone numbers."""
    if isinstance(raw_value, (list, tuple)):
        raw_items = [str(item) for item in raw_value]
    else:
        raw_items = _PHONE_LIST_SEPARATOR.split(str(raw_value or ""))

    normalized: list[str] = []
    for raw_item in raw_items:
        if not raw_item.strip():
            continue
        phone = normalize_phone_number(raw_item)
        if not any(phone_numbers_match(phone, existing) for existing in normalized):
            normalized.append(phone)
    return tuple(normalized)


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def load_forwarding_settings(options: dict[str, Any]) -> ForwardingSettings:
    """Load forwarding settings defensively from config-entry options."""
    sms_template = str(
        options.get(CONF_FORWARD_SMS_TEMPLATE, DEFAULT_FORWARD_SMS_TEMPLATE)
    ).strip()
    call_template = str(
        options.get(CONF_FORWARD_CALL_TEMPLATE, DEFAULT_FORWARD_CALL_TEMPLATE)
    ).strip()
    try:
        validate_forward_template(sms_template)
    except ValueError:
        sms_template = DEFAULT_FORWARD_SMS_TEMPLATE
    try:
        validate_forward_template(call_template)
    except ValueError:
        call_template = DEFAULT_FORWARD_CALL_TEMPLATE
    try:
        excluded_numbers = parse_forward_excluded_numbers(
            options.get(CONF_FORWARD_EXCLUDED_NUMBERS, ())
        )
    except ValueError:
        excluded_numbers = ()

    return ForwardingSettings(
        sms_enabled=bool(options.get(CONF_FORWARD_SMS_ENABLED, False)),
        calls_enabled=bool(options.get(CONF_FORWARD_CALLS_ENABLED, False)),
        forward_command_messages=bool(
            options.get(CONF_FORWARD_COMMAND_MESSAGES, False)
        ),
        recipient_ids=_string_tuple(options.get(CONF_FORWARD_RECIPIENT_IDS, ())),
        excluded_recipient_ids=frozenset(
            _string_tuple(options.get(CONF_FORWARD_EXCLUDED_RECIPIENT_IDS, ()))
        ),
        excluded_numbers=excluded_numbers,
        sms_template=sms_template or DEFAULT_FORWARD_SMS_TEMPLATE,
        call_template=call_template or DEFAULT_FORWARD_CALL_TEMPLATE,
    )


def render_forward_template(template: str, values: dict[str, str]) -> str:
    """Render a pre-validated forwarding template."""
    validate_forward_template(template)
    return template.format_map(values).strip()


def _event_time_text(event: Event) -> str:
    raw_timestamp = event.data.get("timestamp")
    try:
        timestamp = float(raw_timestamp)
        event_time = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        event_time = event.time_fired
    return dt_util.as_local(event_time).strftime("%d.%m.%Y - %H:%M:%S")


class InboundForwardingEngine:
    """Forward selected inbound events through the gateway SMS queue."""

    def __init__(self, hass: HomeAssistant, hub: QTronicSmsGatewayHub) -> None:
        self.hass = hass
        self.hub = hub
        self.entry = hub.entry
        self._remove_listeners: list[Any] = []
        self._tasks: set[asyncio.Task[Any]] = set()
        self._send_lock = asyncio.Lock()

    @property
    def settings(self) -> ForwardingSettings:
        """Return current forwarding settings."""
        return load_forwarding_settings(dict(self.entry.options))

    async def async_start(self) -> None:
        """Subscribe to enabled inbound event types."""
        settings = self.settings
        if settings.sms_enabled:
            self._remove_listeners.append(
                self.hass.bus.async_listen(
                    EVENT_SMS_RECEIVED, self._handle_inbound_event
                )
            )
        if settings.calls_enabled:
            self._remove_listeners.append(
                self.hass.bus.async_listen(
                    EVENT_INCOMING_CALL, self._handle_inbound_event
                )
            )
        _LOGGER.info(
            "Inbound forwarding loaded: sms=%s calls=%s recipients=%s exclusions=%s",
            settings.sms_enabled,
            settings.calls_enabled,
            len(settings.recipient_ids),
            len(settings.excluded_recipient_ids) + len(settings.excluded_numbers),
        )

    async def async_stop(self) -> None:
        """Unsubscribe and cancel outstanding forwarding tasks."""
        for remove_listener in self._remove_listeners:
            remove_listener()
        self._remove_listeners.clear()
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    @callback
    def _handle_inbound_event(self, event: Event) -> None:
        """Schedule forwarding from the Home Assistant event loop."""
        task = self.entry.async_create_task(
            self.hass,
            self._async_forward_event(event),
            "Q-Tronic forward inbound event",
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _async_forward_event(self, event: Event) -> None:
        if not event_belongs_to_hub(event, self.hub):
            return

        settings = self.settings
        is_sms = event.event_type == EVENT_SMS_RECEIVED
        if (is_sms and not settings.sms_enabled) or (
            not is_sms and not settings.calls_enabled
        ):
            return

        source_phone = str(
            event.data.get(EVENT_ATTR_SENDER if is_sms else EVENT_ATTR_CALLER, "")
        ).strip()
        if not source_phone:
            return
        if is_sms and not settings.forward_command_messages:
            command_engine = getattr(self.hub, "sms_command_engine", None)
            if command_engine is not None and command_engine.matching_rule(
                source_phone,
                str(event.data.get(EVENT_ATTR_MESSAGE, "") or ""),
            ) is not None:
                _LOGGER.info(
                    "Inbound SMS forwarding skipped because the message matched an SMS command rule"
                )
                return
        source_recipient_id = str(
            event.data.get(EVENT_ATTR_SAVED_RECIPIENT_ID, "") or ""
        )
        recipient_map = self.hub.saved_recipient_map
        excluded_saved_match = any(
            recipient_id in recipient_map
            and phone_numbers_match(
                source_phone,
                recipient_map[recipient_id].phone,
            )
            for recipient_id in settings.excluded_recipient_ids
        )
        if (
            source_recipient_id in settings.excluded_recipient_ids
            or excluded_saved_match
        ):
            _LOGGER.info(
                "Inbound %s forwarding skipped for excluded saved sender %s",
                "SMS" if is_sms else "call",
                source_recipient_id or mask_phone_number(source_phone),
            )
            return
        if any(
            phone_numbers_match(source_phone, excluded)
            for excluded in settings.excluded_numbers
        ):
            _LOGGER.info(
                "Inbound %s forwarding skipped for excluded number %s",
                "SMS" if is_sms else "call",
                mask_phone_number(source_phone),
            )
            return

        recipients: list[str] = []
        recipient_names: list[str] = []
        for recipient_id in settings.recipient_ids:
            recipient = recipient_map.get(recipient_id)
            if recipient is None:
                continue
            if (
                recipient.id == source_recipient_id
                or phone_numbers_match(recipient.phone, source_phone)
                or any(
                    phone_numbers_match(recipient.phone, existing)
                    for existing in recipients
                )
            ):
                _LOGGER.info(
                    "Anti-echo skipped forwarding inbound %s back to %s",
                    "SMS" if is_sms else "call",
                    recipient.name,
                )
                continue
            recipients.append(recipient.phone)
            recipient_names.append(recipient.name)

        if not recipients:
            _LOGGER.info(
                "Inbound %s was not forwarded because no recipient remained after anti-echo filtering",
                "SMS" if is_sms else "call",
            )
            return

        sender_name = str(
            event.data.get(EVENT_ATTR_SAVED_RECIPIENT_NAME, "") or source_phone
        )
        values = {
            "data_czas": _event_time_text(event),
            "nadawca": source_phone,
            "nazwa_nadawcy": sender_name,
            "wiadomosc": str(event.data.get(EVENT_ATTR_MESSAGE, "") or ""),
            "dzwoniacy": source_phone,
            "typ": "SMS" if is_sms else "POŁĄCZENIE",
        }
        template = settings.sms_template if is_sms else settings.call_template
        message = render_forward_template(template, values)

        try:
            timeout_s = (
                120 * len(recipients)
                + max(0, len(recipients) - 1) * self.hub.send_delay_ms / 1000
            )
            async with self._send_lock:
                async with asyncio.timeout(timeout_s):
                    await self.hub.async_send_sms_batch(
                        message=message,
                        recipients=recipients,
                    )
        except Exception as err:  # pragma: no cover - runtime transport errors
            _LOGGER.warning(
                "Failed to forward inbound %s to %s: %s",
                "SMS" if is_sms else "call",
                recipient_names,
                err,
            )
            return

        _LOGGER.info(
            "Forwarded inbound %s from %s to %s",
            "SMS" if is_sms else "call",
            sender_name,
            recipient_names,
        )
