"""Config flow for the Q-Tronic SMS Gateway integration."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from .const import (
    CONF_CALL_FAILURE_ACTION,
    CONF_CALL_MAX_RETRIES,
    CONF_CALL_RETRY_DELAY_S,
    CONF_CALL_RETRY_FOREVER,
    CONF_CALL_STATE_OBJECT_ID,
    CONF_DEFAULT_RECIPIENT,
    CONF_DEFAULT_RECIPIENT_IDS,
    DEFAULT_ADDON_HOSTNAME,
    CONF_DEFAULT_RING_TIME_S,
    CONF_DELAY_BETWEEN_CALLS_S,
    CONF_DIAL_ACTION,
    CONF_DISCONNECT_ACTION,
    CONF_EXPECTED_MAC,
    CONF_EXPECTED_NAME,
    CONF_INCOMING_CALL_OBJECT_ID,
    CONF_MODEM_ONLINE_OBJECT_ID,
    CONF_REGISTERED_OBJECT_ID,
    CONF_RSSI_OBJECT_ID,
    CONF_SAVED_RECIPIENTS,
    CONF_SEND_DELAY_MS,
    CONF_SEND_SMS_ACTION,
    CONF_SMS_ENCODING,
    CONF_SMS_COMMAND_RULES,
    CONF_SMS_MESSAGE_OBJECT_ID,
    CONF_SMS_SENDER_OBJECT_ID,
    CONF_UNICODE_SEND_SMS_ACTION,
    CONF_USSD_OBJECT_ID,
    CONNECTION_KEYS,
    DEFAULT_CALL_FAILURE_ACTION,
    DEFAULT_CALL_MAX_RETRIES,
    DEFAULT_CALL_RETRY_DELAY_S,
    DEFAULT_CALL_RETRY_FOREVER,
    DEFAULT_DELAY_BETWEEN_CALLS_S,
    DEFAULT_DIAL_ACTION,
    DEFAULT_DISCONNECT_ACTION,
    DEFAULT_PORT,
    DEFAULT_RING_TIME_S,
    DEFAULT_SEND_DELAY_MS,
    DEFAULT_SMS_ENCODING,
    DEFAULT_SEND_SMS_ACTION,
    DEFAULT_UNICODE_SEND_SMS_ACTION,
    DOMAIN,
    RESTART_REQUIRED_MARKER,
    SMS_ENCODINGS,
    SMS_RULE_ACTION,
    SMS_RULE_ACTION_REPORT_STATE,
    SMS_RULE_ACTION_TOGGLE,
    SMS_RULE_ACTION_TURN_OFF,
    SMS_RULE_ACTION_TURN_ON,
    SMS_RULE_COMMAND,
    SMS_RULE_ENABLED,
    SMS_RULE_ENTITY_ID,
    SMS_RULE_FAILURE_REPLY,
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
    SMS_RULE_SENDER_PHONE,
    SMS_RULE_SENDER_SAVED,
    SMS_RULE_SUCCESS_REPLY,
)
from .hub import (
    GatewayAuthenticationError,
    GatewayConnectionError,
    normalize_mac,
    validate_gateway_connection,
)
from .recipients import (
    SavedRecipient,
    load_saved_recipients,
    make_recipient_id,
    normalize_phone_number,
    normalize_recipient_name,
    recipient_select_options,
    recipient_summary_lines,
    serialize_saved_recipients,
    phone_numbers_match,
)
from .sms import normalize_inbound_text
from .sms_commands import (
    CONTROL_ENTITY_DOMAINS,
    DEFAULT_STATE_REPLY,
    REPLY_TEMPLATE_FIELDS,
    SmsCommandRule,
    load_sms_command_rules,
    make_sms_rule_id,
    serialize_sms_command_rules,
    validate_reply_template,
)

CONF_RECIPIENT_NAME = "recipient_name"
CONF_RECIPIENT_PHONE = "recipient_phone"
CONF_RECIPIENT_ID = "recipient_id"
CONF_SMS_RULE_ID = "sms_rule_id"


def user_schema(
    defaults: dict[str, Any] | None = None,
    *,
    suggested_host: str = DEFAULT_ADDON_HOSTNAME,
) -> vol.Schema:
    """Build the setup form schema."""
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_HOST,
                default=defaults.get(CONF_HOST, suggested_host),
            ): selector.TextSelector(),
            vol.Required(
                CONF_PORT,
                default=defaults.get(CONF_PORT, DEFAULT_PORT),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1,
                    max=65535,
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
        }
    )


def suggested_addon_host(config_dir: str | Path) -> str:
    """Return the best-known add-on hostname saved by the sync step."""
    marker_path = Path(config_dir) / "custom_components" / DOMAIN / RESTART_REQUIRED_MARKER
    try:
        payload = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return DEFAULT_ADDON_HOSTNAME
    addon_hostname = payload.get("addon_hostname")
    return str(addon_hostname).strip() if addon_hostname else DEFAULT_ADDON_HOSTNAME


def messaging_schema(
    defaults: dict[str, Any],
    saved_recipients: tuple[SavedRecipient, ...],
) -> vol.Schema:
    """Build the SMS behavior form schema."""
    return vol.Schema(
        {
            vol.Optional(
                CONF_DEFAULT_RECIPIENT_IDS,
                default=defaults.get(CONF_DEFAULT_RECIPIENT_IDS, []),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=recipient_select_options(saved_recipients),
                    multiple=True,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(
                CONF_DEFAULT_RECIPIENT,
                default=defaults.get(CONF_DEFAULT_RECIPIENT, ""),
            ): selector.TextSelector(),
            vol.Optional(
                CONF_SEND_SMS_ACTION,
                default=defaults.get(CONF_SEND_SMS_ACTION, DEFAULT_SEND_SMS_ACTION),
            ): selector.TextSelector(),
            vol.Optional(
                CONF_UNICODE_SEND_SMS_ACTION,
                default=defaults.get(
                    CONF_UNICODE_SEND_SMS_ACTION, DEFAULT_UNICODE_SEND_SMS_ACTION
                ),
            ): selector.TextSelector(),
            vol.Optional(
                CONF_SMS_ENCODING,
                default=defaults.get(CONF_SMS_ENCODING, DEFAULT_SMS_ENCODING),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=list(SMS_ENCODINGS),
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(
                CONF_SEND_DELAY_MS,
                default=defaults.get(CONF_SEND_DELAY_MS, DEFAULT_SEND_DELAY_MS),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=30000,
                    step=100,
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
        }
    )


def calling_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Build the calling behavior form schema."""
    return vol.Schema(
        {
            vol.Optional(
                CONF_DIAL_ACTION,
                default=defaults.get(CONF_DIAL_ACTION, DEFAULT_DIAL_ACTION),
            ): selector.TextSelector(),
            vol.Optional(
                CONF_DISCONNECT_ACTION,
                default=defaults.get(CONF_DISCONNECT_ACTION, DEFAULT_DISCONNECT_ACTION),
            ): selector.TextSelector(),
            vol.Optional(
                CONF_DEFAULT_RING_TIME_S,
                default=defaults.get(CONF_DEFAULT_RING_TIME_S, DEFAULT_RING_TIME_S),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1,
                    max=3600,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(
                CONF_DELAY_BETWEEN_CALLS_S,
                default=defaults.get(
                    CONF_DELAY_BETWEEN_CALLS_S, DEFAULT_DELAY_BETWEEN_CALLS_S
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=300,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(
                CONF_CALL_MAX_RETRIES,
                default=defaults.get(CONF_CALL_MAX_RETRIES, DEFAULT_CALL_MAX_RETRIES),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=100,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(
                CONF_CALL_RETRY_DELAY_S,
                default=defaults.get(
                    CONF_CALL_RETRY_DELAY_S, DEFAULT_CALL_RETRY_DELAY_S
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=600,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(
                CONF_CALL_RETRY_FOREVER,
                default=defaults.get(
                    CONF_CALL_RETRY_FOREVER, DEFAULT_CALL_RETRY_FOREVER
                ),
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_CALL_FAILURE_ACTION,
                default=defaults.get(
                    CONF_CALL_FAILURE_ACTION, DEFAULT_CALL_FAILURE_ACTION
                ),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(
                            value="next_recipient",
                            label="Next recipient",
                        ),
                        selector.SelectOptionDict(
                            value="stop_batch",
                            label="Stop batch",
                        ),
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
        }
    )


def entity_mapping_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Build the entity mapping form schema."""
    return vol.Schema(
        {
            vol.Optional(
                CONF_RSSI_OBJECT_ID,
                default=defaults.get(CONF_RSSI_OBJECT_ID, ""),
            ): selector.TextSelector(),
            vol.Optional(
                CONF_REGISTERED_OBJECT_ID,
                default=defaults.get(CONF_REGISTERED_OBJECT_ID, ""),
            ): selector.TextSelector(),
            vol.Optional(
                CONF_MODEM_ONLINE_OBJECT_ID,
                default=defaults.get(CONF_MODEM_ONLINE_OBJECT_ID, ""),
            ): selector.TextSelector(),
            vol.Optional(
                CONF_SMS_SENDER_OBJECT_ID,
                default=defaults.get(CONF_SMS_SENDER_OBJECT_ID, ""),
            ): selector.TextSelector(),
            vol.Optional(
                CONF_SMS_MESSAGE_OBJECT_ID,
                default=defaults.get(CONF_SMS_MESSAGE_OBJECT_ID, ""),
            ): selector.TextSelector(),
            vol.Optional(
                CONF_INCOMING_CALL_OBJECT_ID,
                default=defaults.get(CONF_INCOMING_CALL_OBJECT_ID, ""),
            ): selector.TextSelector(),
            vol.Optional(
                CONF_CALL_STATE_OBJECT_ID,
                default=defaults.get(CONF_CALL_STATE_OBJECT_ID, ""),
            ): selector.TextSelector(),
            vol.Optional(
                CONF_USSD_OBJECT_ID,
                default=defaults.get(CONF_USSD_OBJECT_ID, ""),
            ): selector.TextSelector(),
        }
    )


def recipient_form_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Build the add/edit recipient form schema."""
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_RECIPIENT_NAME,
                default=defaults.get(CONF_RECIPIENT_NAME, ""),
            ): selector.TextSelector(),
            vol.Required(
                CONF_RECIPIENT_PHONE,
                default=defaults.get(CONF_RECIPIENT_PHONE, ""),
            ): selector.TextSelector(),
        }
    )


def recipient_select_schema(saved_recipients: tuple[SavedRecipient, ...]) -> vol.Schema:
    """Build a selector for one existing recipient."""
    return vol.Schema(
        {
            vol.Required(CONF_RECIPIENT_ID): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=recipient_select_options(saved_recipients),
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
        }
    )


def recipient_delete_schema(saved_recipients: tuple[SavedRecipient, ...]) -> vol.Schema:
    """Build a selector for multiple recipients to delete."""
    return vol.Schema(
        {
            vol.Required(CONF_RECIPIENT_ID): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=recipient_select_options(saved_recipients),
                    multiple=True,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
        }
    )


def sms_rule_form_schema(
    defaults: dict[str, Any],
    saved_recipients: tuple[SavedRecipient, ...],
) -> vol.Schema:
    """Build the add/edit form for one inbound SMS command rule."""
    return vol.Schema(
        {
            vol.Required(
                SMS_RULE_NAME,
                default=defaults.get(SMS_RULE_NAME, ""),
            ): selector.TextSelector(),
            vol.Required(
                SMS_RULE_ENABLED,
                default=defaults.get(SMS_RULE_ENABLED, True),
            ): selector.BooleanSelector(),
            vol.Required(
                SMS_RULE_SENDER_MODE,
                default=defaults.get(SMS_RULE_SENDER_MODE, SMS_RULE_SENDER_SAVED),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(
                            value=SMS_RULE_SENDER_SAVED,
                            label="Zapisany użytkownik",
                        ),
                        selector.SelectOptionDict(
                            value=SMS_RULE_SENDER_MANUAL,
                            label="Ręcznie wpisany numer",
                        ),
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(
                SMS_RULE_SAVED_RECIPIENT_ID,
                description={
                    "suggested_value": defaults.get(
                        SMS_RULE_SAVED_RECIPIENT_ID, ""
                    )
                },
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=recipient_select_options(saved_recipients),
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(
                SMS_RULE_SENDER_PHONE,
                default=defaults.get(SMS_RULE_SENDER_PHONE, ""),
            ): selector.TextSelector(),
            vol.Required(
                SMS_RULE_COMMAND,
                default=defaults.get(SMS_RULE_COMMAND, ""),
            ): selector.TextSelector(),
            vol.Required(
                SMS_RULE_MATCH_MODE,
                default=defaults.get(SMS_RULE_MATCH_MODE, SMS_RULE_MATCH_EXACT),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(
                            value=SMS_RULE_MATCH_EXACT,
                            label="Dokładna treść",
                        ),
                        selector.SelectOptionDict(
                            value=SMS_RULE_MATCH_CONTAINS,
                            label="Wiadomość zawiera",
                        ),
                        selector.SelectOptionDict(
                            value=SMS_RULE_MATCH_STARTS_WITH,
                            label="Wiadomość zaczyna się od",
                        ),
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(
                SMS_RULE_ENTITY_ID,
                default=defaults.get(SMS_RULE_ENTITY_ID, ""),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(multiple=False)
            ),
            vol.Required(
                SMS_RULE_ACTION,
                default=defaults.get(SMS_RULE_ACTION, SMS_RULE_ACTION_TOGGLE),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(
                            value=SMS_RULE_ACTION_TURN_ON,
                            label="Włącz encję",
                        ),
                        selector.SelectOptionDict(
                            value=SMS_RULE_ACTION_TURN_OFF,
                            label="Wyłącz encję",
                        ),
                        selector.SelectOptionDict(
                            value=SMS_RULE_ACTION_TOGGLE,
                            label="Przełącz stan",
                        ),
                        selector.SelectOptionDict(
                            value=SMS_RULE_ACTION_REPORT_STATE,
                            label="Odeślij aktualny stan",
                        ),
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(
                SMS_RULE_REPLY_ENABLED,
                default=defaults.get(SMS_RULE_REPLY_ENABLED, True),
            ): selector.BooleanSelector(),
            vol.Optional(
                SMS_RULE_SUCCESS_REPLY,
                default=defaults.get(SMS_RULE_SUCCESS_REPLY, DEFAULT_STATE_REPLY),
            ): selector.TextSelector(
                selector.TextSelectorConfig(multiline=True)
            ),
            vol.Optional(
                SMS_RULE_FAILURE_REPLY,
                default=defaults.get(SMS_RULE_FAILURE_REPLY, ""),
            ): selector.TextSelector(
                selector.TextSelectorConfig(multiline=True)
            ),
        }
    )


def sms_rule_select_schema(rules: tuple[SmsCommandRule, ...]) -> vol.Schema:
    """Build a selector for one existing SMS command rule."""
    return vol.Schema(
        {
            vol.Required(CONF_SMS_RULE_ID): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=sms_rule_select_options(rules),
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
        }
    )


def sms_rule_delete_schema(rules: tuple[SmsCommandRule, ...]) -> vol.Schema:
    """Build a multiple selector for deleting SMS command rules."""
    return vol.Schema(
        {
            vol.Required(CONF_SMS_RULE_ID): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=sms_rule_select_options(rules),
                    multiple=True,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
        }
    )


def sms_rule_select_options(
    rules: tuple[SmsCommandRule, ...],
) -> list[selector.SelectOptionDict]:
    """Build labeled select options for SMS command rules."""
    return [
        selector.SelectOptionDict(
            value=rule.id,
            label=f"{rule.name}: {rule.command} → {rule.entity_id}",
        )
        for rule in rules
    ]


def sms_rule_summary_lines(
    rules: tuple[SmsCommandRule, ...],
    max_items: int = 12,
) -> str:
    """Build a concise rule summary for the options flow."""
    if not rules:
        return "- brak skonfigurowanych poleceń SMS -"
    lines = [
        f"- {'✓' if rule.enabled else '○'} {rule.name}: „{rule.command}” → "
        f"{rule.entity_id} ({rule.action})"
        for rule in rules[:max_items]
    ]
    remaining = len(rules) - max_items
    if remaining > 0:
        lines.append(f"- ... i jeszcze {remaining}")
    return "\n".join(lines)


def clean_options(data: dict[str, Any]) -> dict[str, Any]:
    """Drop empty option values so config defaults can win."""
    cleaned: dict[str, Any] = {}
    for key, value in data.items():
        if key in CONNECTION_KEYS:
            continue
        if isinstance(value, str):
            value = value.strip()
        if value in ("", None, [], (), set()):
            continue
        cleaned[key] = value
    return cleaned


def update_managed_options(
    options: dict[str, Any],
    user_input: dict[str, Any],
    managed_keys: tuple[str, ...],
) -> None:
    """Update only the managed option keys, removing emptied values."""
    for key in managed_keys:
        value = user_input.get(key)
        if isinstance(value, str):
            value = value.strip()
        if value in ("", None, [], (), set()):
            options.pop(key, None)
            continue
        options[key] = value


class QTronicSmsGatewayConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Q-Tronic SMS Gateway."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return QTronicSmsGatewayOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                device = await validate_gateway_connection(user_input)
            except GatewayAuthenticationError:
                errors["base"] = "invalid_auth"
            except GatewayConnectionError:
                errors["base"] = "cannot_connect"
            else:
                unique_id = normalize_mac(device.mac_address) or device.name or user_input[CONF_HOST]
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()

                title = "Q-Tronic SMS Gateway"
                return self.async_create_entry(
                    title=title,
                    data={
                        CONF_HOST: user_input[CONF_HOST].strip(),
                        CONF_PORT: int(user_input[CONF_PORT]),
                        CONF_EXPECTED_NAME: device.name,
                        CONF_EXPECTED_MAC: normalize_mac(device.mac_address) or "",
                    },
                    options={
                        CONF_SEND_SMS_ACTION: DEFAULT_SEND_SMS_ACTION,
                        CONF_UNICODE_SEND_SMS_ACTION: DEFAULT_UNICODE_SEND_SMS_ACTION,
                        CONF_DIAL_ACTION: DEFAULT_DIAL_ACTION,
                        CONF_DISCONNECT_ACTION: DEFAULT_DISCONNECT_ACTION,
                        CONF_SMS_ENCODING: DEFAULT_SMS_ENCODING,
                        CONF_SEND_DELAY_MS: DEFAULT_SEND_DELAY_MS,
                        CONF_DEFAULT_RING_TIME_S: DEFAULT_RING_TIME_S,
                        CONF_DELAY_BETWEEN_CALLS_S: DEFAULT_DELAY_BETWEEN_CALLS_S,
                        CONF_CALL_MAX_RETRIES: DEFAULT_CALL_MAX_RETRIES,
                        CONF_CALL_RETRY_DELAY_S: DEFAULT_CALL_RETRY_DELAY_S,
                        CONF_CALL_RETRY_FOREVER: DEFAULT_CALL_RETRY_FOREVER,
                        CONF_CALL_FAILURE_ACTION: DEFAULT_CALL_FAILURE_ACTION,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=user_schema(
                user_input,
                suggested_host=suggested_addon_host(self.hass.config.config_dir),
            ),
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None):
        """Handle reconfiguration of connection settings."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()

        if user_input is not None:
            candidate = {
                CONF_HOST: user_input[CONF_HOST].strip(),
                CONF_PORT: int(user_input[CONF_PORT]),
            }
            try:
                device = await validate_gateway_connection(candidate)
            except GatewayAuthenticationError:
                errors["base"] = "invalid_auth"
            except GatewayConnectionError:
                errors["base"] = "cannot_connect"
            else:
                unique_id = normalize_mac(device.mac_address) or entry.unique_id or candidate[CONF_HOST]
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        **entry.data,
                        **candidate,
                        CONF_EXPECTED_NAME: device.name,
                        CONF_EXPECTED_MAC: normalize_mac(device.mac_address) or "",
                    },
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=user_schema(
                user_input or dict(entry.data),
                suggested_host=suggested_addon_host(self.hass.config.config_dir),
            ),
            errors=errors,
        )


class QTronicSmsGatewayOptionsFlow(OptionsFlow):
    """Handle options for Q-Tronic SMS Gateway."""

    def __init__(self) -> None:
        self._options: dict[str, Any] | None = None
        self._editing_recipient_id: str | None = None
        self._editing_sms_rule_id: str | None = None

    @property
    def working_options(self) -> dict[str, Any]:
        """Return the mutable working copy of options."""
        if self._options is None:
            self._options = deepcopy(dict(self.config_entry.options))
        return self._options

    @property
    def saved_recipients(self) -> tuple[SavedRecipient, ...]:
        """Return saved recipients from the working copy."""
        return load_saved_recipients(self.working_options.get(CONF_SAVED_RECIPIENTS, []))

    @property
    def available_recipients(self) -> tuple[SavedRecipient, ...]:
        """Return recipients currently exposed by the running add-on or options."""
        hub = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)
        if hub is not None and hub.saved_recipients:
            return hub.saved_recipients
        return self.saved_recipients

    @property
    def sms_command_rules(self) -> tuple[SmsCommandRule, ...]:
        """Return SMS command rules from the mutable working copy."""
        return load_sms_command_rules(
            self.working_options.get(CONF_SMS_COMMAND_RULES, [])
        )

    def _recipient_by_id(self, recipient_id: str) -> SavedRecipient | None:
        for recipient in self.available_recipients:
            if recipient.id == recipient_id:
                return recipient
        return None

    def _sms_rule_by_id(self, rule_id: str) -> SmsCommandRule | None:
        for rule in self.sms_command_rules:
            if rule.id == rule_id:
                return rule
        return None

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        """Show the top-level options menu."""
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "connection",
                "messaging",
                "calling",
                "recipients",
                "sms_commands",
                "entity_mapping",
                "finish",
            ],
            description_placeholders={
                "recipient_count": str(len(self.available_recipients)),
                "rule_count": str(len(self.sms_command_rules)),
            },
        )

    async def async_step_finish(self, user_input: dict[str, Any] | None = None):
        """Save options and close the flow."""
        return self.async_create_entry(title="", data=clean_options(self.working_options))

    async def async_step_connection(self, user_input: dict[str, Any] | None = None):
        """Edit connection settings for the ESPHome node."""
        errors: dict[str, str] = {}

        if user_input is not None:
            candidate = {
                CONF_HOST: user_input[CONF_HOST].strip(),
                CONF_PORT: int(user_input[CONF_PORT]),
            }
            try:
                device = await validate_gateway_connection(candidate)
            except GatewayAuthenticationError:
                errors["base"] = "invalid_auth"
            except GatewayConnectionError:
                errors["base"] = "cannot_connect"
            else:
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    data={
                        **self.config_entry.data,
                        **candidate,
                        CONF_EXPECTED_NAME: device.name,
                        CONF_EXPECTED_MAC: normalize_mac(device.mac_address) or "",
                    },
                )
                return self.async_create_entry(
                    title="",
                    data=clean_options(self.working_options),
                )

        defaults = {
            CONF_HOST: self.config_entry.data.get(CONF_HOST, DEFAULT_ADDON_HOSTNAME),
            CONF_PORT: self.config_entry.data.get(CONF_PORT, DEFAULT_PORT),
        }
        return self.async_show_form(
            step_id="connection",
            data_schema=user_schema(
                user_input or defaults,
                suggested_host=suggested_addon_host(self.hass.config.config_dir),
            ),
            errors=errors,
        )

    async def async_step_messaging(self, user_input: dict[str, Any] | None = None):
        """Edit SMS sending behavior."""
        managed_keys = (
            CONF_DEFAULT_RECIPIENT_IDS,
            CONF_DEFAULT_RECIPIENT,
            CONF_SEND_SMS_ACTION,
            CONF_UNICODE_SEND_SMS_ACTION,
            CONF_SMS_ENCODING,
            CONF_SEND_DELAY_MS,
        )
        if user_input is not None:
            update_managed_options(self.working_options, user_input, managed_keys)
            return await self.async_step_init()

        defaults = {
            CONF_DEFAULT_RECIPIENT_IDS: [
                recipient_id
                for recipient_id in self.working_options.get(CONF_DEFAULT_RECIPIENT_IDS, [])
                if self._recipient_by_id(str(recipient_id)) is not None
            ],
            CONF_DEFAULT_RECIPIENT: self.working_options.get(CONF_DEFAULT_RECIPIENT, ""),
            CONF_SEND_SMS_ACTION: self.working_options.get(
                CONF_SEND_SMS_ACTION, DEFAULT_SEND_SMS_ACTION
            ),
            CONF_UNICODE_SEND_SMS_ACTION: self.working_options.get(
                CONF_UNICODE_SEND_SMS_ACTION,
                DEFAULT_UNICODE_SEND_SMS_ACTION,
            ),
            CONF_SMS_ENCODING: self.working_options.get(
                CONF_SMS_ENCODING, DEFAULT_SMS_ENCODING
            ),
            CONF_SEND_DELAY_MS: self.working_options.get(
                CONF_SEND_DELAY_MS, DEFAULT_SEND_DELAY_MS
            ),
        }
        return self.async_show_form(
            step_id="messaging",
            data_schema=messaging_schema(defaults, self.saved_recipients),
            description_placeholders={
                "recipients": recipient_summary_lines(self.saved_recipients),
            },
        )

    async def async_step_calling(self, user_input: dict[str, Any] | None = None):
        """Edit call behavior and retry policy."""
        managed_keys = (
            CONF_DIAL_ACTION,
            CONF_DISCONNECT_ACTION,
            CONF_DEFAULT_RING_TIME_S,
            CONF_DELAY_BETWEEN_CALLS_S,
            CONF_CALL_MAX_RETRIES,
            CONF_CALL_RETRY_DELAY_S,
            CONF_CALL_RETRY_FOREVER,
            CONF_CALL_FAILURE_ACTION,
        )
        if user_input is not None:
            update_managed_options(self.working_options, user_input, managed_keys)
            return await self.async_step_init()

        defaults = {
            CONF_DIAL_ACTION: self.working_options.get(
                CONF_DIAL_ACTION, DEFAULT_DIAL_ACTION
            ),
            CONF_DISCONNECT_ACTION: self.working_options.get(
                CONF_DISCONNECT_ACTION,
                DEFAULT_DISCONNECT_ACTION,
            ),
            CONF_DEFAULT_RING_TIME_S: self.working_options.get(
                CONF_DEFAULT_RING_TIME_S, DEFAULT_RING_TIME_S
            ),
            CONF_DELAY_BETWEEN_CALLS_S: self.working_options.get(
                CONF_DELAY_BETWEEN_CALLS_S,
                DEFAULT_DELAY_BETWEEN_CALLS_S,
            ),
            CONF_CALL_MAX_RETRIES: self.working_options.get(
                CONF_CALL_MAX_RETRIES,
                DEFAULT_CALL_MAX_RETRIES,
            ),
            CONF_CALL_RETRY_DELAY_S: self.working_options.get(
                CONF_CALL_RETRY_DELAY_S,
                DEFAULT_CALL_RETRY_DELAY_S,
            ),
            CONF_CALL_RETRY_FOREVER: self.working_options.get(
                CONF_CALL_RETRY_FOREVER,
                DEFAULT_CALL_RETRY_FOREVER,
            ),
            CONF_CALL_FAILURE_ACTION: self.working_options.get(
                CONF_CALL_FAILURE_ACTION,
                DEFAULT_CALL_FAILURE_ACTION,
            ),
        }
        return self.async_show_form(
            step_id="calling",
            data_schema=calling_schema(defaults),
        )

    async def async_step_recipients(self, user_input: dict[str, Any] | None = None):
        """Show the saved recipients submenu."""
        return self.async_show_menu(
            step_id="recipients",
            menu_options=[
                "add_recipient",
                "edit_recipient_select",
                "delete_recipients",
                "init",
            ],
            description_placeholders={
                "recipients": recipient_summary_lines(self.saved_recipients),
            },
        )

    async def async_step_add_recipient(self, user_input: dict[str, Any] | None = None):
        """Add a new saved recipient."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                name = normalize_recipient_name(user_input[CONF_RECIPIENT_NAME])
                phone = normalize_phone_number(user_input[CONF_RECIPIENT_PHONE])
            except ValueError:
                errors["base"] = "invalid_recipient"
            else:
                existing_ids = {recipient.id for recipient in self.saved_recipients}
                recipient_id = make_recipient_id(name, existing_ids)
                recipients = list(self.saved_recipients)
                recipients.append(SavedRecipient(id=recipient_id, name=name, phone=phone))
                self.working_options[CONF_SAVED_RECIPIENTS] = serialize_saved_recipients(
                    recipients
                )
                return await self.async_step_recipients()

        return self.async_show_form(
            step_id="add_recipient",
            data_schema=recipient_form_schema(user_input),
            errors=errors,
        )

    async def async_step_edit_recipient_select(
        self, user_input: dict[str, Any] | None = None
    ):
        """Select a saved recipient to edit."""
        if not self.saved_recipients:
            return self.async_abort(reason="no_saved_recipients")

        if user_input is not None:
            self._editing_recipient_id = str(user_input[CONF_RECIPIENT_ID])
            return await self.async_step_edit_recipient()

        return self.async_show_form(
            step_id="edit_recipient_select",
            data_schema=recipient_select_schema(self.saved_recipients),
        )

    async def async_step_edit_recipient(self, user_input: dict[str, Any] | None = None):
        """Edit one saved recipient."""
        if self._editing_recipient_id is None:
            return await self.async_step_edit_recipient_select()

        recipient = self._recipient_by_id(self._editing_recipient_id)
        if recipient is None:
            self._editing_recipient_id = None
            return self.async_abort(reason="no_saved_recipients")

        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                name = normalize_recipient_name(user_input[CONF_RECIPIENT_NAME])
                phone = normalize_phone_number(user_input[CONF_RECIPIENT_PHONE])
            except ValueError:
                errors["base"] = "invalid_recipient"
            else:
                updated = [
                    SavedRecipient(id=item.id, name=name, phone=phone)
                    if item.id == recipient.id
                    else item
                    for item in self.saved_recipients
                ]
                self.working_options[CONF_SAVED_RECIPIENTS] = serialize_saved_recipients(
                    updated
                )
                self._editing_recipient_id = None
                return await self.async_step_recipients()

        return self.async_show_form(
            step_id="edit_recipient",
            data_schema=recipient_form_schema(
                {
                    CONF_RECIPIENT_NAME: recipient.name,
                    CONF_RECIPIENT_PHONE: recipient.phone,
                }
            ),
            errors=errors,
        )

    async def async_step_delete_recipients(
        self, user_input: dict[str, Any] | None = None
    ):
        """Delete one or more saved recipients."""
        if not self.saved_recipients:
            return self.async_abort(reason="no_saved_recipients")

        if user_input is not None:
            selected_ids = user_input.get(CONF_RECIPIENT_ID, [])
            if isinstance(selected_ids, str):
                selected_ids = [selected_ids]
            remaining = [
                recipient
                for recipient in self.saved_recipients
                if recipient.id not in selected_ids
            ]
            self.working_options[CONF_SAVED_RECIPIENTS] = serialize_saved_recipients(
                remaining
            )
            if CONF_DEFAULT_RECIPIENT_IDS in self.working_options:
                self.working_options[CONF_DEFAULT_RECIPIENT_IDS] = [
                    recipient_id
                    for recipient_id in self.working_options.get(
                        CONF_DEFAULT_RECIPIENT_IDS, []
                    )
                    if recipient_id not in selected_ids
                ]
            return await self.async_step_recipients()

        return self.async_show_form(
            step_id="delete_recipients",
            data_schema=recipient_delete_schema(self.saved_recipients),
        )

    async def async_step_sms_commands(self, user_input: dict[str, Any] | None = None):
        """Show the inbound SMS commands submenu."""
        return self.async_show_menu(
            step_id="sms_commands",
            menu_options=[
                "add_sms_rule",
                "edit_sms_rule_select",
                "delete_sms_rules",
                "init",
            ],
            description_placeholders={
                "rules": sms_rule_summary_lines(self.sms_command_rules),
            },
        )

    def _validated_sms_rule(
        self,
        user_input: dict[str, Any],
        *,
        rule_id: str,
    ) -> SmsCommandRule:
        """Validate and normalize one SMS command rule form submission."""
        name = " ".join(str(user_input.get(SMS_RULE_NAME, "")).split())
        command = " ".join(str(user_input.get(SMS_RULE_COMMAND, "")).split())
        sender_mode = str(user_input.get(SMS_RULE_SENDER_MODE, ""))
        saved_recipient_id = str(
            user_input.get(SMS_RULE_SAVED_RECIPIENT_ID, "") or ""
        ).strip()
        sender_phone = str(user_input.get(SMS_RULE_SENDER_PHONE, "") or "").strip()
        match_mode = str(user_input.get(SMS_RULE_MATCH_MODE, ""))
        action = str(user_input.get(SMS_RULE_ACTION, ""))
        entity_value = str(user_input.get(SMS_RULE_ENTITY_ID, "")).strip()
        entity_id = (
            er.async_resolve_entity_id(er.async_get(self.hass), entity_value)
            or entity_value
        )
        reply_enabled = bool(user_input.get(SMS_RULE_REPLY_ENABLED, True))
        success_reply = str(user_input.get(SMS_RULE_SUCCESS_REPLY, "") or "").strip()
        failure_reply = str(user_input.get(SMS_RULE_FAILURE_REPLY, "") or "").strip()

        if not name:
            raise ValueError("invalid_rule_name")
        if not normalize_inbound_text(command):
            raise ValueError("invalid_command")
        if sender_mode == SMS_RULE_SENDER_SAVED:
            recipient = self._recipient_by_id(saved_recipient_id)
            if recipient is None:
                raise ValueError("invalid_sender")
            sender_phone = recipient.phone
        elif sender_mode == SMS_RULE_SENDER_MANUAL:
            try:
                sender_phone = normalize_phone_number(sender_phone)
            except ValueError as err:
                raise ValueError("invalid_sender") from err
            saved_recipient_id = ""
        else:
            raise ValueError("invalid_sender")

        state = self.hass.states.get(entity_id)
        if state is None:
            raise ValueError("entity_not_found")
        if match_mode not in SMS_RULE_MATCH_MODES:
            raise ValueError("invalid_command")
        if action not in {
            SMS_RULE_ACTION_TURN_ON,
            SMS_RULE_ACTION_TURN_OFF,
            SMS_RULE_ACTION_TOGGLE,
            SMS_RULE_ACTION_REPORT_STATE,
        }:
            raise ValueError("unsupported_entity")
        if (
            action != SMS_RULE_ACTION_REPORT_STATE
            and entity_id.partition(".")[0] not in CONTROL_ENTITY_DOMAINS
        ):
            raise ValueError("unsupported_entity")

        if action == SMS_RULE_ACTION_REPORT_STATE:
            reply_enabled = True
        if reply_enabled and not success_reply:
            success_reply = DEFAULT_STATE_REPLY
        try:
            if success_reply:
                validate_reply_template(success_reply)
            if failure_reply:
                validate_reply_template(failure_reply)
        except ValueError as err:
            raise ValueError("invalid_reply_template") from err

        normalized_command = normalize_inbound_text(command)
        for existing in self.sms_command_rules:
            if existing.id == rule_id:
                continue
            existing_phone = existing.sender_phone
            if existing.sender_mode == SMS_RULE_SENDER_SAVED:
                existing_recipient = self._recipient_by_id(
                    existing.saved_recipient_id
                )
                if existing_recipient is not None:
                    existing_phone = existing_recipient.phone
            same_saved_recipient = (
                sender_mode == SMS_RULE_SENDER_SAVED
                and existing.sender_mode == SMS_RULE_SENDER_SAVED
                and existing.saved_recipient_id == saved_recipient_id
            )
            if (
                (
                    same_saved_recipient
                    or phone_numbers_match(existing_phone, sender_phone)
                )
                and normalize_inbound_text(existing.command) == normalized_command
                and existing.match_mode == match_mode
            ):
                raise ValueError("duplicate_sms_rule")

        return SmsCommandRule(
            id=rule_id,
            name=name,
            enabled=bool(user_input.get(SMS_RULE_ENABLED, True)),
            sender_mode=sender_mode,
            saved_recipient_id=saved_recipient_id,
            sender_phone=sender_phone,
            command=command,
            match_mode=match_mode,
            action=action,
            entity_id=entity_id,
            reply_enabled=reply_enabled,
            success_reply=success_reply,
            failure_reply=failure_reply,
        )

    async def async_step_add_sms_rule(
        self, user_input: dict[str, Any] | None = None
    ):
        """Add a new inbound SMS command rule."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                rule = self._validated_sms_rule(
                    user_input,
                    rule_id=make_sms_rule_id(),
                )
            except ValueError as err:
                errors["base"] = str(err)
            else:
                rules = list(self.sms_command_rules)
                rules.append(rule)
                self.working_options[CONF_SMS_COMMAND_RULES] = (
                    serialize_sms_command_rules(rules)
                )
                return await self.async_step_sms_commands()

        return self.async_show_form(
            step_id="add_sms_rule",
            data_schema=sms_rule_form_schema(
                user_input or {}, self.available_recipients
            ),
            errors=errors,
            description_placeholders={
                name: "{" + name + "}" for name in REPLY_TEMPLATE_FIELDS
            },
        )

    async def async_step_edit_sms_rule_select(
        self, user_input: dict[str, Any] | None = None
    ):
        """Select an SMS command rule to edit."""
        if not self.sms_command_rules:
            return self.async_abort(reason="no_sms_rules")
        if user_input is not None:
            self._editing_sms_rule_id = str(user_input[CONF_SMS_RULE_ID])
            return await self.async_step_edit_sms_rule()
        return self.async_show_form(
            step_id="edit_sms_rule_select",
            data_schema=sms_rule_select_schema(self.sms_command_rules),
        )

    async def async_step_edit_sms_rule(
        self, user_input: dict[str, Any] | None = None
    ):
        """Edit one inbound SMS command rule."""
        if self._editing_sms_rule_id is None:
            return await self.async_step_edit_sms_rule_select()
        existing = self._sms_rule_by_id(self._editing_sms_rule_id)
        if existing is None:
            self._editing_sms_rule_id = None
            return self.async_abort(reason="no_sms_rules")

        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                updated_rule = self._validated_sms_rule(
                    user_input,
                    rule_id=existing.id,
                )
            except ValueError as err:
                errors["base"] = str(err)
            else:
                rules = [
                    updated_rule if rule.id == existing.id else rule
                    for rule in self.sms_command_rules
                ]
                self.working_options[CONF_SMS_COMMAND_RULES] = (
                    serialize_sms_command_rules(rules)
                )
                self._editing_sms_rule_id = None
                return await self.async_step_sms_commands()

        defaults = existing.as_dict()
        if user_input is not None:
            defaults.update(user_input)
        return self.async_show_form(
            step_id="edit_sms_rule",
            data_schema=sms_rule_form_schema(defaults, self.available_recipients),
            errors=errors,
            description_placeholders={
                name: "{" + name + "}" for name in REPLY_TEMPLATE_FIELDS
            },
        )

    async def async_step_delete_sms_rules(
        self, user_input: dict[str, Any] | None = None
    ):
        """Delete one or more inbound SMS command rules."""
        if not self.sms_command_rules:
            return self.async_abort(reason="no_sms_rules")
        if user_input is not None:
            selected_ids = user_input.get(CONF_SMS_RULE_ID, [])
            if isinstance(selected_ids, str):
                selected_ids = [selected_ids]
            remaining = [
                rule for rule in self.sms_command_rules if rule.id not in selected_ids
            ]
            self.working_options[CONF_SMS_COMMAND_RULES] = (
                serialize_sms_command_rules(remaining)
            )
            return await self.async_step_sms_commands()
        return self.async_show_form(
            step_id="delete_sms_rules",
            data_schema=sms_rule_delete_schema(self.sms_command_rules),
        )

    async def async_step_entity_mapping(self, user_input: dict[str, Any] | None = None):
        """Edit autodetection overrides for ESPHome entities."""
        managed_keys = (
            CONF_RSSI_OBJECT_ID,
            CONF_REGISTERED_OBJECT_ID,
            CONF_MODEM_ONLINE_OBJECT_ID,
            CONF_SMS_SENDER_OBJECT_ID,
            CONF_SMS_MESSAGE_OBJECT_ID,
            CONF_INCOMING_CALL_OBJECT_ID,
            CONF_CALL_STATE_OBJECT_ID,
            CONF_USSD_OBJECT_ID,
        )
        if user_input is not None:
            update_managed_options(self.working_options, user_input, managed_keys)
            return await self.async_step_init()

        defaults = {
            CONF_RSSI_OBJECT_ID: self.working_options.get(CONF_RSSI_OBJECT_ID, ""),
            CONF_REGISTERED_OBJECT_ID: self.working_options.get(
                CONF_REGISTERED_OBJECT_ID, ""
            ),
            CONF_MODEM_ONLINE_OBJECT_ID: self.working_options.get(
                CONF_MODEM_ONLINE_OBJECT_ID, ""
            ),
            CONF_SMS_SENDER_OBJECT_ID: self.working_options.get(
                CONF_SMS_SENDER_OBJECT_ID, ""
            ),
            CONF_SMS_MESSAGE_OBJECT_ID: self.working_options.get(
                CONF_SMS_MESSAGE_OBJECT_ID, ""
            ),
            CONF_INCOMING_CALL_OBJECT_ID: self.working_options.get(
                CONF_INCOMING_CALL_OBJECT_ID, ""
            ),
            CONF_CALL_STATE_OBJECT_ID: self.working_options.get(
                CONF_CALL_STATE_OBJECT_ID, ""
            ),
            CONF_USSD_OBJECT_ID: self.working_options.get(CONF_USSD_OBJECT_ID, ""),
        }
        return self.async_show_form(
            step_id="entity_mapping",
            data_schema=entity_mapping_schema(defaults),
        )
