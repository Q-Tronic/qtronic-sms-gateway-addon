"""Secure inbound SMS command rules for Q-Tronic SMS Gateway."""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
import json
import logging
import re
import secrets
from string import Formatter
from time import monotonic
from typing import Any
from uuid import uuid4

from homeassistant.const import ATTR_ENTITY_ID, ATTR_FRIENDLY_NAME, STATE_OFF, STATE_ON
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .const import (
    CONF_SMS_COMMAND_RULES,
    CONF_SMS_SECURITY_GLOBAL_LIMIT,
    CONF_SMS_SECURITY_LOCKOUT_S,
    CONF_SMS_SECURITY_PIN_FAILURE_LIMIT,
    CONF_SMS_SECURITY_SENDER_LIMIT,
    CONF_SMS_SECURITY_WINDOW_S,
    DEFAULT_SMS_RULE_CHALLENGE_TTL_S,
    DEFAULT_SMS_RULE_COOLDOWN_S,
    DEFAULT_SMS_SECURITY_GLOBAL_LIMIT,
    DEFAULT_SMS_SECURITY_LOCKOUT_S,
    DEFAULT_SMS_SECURITY_PIN_FAILURE_LIMIT,
    DEFAULT_SMS_SECURITY_SENDER_LIMIT,
    DEFAULT_SMS_SECURITY_WINDOW_S,
    EVENT_ATTR_GATEWAY_HOST,
    EVENT_ATTR_MESSAGE,
    EVENT_ATTR_SAVED_RECIPIENT_ID,
    EVENT_ATTR_SENDER,
    EVENT_SMS_COMMAND_EXECUTED,
    EVENT_SMS_COMMAND_FAILED,
    EVENT_SMS_RECEIVED,
    SMS_RULE_ACTION,
    SMS_RULE_ACTION_ACTIVATE_SCENE,
    SMS_RULE_ACTION_ALARM_ARM_AWAY,
    SMS_RULE_ACTION_ALARM_ARM_HOME,
    SMS_RULE_ACTION_ALARM_ARM_NIGHT,
    SMS_RULE_ACTION_ALARM_DISARM,
    SMS_RULE_ACTION_ALARM_TRIGGER,
    SMS_RULE_ACTION_CLOSE_COVER,
    SMS_RULE_ACTION_LOCK,
    SMS_RULE_ACTION_OPEN_COVER,
    SMS_RULE_ACTION_REPORT_STATE,
    SMS_RULE_ACTION_RUN_SCRIPT,
    SMS_RULE_ACTION_SET_VALUE,
    SMS_RULE_ACTION_STOP_COVER,
    SMS_RULE_ACTION_TOGGLE,
    SMS_RULE_ACTION_TURN_OFF,
    SMS_RULE_ACTION_TURN_ON,
    SMS_RULE_ACTION_UNLOCK,
    SMS_RULE_ACTIONS,
    SMS_RULE_CHALLENGE_REQUIRED,
    SMS_RULE_CHALLENGE_TTL_S,
    SMS_RULE_COMMAND,
    SMS_RULE_CONDITION_AFTER,
    SMS_RULE_CONDITION_BEFORE,
    SMS_RULE_CONDITION_ENTITY_ID,
    SMS_RULE_CONDITION_STATE,
    SMS_RULE_COOLDOWN_S,
    SMS_RULE_ENABLED,
    SMS_RULE_ENTITY_ID,
    SMS_RULE_ENTITY_IDS,
    SMS_RULE_FAILURE_REPLY,
    SMS_RULE_ID,
    SMS_RULE_MATCH_CONTAINS,
    SMS_RULE_MATCH_EXACT,
    SMS_RULE_MATCH_MODE,
    SMS_RULE_MATCH_MODES,
    SMS_RULE_MATCH_STARTS_WITH,
    SMS_RULE_NAME,
    SMS_RULE_PIN_HASH,
    SMS_RULE_PIN_REQUIRED,
    SMS_RULE_PRIORITY,
    SMS_RULE_REPLY_ENABLED,
    SMS_RULE_SAVED_RECIPIENT_ID,
    SMS_RULE_SENDER_MANUAL,
    SMS_RULE_SENDER_MODE,
    SMS_RULE_SENDER_MODES,
    SMS_RULE_SENDER_PHONE,
    SMS_RULE_SENDER_SAVED,
    SMS_RULE_SERVICE_DATA,
    SMS_RULE_SUCCESS_REPLY,
)
from .event_source import event_belongs_to_hub
from .hub import QTronicSmsGatewayHub
from .recipients import SavedRecipient, mask_phone_number, normalize_phone_number
from .security import (
    authorization_numbers_match,
    canonical_authorization_number,
    split_trailing_pin,
    verify_pin,
)
from .sms import normalize_inbound_text

_LOGGER = logging.getLogger(__name__)

DEFAULT_SUCCESS_REPLY = "Wykonano: {nazwa_encji} = {stan}"
DEFAULT_STATE_REPLY = "{wynik}"
DEFAULT_FAILURE_REPLY = "Nie udało się wykonać polecenia dla {nazwa_encji}."
CHALLENGE_PREFIX = "potwierdz"

MAX_REPLY_TEMPLATE_ENTITIES = 20
ENTITY_REPLY_TEMPLATE_FIELD_BASES = (
    "zmienna",
    "stan",
    "jednostka",
    "nazwa_encji",
    "entity_id",
    "wynik",
)
_INDEXED_REPLY_TEMPLATE_FIELDS = {
    f"{field}_{index}": index
    for field in ENTITY_REPLY_TEMPLATE_FIELD_BASES
    for index in range(1, MAX_REPLY_TEMPLATE_ENTITIES + 1)
}
REPLY_TEMPLATE_FIELDS = frozenset(
    {
        "data_czas",
        "zmienna",
        "stan",
        "jednostka",
        "nazwa_encji",
        "entity_id",
        "nadawca",
        "komenda",
        "value",
        "wynik",
        "liczba_encji",
        *_INDEXED_REPLY_TEMPLATE_FIELDS,
    }
)

CONTROL_ENTITY_DOMAINS = frozenset(
    {
        "alarm_control_panel",
        "cover",
        "fan",
        "input_boolean",
        "input_number",
        "light",
        "lock",
        "number",
        "scene",
        "script",
        "switch",
        "climate",
    }
)

_ACTION_SERVICE: dict[str, tuple[frozenset[str], str]] = {
    SMS_RULE_ACTION_TURN_ON: (
        frozenset({"fan", "input_boolean", "light", "switch"}),
        "turn_on",
    ),
    SMS_RULE_ACTION_TURN_OFF: (
        frozenset({"fan", "input_boolean", "light", "switch"}),
        "turn_off",
    ),
    SMS_RULE_ACTION_TOGGLE: (
        frozenset({"fan", "input_boolean", "light", "switch"}),
        "toggle",
    ),
    SMS_RULE_ACTION_OPEN_COVER: (frozenset({"cover"}), "open_cover"),
    SMS_RULE_ACTION_CLOSE_COVER: (frozenset({"cover"}), "close_cover"),
    SMS_RULE_ACTION_STOP_COVER: (frozenset({"cover"}), "stop_cover"),
    SMS_RULE_ACTION_LOCK: (frozenset({"lock"}), "lock"),
    SMS_RULE_ACTION_UNLOCK: (frozenset({"lock"}), "unlock"),
    SMS_RULE_ACTION_ALARM_ARM_HOME: (
        frozenset({"alarm_control_panel"}),
        "alarm_arm_home",
    ),
    SMS_RULE_ACTION_ALARM_ARM_AWAY: (
        frozenset({"alarm_control_panel"}),
        "alarm_arm_away",
    ),
    SMS_RULE_ACTION_ALARM_ARM_NIGHT: (
        frozenset({"alarm_control_panel"}),
        "alarm_arm_night",
    ),
    SMS_RULE_ACTION_ALARM_DISARM: (
        frozenset({"alarm_control_panel"}),
        "alarm_disarm",
    ),
    SMS_RULE_ACTION_ALARM_TRIGGER: (
        frozenset({"alarm_control_panel"}),
        "alarm_trigger",
    ),
    SMS_RULE_ACTION_RUN_SCRIPT: (frozenset({"script"}), "turn_on"),
    SMS_RULE_ACTION_ACTIVATE_SCENE: (frozenset({"scene"}), "turn_on"),
}


def action_supports_domain(action: str, domain: str) -> bool:
    """Return whether a rule action is safe for an entity domain."""
    if action == SMS_RULE_ACTION_REPORT_STATE:
        return True
    if action == SMS_RULE_ACTION_SET_VALUE:
        return domain in {"number", "input_number", "climate", "cover"}
    return domain in _ACTION_SERVICE.get(action, (frozenset(), ""))[0]


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
    entity_ids: tuple[str, ...] = ()
    priority: int = 0
    cooldown_s: int = DEFAULT_SMS_RULE_COOLDOWN_S
    pin_hash: str = ""
    pin_required: bool = False
    challenge_required: bool = False
    challenge_ttl_s: int = DEFAULT_SMS_RULE_CHALLENGE_TTL_S
    condition_after: str = ""
    condition_before: str = ""
    condition_entity_id: str = ""
    condition_state: str = ""
    service_data: dict[str, Any] | None = None

    @property
    def targets(self) -> tuple[str, ...]:
        """Return normalized targets while preserving old single-entity rules."""
        if self.entity_ids:
            return self.entity_ids
        return (self.entity_id,) if self.entity_id else ()

    def as_dict(self) -> dict[str, Any]:
        """Serialize the rule into config-entry-safe data."""
        value = asdict(self)
        value[SMS_RULE_ENTITY_IDS] = list(self.targets)
        value[SMS_RULE_ENTITY_ID] = self.targets[0] if self.targets else ""
        return value


@dataclass(frozen=True, slots=True)
class RuleMatch:
    """A rule match and the optional captured command parameter."""

    rule: SmsCommandRule
    value: str = ""
    supplied_pin: str = ""
    command_message: str = ""


@dataclass(slots=True)
class PendingChallenge:
    """One one-time confirmation waiting for an SMS response."""

    code: str
    expires_at: float
    rule: SmsCommandRule
    sender: str
    message: str
    value: str
    saved_recipient_id: str


def validate_reply_template(template: str, *, entity_count: int | None = None) -> None:
    """Validate placeholders used by an SMS reply template."""
    try:
        parsed = list(Formatter().parse(template))
        fields = [field_name for _, field_name, _, _ in parsed if field_name]
    except ValueError as err:
        raise ValueError("Invalid reply template syntax.") from err
    unknown = set(fields) - REPLY_TEMPLATE_FIELDS
    if unknown:
        raise ValueError(f"Unsupported reply template field: {sorted(unknown)[0]}")
    if entity_count is not None:
        for field_name in fields:
            index = _INDEXED_REPLY_TEMPLATE_FIELDS.get(field_name)
            if index is not None and index > entity_count:
                raise ValueError(
                    f"Reply template field '{field_name}' refers to entity {index}, "
                    f"but the rule has {entity_count} target entities."
                )
    if any(field_name == "" for _, field_name, _, _ in parsed):
        raise ValueError("Positional reply template fields are not supported.")
    if any(format_spec or conversion for _, _, format_spec, conversion in parsed):
        raise ValueError("Reply template formatting modifiers are not supported.")


def _parse_entity_ids(item: dict[str, Any]) -> tuple[str, ...]:
    raw = item.get(SMS_RULE_ENTITY_IDS)
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        raw = [item.get(SMS_RULE_ENTITY_ID, "")]
    return tuple(dict.fromkeys(str(value).strip() for value in raw if str(value).strip()))


def _parse_service_data(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        parsed = {str(key): value for key, value in raw.items()}
    elif isinstance(raw, str) and raw.strip():
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("Service data must be a JSON object.")
    else:
        return {}
    forbidden = {ATTR_ENTITY_ID, "code", "password", "pin", "token"}
    if any(key.casefold() in forbidden for key in parsed):
        raise ValueError("Service data contains a protected field.")
    return parsed


def _clean_rule(item: dict[str, Any]) -> SmsCommandRule | None:
    """Load and validate one persisted rule, including legacy records."""
    try:
        targets = _parse_entity_ids(item)
        rule_id = str(item.get(SMS_RULE_ID) or uuid4().hex[:10]).strip()
        name = str(item.get(SMS_RULE_NAME, "")).strip()
        sender_mode = str(item.get(SMS_RULE_SENDER_MODE, SMS_RULE_SENDER_MANUAL))
        saved_recipient_id = str(item.get(SMS_RULE_SAVED_RECIPIENT_ID, "")).strip()
        raw_phone = str(item.get(SMS_RULE_SENDER_PHONE, "")).strip()
        sender_phone = normalize_phone_number(raw_phone) if raw_phone else ""
        command = " ".join(str(item.get(SMS_RULE_COMMAND, "")).split())
        match_mode = str(item.get(SMS_RULE_MATCH_MODE, SMS_RULE_MATCH_EXACT))
        action = str(item.get(SMS_RULE_ACTION, SMS_RULE_ACTION_TOGGLE))
        reply_enabled = bool(item.get(SMS_RULE_REPLY_ENABLED, True))
        success_reply = str(item.get(SMS_RULE_SUCCESS_REPLY, DEFAULT_SUCCESS_REPLY)).strip()
        failure_reply = str(item.get(SMS_RULE_FAILURE_REPLY, DEFAULT_FAILURE_REPLY)).strip()
        priority = max(-1000, min(1000, int(item.get(SMS_RULE_PRIORITY, 0))))
        cooldown_s = max(0, min(86400, int(item.get(SMS_RULE_COOLDOWN_S, DEFAULT_SMS_RULE_COOLDOWN_S))))
        challenge_ttl_s = max(
            30,
            min(900, int(item.get(SMS_RULE_CHALLENGE_TTL_S, DEFAULT_SMS_RULE_CHALLENGE_TTL_S))),
        )
        service_data = _parse_service_data(item.get(SMS_RULE_SERVICE_DATA, {}))
        validate_reply_template(success_reply, entity_count=len(targets))
        if failure_reply:
            validate_reply_template(failure_reply, entity_count=len(targets))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None

    if (
        not rule_id
        or not name
        or not command
        or command.casefold().count("{value}") > 1
        or not targets
        or len(targets) > MAX_REPLY_TEMPLATE_ENTITIES
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
        entity_id=targets[0],
        entity_ids=targets,
        reply_enabled=reply_enabled,
        success_reply=success_reply or DEFAULT_SUCCESS_REPLY,
        failure_reply=failure_reply,
        priority=priority,
        cooldown_s=cooldown_s,
        pin_hash=str(item.get(SMS_RULE_PIN_HASH, "") or ""),
        pin_required=bool(
            item.get(SMS_RULE_PIN_REQUIRED, item.get(SMS_RULE_PIN_HASH, ""))
        ),
        challenge_required=bool(item.get(SMS_RULE_CHALLENGE_REQUIRED, False)),
        challenge_ttl_s=challenge_ttl_s,
        condition_after=str(item.get(SMS_RULE_CONDITION_AFTER, "") or "").strip(),
        condition_before=str(item.get(SMS_RULE_CONDITION_BEFORE, "") or "").strip(),
        condition_entity_id=str(item.get(SMS_RULE_CONDITION_ENTITY_ID, "") or "").strip(),
        condition_state=str(item.get(SMS_RULE_CONDITION_STATE, "") or "").strip(),
        service_data=service_data,
    )


def load_sms_command_rules(raw_value: Any) -> tuple[SmsCommandRule, ...]:
    """Load rules and order them by priority, preserving order for ties."""
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
    return tuple(sorted(rules, key=lambda rule: -rule.priority))


def serialize_sms_command_rules(
    rules: tuple[SmsCommandRule, ...] | list[SmsCommandRule],
) -> list[dict[str, Any]]:
    return [rule.as_dict() for rule in rules]


def make_sms_rule_id() -> str:
    return uuid4().hex[:10]


def _normalized_pattern(rule: SmsCommandRule) -> str:
    return normalize_inbound_text(rule.command)


def match_rule_message(rule: SmsCommandRule, message: str) -> str | None:
    """Return a captured `{value}` or an empty string when a rule matches."""
    received = normalize_inbound_text(message)
    expected = _normalized_pattern(rule)
    marker = "{value}"
    if marker not in expected:
        if rule.match_mode == SMS_RULE_MATCH_EXACT:
            return "" if received == expected else None
        if rule.match_mode == SMS_RULE_MATCH_CONTAINS:
            return "" if expected in received else None
        if rule.match_mode == SMS_RULE_MATCH_STARTS_WITH:
            return "" if received.startswith(expected) else None
        return None

    prefix, suffix = expected.split(marker, 1)
    value_pattern = r"(?P<value>.+?)"
    body = re.escape(prefix) + value_pattern + re.escape(suffix)
    if rule.match_mode == SMS_RULE_MATCH_EXACT:
        expression = "^" + body + "$"
    elif rule.match_mode == SMS_RULE_MATCH_STARTS_WITH:
        expression = "^" + body
    else:
        expression = body
    match = re.search(expression, received)
    return match.group("value").strip() if match else None


def sms_rule_matches_message(rule: SmsCommandRule, message: str) -> bool:
    return match_rule_message(rule, message) is not None


def sms_rule_matches_sender(
    rule: SmsCommandRule,
    sender: str,
    saved_recipients: dict[str, SavedRecipient],
) -> bool:
    """Authorize using the complete normalized number, never a suffix."""
    if rule.sender_mode == SMS_RULE_SENDER_SAVED:
        recipient = saved_recipients.get(rule.saved_recipient_id)
        return recipient is not None and authorization_numbers_match(recipient.phone, sender)
    return authorization_numbers_match(rule.sender_phone, sender)


def find_rule_collisions(rules: tuple[SmsCommandRule, ...] | list[SmsCommandRule]) -> list[tuple[str, str]]:
    """Detect command patterns that can consume the same sender/message."""
    collisions: list[tuple[str, str]] = []
    enabled = [rule for rule in rules if rule.enabled]
    for index, left in enumerate(enabled):
        for right in enabled[index + 1 :]:
            same_sender = (
                left.saved_recipient_id == right.saved_recipient_id
                if left.sender_mode == right.sender_mode == SMS_RULE_SENDER_SAVED
                else authorization_numbers_match(left.sender_phone, right.sender_phone)
            )
            if not same_sender:
                continue
            left_pattern = _normalized_pattern(left).replace("{value}", "")
            right_pattern = _normalized_pattern(right).replace("{value}", "")
            overlaps = (
                left_pattern == right_pattern
                or left_pattern in right_pattern
                or right_pattern in left_pattern
            )
            if overlaps:
                collisions.append((left.id, right.id))
    return collisions


def _localized_state(state: State) -> str:
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
            return on_label if value == STATE_ON else off_label if value == STATE_OFF else value
    if value == STATE_ON:
        return "włączony"
    if value == STATE_OFF:
        return "wyłączony"
    return value


def reply_template_values(
    state: State,
    *,
    data_czas: str,
    sender_name: str,
    command: str,
    value: str = "",
) -> dict[str, str]:
    return reply_template_values_many(
        [state],
        data_czas=data_czas,
        sender_name=sender_name,
        command=command,
        value=value,
        entity_ids=(state.entity_id,),
    )


def _entity_reply_template_values(
    state: State | None, entity_id: str = ""
) -> dict[str, str]:
    """Build one entity slot without losing its selected-list position."""
    if state is None:
        name = entity_id
        return {
            "zmienna": "nieznana",
            "stan": "nieznany",
            "jednostka": "",
            "nazwa_encji": name,
            "entity_id": entity_id,
            "wynik": f"{name}: nieznany" if name else "brak stanu",
        }
    unit = str(state.attributes.get("unit_of_measurement", "") or "")
    name = str(state.attributes.get(ATTR_FRIENDLY_NAME) or state.entity_id)
    localized = _localized_state(state)
    return {
        "zmienna": state.state,
        "stan": localized,
        "jednostka": unit,
        "nazwa_encji": name,
        "entity_id": state.entity_id,
        "wynik": f"{name}: {localized} {unit}".strip(),
    }


def _entity_result_text(values: dict[str, str]) -> str:
    return values["wynik"]


def reply_template_values_many(
    states: list[State | None],
    *,
    data_czas: str,
    sender_name: str,
    command: str,
    value: str,
    entity_ids: tuple[str, ...] | list[str] | None = None,
) -> dict[str, str]:
    selected_entity_ids = list(entity_ids or ())
    slot_count = max(len(states), len(selected_entity_ids))
    selected_entity_ids.extend([""] * (slot_count - len(selected_entity_ids)))
    padded_states = [*states, *([None] * (slot_count - len(states)))]
    slots = [
        _entity_reply_template_values(state, selected_entity_ids[index])
        for index, state in enumerate(padded_states)
    ]

    results = [_entity_result_text(slot) for slot in slots]
    if len(slots) == 1:
        legacy = slots[0]
    elif slots:
        # Keep the pre-0.5.1 meaning of the unnumbered variables for existing
        # multi-entity rules. Numbered variables below are the unambiguous way
        # to address one selected entity.
        legacy = {
            "zmienna": "; ".join(slot["zmienna"] for slot in slots),
            "stan": "; ".join(results),
            "jednostka": "",
            "nazwa_encji": ", ".join(slot["nazwa_encji"] for slot in slots),
            "entity_id": ", ".join(slot["entity_id"] for slot in slots),
            "wynik": "; ".join(results),
        }
    else:
        legacy = _entity_reply_template_values(None)
    values = {
        "data_czas": data_czas,
        **legacy,
        "nadawca": sender_name,
        "komenda": command,
        "value": value,
        "wynik": "; ".join(results) if results else "brak stanu",
        "liczba_encji": str(slot_count),
    }
    # All valid indexed placeholders are always present so an error reply can
    # still render when one target vanished between validation and execution.
    for index in range(1, MAX_REPLY_TEMPLATE_ENTITIES + 1):
        slot = slots[index - 1] if index <= slot_count else None
        for field in ENTITY_REPLY_TEMPLATE_FIELD_BASES:
            values[f"{field}_{index}"] = slot[field] if slot is not None else ""
    return values


def render_reply_template(template: str, values: dict[str, str]) -> str:
    validate_reply_template(template)
    return template.format_map(values).strip()


def _reply_time_text() -> str:
    return dt_util.now().strftime("%d.%m.%Y - %H:%M:%S")


class SmsCommandRuleEngine:
    """Listen for inbound SMS events and execute the highest-priority matching rule."""

    def __init__(self, hass: HomeAssistant, hub: QTronicSmsGatewayHub) -> None:
        self.hass = hass
        self.hub = hub
        self.entry = hub.entry
        self._remove_listener = None
        self._execution_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._global_attempts: deque[float] = deque()
        self._sender_attempts: dict[str, deque[float]] = defaultdict(deque)
        self._pin_failures: dict[str, deque[float]] = defaultdict(deque)
        self._locked_until: dict[str, float] = {}
        self._last_rule_run: dict[tuple[str, str], float] = {}
        self._pending_challenges: dict[tuple[str, str], PendingChallenge] = {}
        self._stats: dict[str, Any] = {
            "status": "idle",
            "received": 0,
            "matched": 0,
            "executed": 0,
            "failed": 0,
            "rate_limited": 0,
            "auth_failed": 0,
            "challenges": 0,
            "last_rule_id": None,
            "last_rule_name": None,
            "last_result": None,
            "last_error": None,
            "last_run": None,
        }

    @property
    def rules(self) -> tuple[SmsCommandRule, ...]:
        return load_sms_command_rules(self.entry.options.get(CONF_SMS_COMMAND_RULES, []))

    @property
    def statistics(self) -> dict[str, Any]:
        return dict(self._stats)

    def _option_int(self, key: str, default: int, minimum: int = 1) -> int:
        try:
            return max(minimum, int(self.entry.options.get(key, default)))
        except (TypeError, ValueError):
            return default

    async def async_start(self) -> None:
        self._remove_listener = self.hass.bus.async_listen(
            EVENT_SMS_RECEIVED, self._handle_sms_event
        )
        _LOGGER.info("Loaded %s inbound SMS command rules", len(self.rules))

    async def async_stop(self) -> None:
        if self._remove_listener is not None:
            self._remove_listener()
            self._remove_listener = None
        for task in tuple(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
        self._tasks.clear()
        self._pending_challenges.clear()

    @callback
    def _handle_sms_event(self, event: Event) -> None:
        task = self.entry.async_create_task(
            self.hass,
            self._async_process_sms(event),
            "Q-Tronic process inbound SMS command",
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _pin_hash_for_rule(self, rule: SmsCommandRule) -> str:
        if rule.pin_required and rule.pin_hash:
            return rule.pin_hash
        recipient = self.hub.saved_recipient_map.get(rule.saved_recipient_id)
        if recipient and recipient.pin_required and recipient.pin_hash:
            return recipient.pin_hash
        return "invalid_required_pin_hash" if rule.pin_required else ""

    def _match(self, sender: str, message: str) -> RuleMatch | None:
        for rule in self.rules:
            if not rule.enabled or not sms_rule_matches_sender(
                rule, sender, self.hub.saved_recipient_map
            ):
                continue
            pin_hash = self._pin_hash_for_rule(rule)
            command_message, supplied_pin = (
                split_trailing_pin(message) if pin_hash else (message, "")
            )
            value = match_rule_message(rule, command_message)
            if value is None and pin_hash:
                # A numeric `{value}` can look like a PIN. Matching the original
                # message makes the command recognizable, while verification below
                # still fails closed because no PIN was supplied.
                value = match_rule_message(rule, message)
                if value is not None:
                    command_message, supplied_pin = message, ""
            if value is not None:
                return RuleMatch(rule, value, supplied_pin, command_message)
        return None

    def matching_rule(self, sender: str, message: str) -> SmsCommandRule | None:
        match = self._match(sender, message)
        return match.rule if match else None

    def dry_run(self, sender: str, message: str) -> dict[str, Any]:
        """Evaluate a message without changing HA state or consuming limits."""
        match = self._match(sender, message)
        if match is None:
            return {"matched": False, "reason": "no_match"}
        pin_hash = self._pin_hash_for_rule(match.rule)
        return {
            "matched": True,
            "rule_id": match.rule.id,
            "rule_name": match.rule.name,
            "action": match.rule.action,
            "targets": list(match.rule.targets),
            "value": match.value,
            "pin_required": bool(pin_hash),
            "pin_valid": bool(pin_hash and verify_pin(match.supplied_pin, pin_hash))
            if pin_hash
            else True,
            "challenge_required": match.rule.challenge_required,
            "conditions_ok": self._conditions_pass(match.rule),
        }

    def _event_belongs_to_this_entry(self, event: Event) -> bool:
        return event_belongs_to_hub(event, self.hub)

    def _prune(self, values: deque[float], now: float, window: int) -> None:
        while values and now - values[0] >= window:
            values.popleft()

    def _consume_rate_limit(self, sender_key: str) -> bool:
        now = monotonic()
        window = self._option_int(
            CONF_SMS_SECURITY_WINDOW_S, DEFAULT_SMS_SECURITY_WINDOW_S
        )
        global_limit = self._option_int(
            CONF_SMS_SECURITY_GLOBAL_LIMIT, DEFAULT_SMS_SECURITY_GLOBAL_LIMIT
        )
        sender_limit = self._option_int(
            CONF_SMS_SECURITY_SENDER_LIMIT, DEFAULT_SMS_SECURITY_SENDER_LIMIT
        )
        sender_values = self._sender_attempts[sender_key]
        self._prune(self._global_attempts, now, window)
        self._prune(sender_values, now, window)
        if len(self._global_attempts) >= global_limit or len(sender_values) >= sender_limit:
            return False
        self._global_attempts.append(now)
        sender_values.append(now)
        return True

    def _sender_is_locked(self, sender_key: str) -> bool:
        until = self._locked_until.get(sender_key, 0)
        if until <= monotonic():
            self._locked_until.pop(sender_key, None)
            return False
        return True

    def _record_pin_failure(self, sender_key: str) -> None:
        now = monotonic()
        window = self._option_int(
            CONF_SMS_SECURITY_WINDOW_S, DEFAULT_SMS_SECURITY_WINDOW_S
        )
        failures = self._pin_failures[sender_key]
        self._prune(failures, now, window)
        failures.append(now)
        failure_limit = self._option_int(
            CONF_SMS_SECURITY_PIN_FAILURE_LIMIT,
            DEFAULT_SMS_SECURITY_PIN_FAILURE_LIMIT,
        )
        if len(failures) >= failure_limit:
            self._locked_until[sender_key] = now + self._option_int(
                CONF_SMS_SECURITY_LOCKOUT_S, DEFAULT_SMS_SECURITY_LOCKOUT_S
            )
            failures.clear()

    def _challenge_response(self, sender_key: str, message: str) -> PendingChallenge | None:
        normalized = normalize_inbound_text(message)
        parts = normalized.split()
        if len(parts) != 2 or parts[0] != CHALLENGE_PREFIX:
            return None
        now = monotonic()
        supplied = parts[1]
        candidates = [
            (key, pending)
            for key, pending in self._pending_challenges.items()
            if key[0] == sender_key
        ]
        for key, pending in candidates:
            if pending.expires_at <= now:
                self._pending_challenges.pop(key, None)
                continue
            if secrets.compare_digest(pending.code, supplied):
                self._pending_challenges.pop(key, None)
                return pending
        if candidates:
            self._record_pin_failure(sender_key)
        return None

    async def _async_process_sms(self, event: Event) -> None:
        if not self._event_belongs_to_this_entry(event):
            return
        sender = str(event.data.get(EVENT_ATTR_SENDER, "")).strip()
        message = str(event.data.get(EVENT_ATTR_MESSAGE, "")).strip()
        saved_recipient_id = str(event.data.get(EVENT_ATTR_SAVED_RECIPIENT_ID, "") or "")
        if not sender or not message:
            return
        self._stats["received"] += 1
        sender_key = canonical_authorization_number(sender)
        if not sender_key or self._sender_is_locked(sender_key):
            self._stats["auth_failed"] += 1
            self._stats["status"] = "locked"
            self._notify_statistics()
            return

        pending = self._challenge_response(sender_key, message)
        if pending is not None:
            if not self._consume_rate_limit(sender_key):
                self._rate_limited(pending.rule)
                return
            async with self._execution_lock:
                last_run = self._last_rule_run.get(
                    (sender_key, pending.rule.id), 0
                )
                if monotonic() - last_run < pending.rule.cooldown_s:
                    self._rate_limited(pending.rule, "cooldown")
                    return
                if not self._conditions_pass(pending.rule):
                    await self._async_rule_failed(
                        pending.rule,
                        sender,
                        pending.message,
                        mask_phone_number(sender),
                        "Rule conditions are not satisfied.",
                        value=pending.value,
                    )
                    return
                await self._async_execute_rule(
                    pending.rule,
                    sender,
                    pending.message,
                    pending.saved_recipient_id,
                    event,
                    pending.value,
                )
            return

        match = self._match(sender, message)
        if match is None:
            return
        self._stats["matched"] += 1
        if not self._consume_rate_limit(sender_key):
            self._rate_limited(match.rule)
            return
        pin_hash = self._pin_hash_for_rule(match.rule)
        if pin_hash and not verify_pin(match.supplied_pin, pin_hash):
            self._record_pin_failure(sender_key)
            self._stats["auth_failed"] += 1
            self._stats["status"] = "auth_failed"
            self._stats["last_error"] = "invalid_pin"
            self._notify_statistics()
            return
        if match.rule.challenge_required:
            code = f"{secrets.randbelow(1_000_000):06d}"
            challenge_key = (sender_key, match.rule.id)
            self._pending_challenges[challenge_key] = PendingChallenge(
                code=code,
                expires_at=monotonic() + match.rule.challenge_ttl_s,
                rule=match.rule,
                sender=sender,
                message=match.command_message,
                value=match.value,
                saved_recipient_id=saved_recipient_id,
            )
            self._stats["challenges"] += 1
            self._stats["status"] = "challenge_pending"
            self._notify_statistics()
            try:
                await self.hub.async_send_sms(
                    message=(
                        f"Potwierdź polecenie: {CHALLENGE_PREFIX.upper()} {code}. "
                        f"Kod jest ważny {match.rule.challenge_ttl_s} s."
                    ),
                    recipient=sender,
                )
            except Exception as err:  # pragma: no cover - transport errors
                self._pending_challenges.pop(challenge_key, None)
                await self._async_rule_failed(
                    match.rule,
                    sender,
                    match.command_message,
                    mask_phone_number(sender),
                    f"Could not send the confirmation challenge: {err}",
                    value=match.value,
                )
            return

        async with self._execution_lock:
            # Cooldown and conditions must be checked while holding the same
            # lock as execution. Two identical inbound events can otherwise
            # both pass the check before either one records its completion.
            last_run = self._last_rule_run.get((sender_key, match.rule.id), 0)
            if monotonic() - last_run < match.rule.cooldown_s:
                self._rate_limited(match.rule, "cooldown")
                return
            if not self._conditions_pass(match.rule):
                await self._async_rule_failed(
                    match.rule,
                    sender,
                    message,
                    mask_phone_number(sender),
                    "Rule conditions are not satisfied.",
                    value=match.value,
                )
                return
            await self._async_execute_rule(
                match.rule, sender, message, saved_recipient_id, event, match.value
            )

    def _rate_limited(self, rule: SmsCommandRule, reason: str = "rate_limit") -> None:
        self._stats["rate_limited"] += 1
        self._stats["status"] = "rate_limited"
        self._stats["last_rule_id"] = rule.id
        self._stats["last_rule_name"] = rule.name
        self._stats["last_error"] = reason
        self._notify_statistics()

    def _conditions_pass(self, rule: SmsCommandRule) -> bool:
        if rule.condition_entity_id:
            state = self.hass.states.get(rule.condition_entity_id)
            if state is None or state.state.casefold() != rule.condition_state.casefold():
                return False
        if rule.condition_after or rule.condition_before:
            now = dt_util.now().time().replace(tzinfo=None)

            def parse(value: str):
                if not value:
                    return None
                try:
                    return dt_util.parse_time(value)
                except (TypeError, ValueError):
                    return None

            after = parse(rule.condition_after)
            before = parse(rule.condition_before)
            if after and before:
                if after <= before and not (after <= now <= before):
                    return False
                if after > before and not (now >= after or now <= before):
                    return False
            elif after and now < after:
                return False
            elif before and now > before:
                return False
        return True

    def _service_for(self, rule: SmsCommandRule, entity_id: str, value: str) -> tuple[str, str, dict[str, Any]]:
        domain = entity_id.partition(".")[0]
        data = dict(rule.service_data or {})
        data.pop(ATTR_ENTITY_ID, None)
        data = {
            key: (item.replace("{value}", value) if isinstance(item, str) else item)
            for key, item in data.items()
        }
        data[ATTR_ENTITY_ID] = entity_id
        if rule.action == SMS_RULE_ACTION_SET_VALUE:
            try:
                numeric_value = float(value.replace(",", "."))
            except ValueError as err:
                raise HomeAssistantError("The command value must be numeric.") from err
            if domain in {"number", "input_number"}:
                data["value"] = numeric_value
                return domain, "set_value", data
            if domain == "climate":
                data["temperature"] = numeric_value
                return domain, "set_temperature", data
            if domain == "cover":
                if not 0 <= numeric_value <= 100:
                    raise HomeAssistantError("Cover position must be between 0 and 100.")
                data["position"] = int(numeric_value)
                return domain, "set_cover_position", data
            raise HomeAssistantError(f"Entity domain '{domain}' cannot accept a numeric value.")
        allowed_domains, service = _ACTION_SERVICE.get(rule.action, (frozenset(), ""))
        if domain not in allowed_domains:
            raise HomeAssistantError(
                f"Action '{rule.action}' is not allowed for entity domain '{domain}'."
            )
        return domain, service, data

    async def _async_execute_rule(
        self,
        rule: SmsCommandRule,
        sender: str,
        message: str,
        saved_recipient_id: str,
        event: Event,
        value: str = "",
    ) -> None:
        states = [self.hass.states.get(entity_id) for entity_id in rule.targets]
        sender_recipient = self.hub.saved_recipient_map.get(saved_recipient_id)
        if sender_recipient is None and rule.saved_recipient_id:
            sender_recipient = self.hub.saved_recipient_map.get(rule.saved_recipient_id)
        sender_name = sender_recipient.name if sender_recipient else mask_phone_number(sender)
        missing = [entity_id for entity_id, state in zip(rule.targets, states, strict=False) if state is None]
        if missing:
            await self._async_rule_failed(
                rule,
                sender,
                message,
                sender_name,
                "Target entities were not found: " + ", ".join(missing),
                value=value,
            )
            return

        current_states = [state for state in states if state is not None]
        try:
            if rule.action != SMS_RULE_ACTION_REPORT_STATE:
                for entity_id, previous_state in zip(rule.targets, current_states, strict=False):
                    if rule.action == SMS_RULE_ACTION_TOGGLE and previous_state.state not in {STATE_ON, STATE_OFF}:
                        raise HomeAssistantError(
                            "Cannot toggle an entity whose current state is not on or off."
                        )
                    domain, service, data = self._service_for(rule, entity_id, value)
                    if not self.hass.services.has_service(domain, service):
                        raise HomeAssistantError(
                            f"Entity domain '{domain}' does not provide service '{service}'."
                        )
                    async with asyncio.timeout(30):
                        await self.hass.services.async_call(
                            domain, service, data, blocking=True, context=event.context
                        )
                    if rule.action in {
                        SMS_RULE_ACTION_TURN_ON,
                        SMS_RULE_ACTION_TURN_OFF,
                        SMS_RULE_ACTION_TOGGLE,
                    }:
                        await self._async_wait_for_expected_state(rule.action, entity_id, previous_state)
                current_states = [
                    self.hass.states.get(entity_id) for entity_id in rule.targets
                ]

            self._last_rule_run[(canonical_authorization_number(sender), rule.id)] = monotonic()
            self._stats["executed"] += 1
            self._stats["status"] = "success"
            self._stats["last_rule_id"] = rule.id
            self._stats["last_rule_name"] = rule.name
            self._stats["last_result"] = "success"
            self._stats["last_error"] = None
            self._stats["last_run"] = dt_util.utcnow().isoformat()
            self._notify_statistics()
            self.hass.bus.async_fire(
                EVENT_SMS_COMMAND_EXECUTED,
                {
                    "config_entry_id": self.entry.entry_id,
                    "rule_id": rule.id,
                    "rule_name": rule.name,
                    "entity_id": rule.entity_id,
                    "entity_ids": list(rule.targets),
                    "action": rule.action,
                    "value": value,
                    EVENT_ATTR_GATEWAY_HOST: self.hub.gateway_host,
                },
            )
            if rule.reply_enabled or rule.action == SMS_RULE_ACTION_REPORT_STATE:
                try:
                    values = reply_template_values_many(
                        current_states,
                        data_czas=_reply_time_text(),
                        sender_name=sender_name,
                        command=message,
                        value=value,
                        entity_ids=rule.targets,
                    )
                    template = rule.success_reply or (
                        DEFAULT_STATE_REPLY
                        if rule.action == SMS_RULE_ACTION_REPORT_STATE
                        else DEFAULT_SUCCESS_REPLY
                    )
                    async with asyncio.timeout(120):
                        await self.hub.async_send_sms(
                            message=render_reply_template(template, values),
                            recipient=sender,
                        )
                except Exception as reply_err:  # pragma: no cover - transport errors
                    _LOGGER.warning(
                        "Rule '%s' succeeded, but its SMS reply failed: %s",
                        rule.name,
                        reply_err,
                    )
        except Exception as err:  # pragma: no cover - runtime service/transport errors
            await self._async_rule_failed(
                rule, sender, message, sender_name, str(err), current_states, value
            )

    async def _async_wait_for_expected_state(
        self, action: str, entity_id: str, previous_state: State
    ) -> State:
        expected: str | None = None
        if action == SMS_RULE_ACTION_TURN_ON:
            expected = STATE_ON
        elif action == SMS_RULE_ACTION_TURN_OFF:
            expected = STATE_OFF
        elif action == SMS_RULE_ACTION_TOGGLE:
            expected = STATE_OFF if previous_state.state == STATE_ON else STATE_ON
        for _ in range(60):
            current = self.hass.states.get(entity_id)
            if current is not None and (expected is None or current.state == expected):
                return current
            await asyncio.sleep(0.25)
        raise HomeAssistantError(f"Entity did not reach expected state '{expected}'.")

    async def _async_rule_failed(
        self,
        rule: SmsCommandRule,
        sender: str,
        message: str,
        sender_name: str,
        error: str,
        states: list[State | None] | State | None = None,
        value: str = "",
    ) -> None:
        self._stats["failed"] += 1
        self._stats["status"] = "failed"
        self._stats["last_rule_id"] = rule.id
        self._stats["last_rule_name"] = rule.name
        self._stats["last_result"] = "failed"
        self._stats["last_error"] = error
        self._stats["last_run"] = dt_util.utcnow().isoformat()
        self._notify_statistics()
        self.hass.bus.async_fire(
            EVENT_SMS_COMMAND_FAILED,
            {
                "config_entry_id": self.entry.entry_id,
                "rule_id": rule.id,
                "rule_name": rule.name,
                "entity_id": rule.entity_id,
                "entity_ids": list(rule.targets),
                "action": rule.action,
                "error": error,
            },
        )
        _LOGGER.warning("SMS command rule '%s' failed: %s", rule.name, error)
        if not rule.failure_reply:
            return
        if isinstance(states, State):
            state_list = [states]
        elif isinstance(states, list):
            state_list = states
        else:
            state_list = [self.hass.states.get(entity_id) for entity_id in rule.targets]
        try:
            values = reply_template_values_many(
                state_list,
                data_czas=_reply_time_text(),
                sender_name=sender_name,
                command=message,
                value=value,
                entity_ids=rule.targets,
            )
            async with asyncio.timeout(120):
                await self.hub.async_send_sms(
                    message=render_reply_template(rule.failure_reply, values),
                    recipient=sender,
                )
        except Exception as reply_err:  # pragma: no cover
            _LOGGER.warning(
                "Failed to send error reply for SMS rule '%s': %s",
                rule.name,
                reply_err,
            )

    def _notify_statistics(self) -> None:
        notify = getattr(self.hub, "notify_listeners", None)
        if notify is not None:
            notify()
