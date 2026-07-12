"""Inbound SMS command rules for Q-Tronic SMS Gateway."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import logging
from string import Formatter
from typing import Any
from uuid import uuid4

from homeassistant.const import ATTR_ENTITY_ID, ATTR_FRIENDLY_NAME, STATE_OFF, STATE_ON
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError

from .const import (
    EVENT_ATTR_GATEWAY_HOST,
    EVENT_ATTR_MESSAGE,
    EVENT_ATTR_SAVED_RECIPIENT_ID,
    EVENT_ATTR_SENDER,
    EVENT_SMS_COMMAND_EXECUTED,
    EVENT_SMS_COMMAND_FAILED,
    EVENT_SMS_RECEIVED,
    SMS_RULE_ACTION,
    SMS_RULE_ACTION_REPORT_STATE,
    SMS_RULE_ACTION_TOGGLE,
    SMS_RULE_ACTION_TURN_OFF,
    SMS_RULE_ACTION_TURN_ON,
    SMS_RULE_ACTIONS,
    SMS_RULE_COMMAND,
    SMS_RULE_ENABLED,
    SMS_RULE_ENTITY_ID,
    SMS_RULE_FAILURE_REPLY,
    SMS_RULE_ID,
    SMS_RULE_MATCH_CONTAINS,
    SMS_RULE_MATCH_EXACT,
    SMS_RULE_MATCH_MODE,
    SMS_RULE_MATCH_MODES,
    SMS_RULE_MATCH_STARTS_WITH,
    SMS_RULE_NAME,
    SMS_RULE_REPLY_ENABLED,
    SMS_RULE_SAVED_RECIPIENT_ID,
    SMS_RULE_SENDER_MANUAL,
    SMS_RULE_SENDER_MODE,
    SMS_RULE_SENDER_MODES,
    SMS_RULE_SENDER_PHONE,
    SMS_RULE_SENDER_SAVED,
    SMS_RULE_SUCCESS_REPLY,
)
from .hub import QTronicSmsGatewayHub
from .event_source import event_belongs_to_hub
from .recipients import (
    SavedRecipient,
    mask_phone_number,
    normalize_phone_number,
    phone_numbers_match,
)
from .sms import normalize_inbound_text

_LOGGER = logging.getLogger(__name__)

DEFAULT_SUCCESS_REPLY = "Wykonano: {nazwa_encji} = {stan}"
DEFAULT_STATE_REPLY = "{nazwa_encji}: {stan} {jednostka}"
DEFAULT_FAILURE_REPLY = "Nie udało się wykonać polecenia dla {nazwa_encji}."

REPLY_TEMPLATE_FIELDS = frozenset(
    {
        "zmienna",
        "stan",
        "jednostka",
        "nazwa_encji",
        "entity_id",
        "nadawca",
        "komenda",
    }
)

CONTROL_ENTITY_DOMAINS = frozenset(
    {
        "fan",
        "input_boolean",
        "light",
        "switch",
    }
)


@dataclass(frozen=True, slots=True)
class SmsCommandRule:
    """One persisted inbound SMS command rule."""

    id: str
    name: str
    enabled: bool
    sender_mode: str
    saved_recipient_id: str
    sender_phone: str
    command: str
    match_mode: str
    action: str
    entity_id: str
    reply_enabled: bool
    success_reply: str
    failure_reply: str

    def as_dict(self) -> dict[str, Any]:
        """Serialize the rule into config-entry-safe data."""
        return asdict(self)


def validate_reply_template(template: str) -> None:
    """Validate placeholders used by an SMS reply template."""
    try:
        parsed = list(Formatter().parse(template))
        fields = {field_name for _, field_name, _, _ in parsed if field_name}
    except ValueError as err:
        raise ValueError("Invalid reply template syntax.") from err
    unknown = fields - REPLY_TEMPLATE_FIELDS
    if unknown:
        raise ValueError(f"Unsupported reply template field: {sorted(unknown)[0]}")
    if any(field_name == "" for _, field_name, _, _ in parsed):
        raise ValueError("Positional reply template fields are not supported.")
    if any(format_spec or conversion for _, _, format_spec, conversion in parsed):
        raise ValueError("Reply template formatting modifiers are not supported.")


def _clean_rule(item: dict[str, Any]) -> SmsCommandRule | None:
    """Load and validate one persisted rule."""
    try:
        rule_id = str(item.get(SMS_RULE_ID) or uuid4().hex[:10]).strip()
        name = str(item.get(SMS_RULE_NAME, "")).strip()
        sender_mode = str(item.get(SMS_RULE_SENDER_MODE, SMS_RULE_SENDER_MANUAL))
        saved_recipient_id = str(item.get(SMS_RULE_SAVED_RECIPIENT_ID, "")).strip()
        raw_phone = str(item.get(SMS_RULE_SENDER_PHONE, "")).strip()
        sender_phone = normalize_phone_number(raw_phone) if raw_phone else ""
        command = " ".join(str(item.get(SMS_RULE_COMMAND, "")).split())
        match_mode = str(item.get(SMS_RULE_MATCH_MODE, SMS_RULE_MATCH_EXACT))
        action = str(item.get(SMS_RULE_ACTION, SMS_RULE_ACTION_TOGGLE))
        entity_id = str(item.get(SMS_RULE_ENTITY_ID, "")).strip()
        reply_enabled = bool(item.get(SMS_RULE_REPLY_ENABLED, True))
        success_reply = str(item.get(SMS_RULE_SUCCESS_REPLY, DEFAULT_SUCCESS_REPLY)).strip()
        failure_reply = str(item.get(SMS_RULE_FAILURE_REPLY, DEFAULT_FAILURE_REPLY)).strip()
        validate_reply_template(success_reply)
        if failure_reply:
            validate_reply_template(failure_reply)
    except (TypeError, ValueError):
        return None

    if (
        not rule_id
        or not name
        or not command
        or not entity_id
        or sender_mode not in SMS_RULE_SENDER_MODES
        or match_mode not in SMS_RULE_MATCH_MODES
        or action not in SMS_RULE_ACTIONS
    ):
        return None
    if sender_mode == SMS_RULE_SENDER_SAVED and not saved_recipient_id:
        return None
    if sender_mode == SMS_RULE_SENDER_MANUAL and not sender_phone:
        return None
    if action == SMS_RULE_ACTION_REPORT_STATE:
        reply_enabled = True

    return SmsCommandRule(
        id=rule_id,
        name=name,
        enabled=bool(item.get(SMS_RULE_ENABLED, True)),
        sender_mode=sender_mode,
        saved_recipient_id=saved_recipient_id,
        sender_phone=sender_phone,
        command=command,
        match_mode=match_mode,
        action=action,
        entity_id=entity_id,
        reply_enabled=reply_enabled,
        success_reply=success_reply or DEFAULT_SUCCESS_REPLY,
        failure_reply=failure_reply,
    )


def load_sms_command_rules(raw_value: Any) -> tuple[SmsCommandRule, ...]:
    """Load SMS command rules from config entry options."""
    if not isinstance(raw_value, list):
        return ()
    rules: list[SmsCommandRule] = []
    seen_ids: set[str] = set()
    for item in raw_value:
        if not isinstance(item, dict):
            continue
        rule = _clean_rule(item)
        if rule is None or rule.id in seen_ids:
            continue
        seen_ids.add(rule.id)
        rules.append(rule)
    return tuple(rules)


def serialize_sms_command_rules(
    rules: tuple[SmsCommandRule, ...] | list[SmsCommandRule],
) -> list[dict[str, Any]]:
    """Serialize rules for config entry options."""
    return [rule.as_dict() for rule in rules]


def make_sms_rule_id() -> str:
    """Create a stable unique identifier for a new rule."""
    return uuid4().hex[:10]


def sms_rule_matches_message(rule: SmsCommandRule, message: str) -> bool:
    """Match a normalized, potentially multi-word SMS command."""
    received = normalize_inbound_text(message)
    expected = normalize_inbound_text(rule.command)
    if rule.match_mode == SMS_RULE_MATCH_EXACT:
        return received == expected
    if rule.match_mode == SMS_RULE_MATCH_CONTAINS:
        return expected in received
    if rule.match_mode == SMS_RULE_MATCH_STARTS_WITH:
        return received.startswith(expected)
    return False


def sms_rule_matches_sender(
    rule: SmsCommandRule,
    sender: str,
    saved_recipients: dict[str, SavedRecipient],
) -> bool:
    """Match an inbound sender against a saved or manual rule sender."""
    if rule.sender_mode == SMS_RULE_SENDER_SAVED:
        recipient = saved_recipients.get(rule.saved_recipient_id)
        return recipient is not None and phone_numbers_match(recipient.phone, sender)
    return phone_numbers_match(rule.sender_phone, sender)


def _localized_state(state: State) -> str:
    """Return a concise Polish representation for common HA states."""
    value = state.state
    domain = state.entity_id.partition(".")[0]
    device_class = str(state.attributes.get("device_class", ""))

    if value == "unavailable":
        return "niedostępna"
    if value == "unknown":
        return "nieznany"
    if domain == "cover" or device_class in {"door", "garage_door", "opening", "window"}:
        return {
            "open": "otwarta",
            "closed": "zamknięta",
            "opening": "otwierana",
            "closing": "zamykana",
            STATE_ON: "otwarta",
            STATE_OFF: "zamknięta",
        }.get(value, value)
    if domain == "lock":
        return {"locked": "zamknięty", "unlocked": "otwarty"}.get(value, value)
    if domain == "binary_sensor":
        binary_labels = {
            "connectivity": ("połączony", "rozłączony"),
            "motion": ("wykryto ruch", "brak ruchu"),
            "occupancy": ("zajęte", "wolne"),
            "presence": ("obecność", "brak obecności"),
            "moisture": ("mokro", "sucho"),
            "smoke": ("wykryto dym", "brak dymu"),
            "problem": ("wykryto problem", "brak problemu"),
        }
        if device_class in binary_labels:
            on_label, off_label = binary_labels[device_class]
            if value == STATE_ON:
                return on_label
            if value == STATE_OFF:
                return off_label
            return value
    if value == STATE_ON:
        return "włączony"
    if value == STATE_OFF:
        return "wyłączony"
    return value


def reply_template_values(
    state: State,
    *,
    sender_name: str,
    command: str,
) -> dict[str, str]:
    """Build all supported reply variables for an entity state."""
    unit = str(state.attributes.get("unit_of_measurement", "") or "")
    entity_name = str(state.attributes.get(ATTR_FRIENDLY_NAME) or state.entity_id)
    return {
        "zmienna": state.state,
        "stan": _localized_state(state),
        "jednostka": unit,
        "nazwa_encji": entity_name,
        "entity_id": state.entity_id,
        "nadawca": sender_name,
        "komenda": command,
    }


def render_reply_template(template: str, values: dict[str, str]) -> str:
    """Render a pre-validated SMS reply template."""
    validate_reply_template(template)
    return template.format_map(values).strip()


class SmsCommandRuleEngine:
    """Listen for inbound SMS events and execute the first matching rule."""

    def __init__(self, hass: HomeAssistant, hub: QTronicSmsGatewayHub) -> None:
        self.hass = hass
        self.hub = hub
        self.entry = hub.entry
        self._remove_listener = None
        self._execution_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[Any]] = set()

    @property
    def rules(self) -> tuple[SmsCommandRule, ...]:
        """Return the current rules from config entry options."""
        from .const import CONF_SMS_COMMAND_RULES

        return load_sms_command_rules(
            self.entry.options.get(CONF_SMS_COMMAND_RULES, [])
        )

    async def async_start(self) -> None:
        """Start listening for inbound SMS events."""
        self._remove_listener = self.hass.bus.async_listen(
            EVENT_SMS_RECEIVED, self._handle_sms_event
        )
        _LOGGER.info("Loaded %s inbound SMS command rules", len(self.rules))

    async def async_stop(self) -> None:
        """Stop listening for inbound SMS events."""
        if self._remove_listener is not None:
            self._remove_listener()
            self._remove_listener = None
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    @callback
    def _handle_sms_event(self, event: Event) -> None:
        """Schedule rule processing from the Home Assistant event loop."""
        task = self.entry.async_create_task(
            self.hass,
            self._async_process_sms(event),
            "Q-Tronic process inbound SMS command",
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def matching_rule(self, sender: str, message: str) -> SmsCommandRule | None:
        """Return the first enabled rule matching a sender and message."""
        for rule in self.rules:
            if not rule.enabled:
                continue
            if not sms_rule_matches_sender(
                rule,
                sender,
                self.hub.saved_recipient_map,
            ):
                continue
            if sms_rule_matches_message(rule, message):
                return rule
        return None

    def _event_belongs_to_this_entry(self, event: Event) -> bool:
        return event_belongs_to_hub(event, self.hub)

    async def _async_process_sms(self, event: Event) -> None:
        if not self._event_belongs_to_this_entry(event):
            return
        sender = str(event.data.get(EVENT_ATTR_SENDER, "")).strip()
        message = str(event.data.get(EVENT_ATTR_MESSAGE, "")).strip()
        saved_recipient_id = str(
            event.data.get(EVENT_ATTR_SAVED_RECIPIENT_ID, "") or ""
        )
        if not sender or not message:
            return

        rule = self.matching_rule(sender, message)
        if rule is None:
            return
        async with self._execution_lock:
            await self._async_execute_rule(
                rule, sender, message, saved_recipient_id, event
            )

    async def _async_execute_rule(
        self,
        rule: SmsCommandRule,
        sender: str,
        message: str,
        saved_recipient_id: str,
        event: Event,
    ) -> None:
        state = self.hass.states.get(rule.entity_id)
        sender_recipient = self.hub.saved_recipient_map.get(saved_recipient_id)
        if sender_recipient is None and rule.saved_recipient_id:
            sender_recipient = self.hub.saved_recipient_map.get(
                rule.saved_recipient_id
            )
        sender_name = sender_recipient.name if sender_recipient else mask_phone_number(sender)

        if state is None:
            await self._async_rule_failed(
                rule, sender, message, sender_name, "Target entity was not found."
            )
            return

        try:
            if rule.action != SMS_RULE_ACTION_REPORT_STATE:
                domain = rule.entity_id.partition(".")[0]
                if (
                    rule.action == SMS_RULE_ACTION_TOGGLE
                    and state.state not in {STATE_ON, STATE_OFF}
                ):
                    raise HomeAssistantError(
                        "Cannot toggle an entity whose current state is not on or off."
                    )
                if domain not in CONTROL_ENTITY_DOMAINS:
                    raise HomeAssistantError(
                        f"Entity domain '{domain}' cannot be controlled by this rule."
                    )
                if not self.hass.services.has_service(domain, rule.action):
                    raise HomeAssistantError(
                        f"Entity domain '{domain}' does not provide service '{rule.action}'."
                    )
                async with asyncio.timeout(30):
                    await self.hass.services.async_call(
                        domain,
                        rule.action,
                        {ATTR_ENTITY_ID: rule.entity_id},
                        blocking=True,
                        context=event.context,
                    )
                state = await self._async_wait_for_expected_state(
                    rule, state
                )

            values = reply_template_values(
                state,
                sender_name=sender_name,
                command=message,
            )
            reply_template = (
                rule.success_reply
                or (
                    DEFAULT_STATE_REPLY
                    if rule.action == SMS_RULE_ACTION_REPORT_STATE
                    else DEFAULT_SUCCESS_REPLY
                )
            )
            reply = render_reply_template(reply_template, values)

            self.hass.bus.async_fire(
                EVENT_SMS_COMMAND_EXECUTED,
                {
                    "config_entry_id": self.entry.entry_id,
                    "rule_id": rule.id,
                    "rule_name": rule.name,
                    "entity_id": rule.entity_id,
                    "action": rule.action,
                    EVENT_ATTR_GATEWAY_HOST: self.hub.gateway_host,
                },
            )
            _LOGGER.info(
                "SMS command rule '%s' executed for %s on %s",
                rule.name,
                sender_name,
                rule.entity_id,
            )
            if rule.reply_enabled or rule.action == SMS_RULE_ACTION_REPORT_STATE:
                try:
                    async with asyncio.timeout(120):
                        await self.hub.async_send_sms(
                            message=reply, recipient=sender
                        )
                except Exception as reply_err:  # pragma: no cover - transport errors
                    _LOGGER.warning(
                        "Rule '%s' succeeded, but its SMS reply failed: %s",
                        rule.name,
                        reply_err,
                    )
        except Exception as err:  # pragma: no cover - runtime service errors
            await self._async_rule_failed(
                rule, sender, message, sender_name, str(err), state
            )

    async def _async_wait_for_expected_state(
        self,
        rule: SmsCommandRule,
        previous_state: State,
    ) -> State:
        """Wait briefly for a controlled entity to reach its expected state."""
        expected: str | None = None
        if rule.action == SMS_RULE_ACTION_TURN_ON:
            expected = STATE_ON
        elif rule.action == SMS_RULE_ACTION_TURN_OFF:
            expected = STATE_OFF
        elif rule.action == SMS_RULE_ACTION_TOGGLE:
            if previous_state.state == STATE_ON:
                expected = STATE_OFF
            elif previous_state.state == STATE_OFF:
                expected = STATE_ON

        for _ in range(60):
            current = self.hass.states.get(rule.entity_id)
            if current is not None and (expected is None or current.state == expected):
                return current
            await asyncio.sleep(0.25)
        current = self.hass.states.get(rule.entity_id)
        if current is None:
            raise HomeAssistantError("Target entity disappeared after the action.")
        if expected is not None and current.state != expected:
            raise HomeAssistantError(
                f"Entity did not reach expected state '{expected}'."
            )
        return current

    async def _async_rule_failed(
        self,
        rule: SmsCommandRule,
        sender: str,
        message: str,
        sender_name: str,
        error: str,
        state: State | None = None,
    ) -> None:
        self.hass.bus.async_fire(
            EVENT_SMS_COMMAND_FAILED,
            {
                "config_entry_id": self.entry.entry_id,
                "rule_id": rule.id,
                "rule_name": rule.name,
                "entity_id": rule.entity_id,
                "action": rule.action,
                "error": error,
            },
        )
        _LOGGER.warning("SMS command rule '%s' failed: %s", rule.name, error)
        if not rule.failure_reply:
            return
        try:
            if state is None:
                state = self.hass.states.get(rule.entity_id)
            values = (
                reply_template_values(
                    state,
                    sender_name=sender_name,
                    command=message,
                )
                if state is not None
                else {
                    "zmienna": "nieznana",
                    "stan": "nieznany",
                    "jednostka": "",
                    "nazwa_encji": rule.entity_id,
                    "entity_id": rule.entity_id,
                    "nadawca": sender_name,
                    "komenda": message,
                }
            )
            reply = render_reply_template(rule.failure_reply, values)
            async with asyncio.timeout(120):
                await self.hub.async_send_sms(message=reply, recipient=sender)
        except Exception as reply_err:  # pragma: no cover - runtime transport errors
            _LOGGER.warning(
                "Failed to send error reply for SMS rule '%s': %s",
                rule.name,
                reply_err,
            )
