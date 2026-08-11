"""Sanitized diagnostics for Q-Tronic SMS Gateway config entries."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_DEFAULT_RECIPIENT,
    CONF_DEFAULT_RECIPIENT_IDS,
    CONF_FORWARD_EXCLUDED_NUMBERS,
    CONF_FORWARD_EXCLUDED_RECIPIENT_IDS,
    CONF_FORWARD_RECIPIENT_IDS,
    CONF_SAVED_RECIPIENTS,
    CONF_SMS_COMMAND_RULES,
    ROLE_USSD,
    SMS_RULE_COMMAND,
    SMS_RULE_FAILURE_REPLY,
    SMS_RULE_PIN_HASH,
    SMS_RULE_SAVED_RECIPIENT_ID,
    SMS_RULE_SENDER_PHONE,
    SMS_RULE_SUCCESS_REPLY,
)
from .hub import QTronicSmsGatewayHub


def _sanitized_options(options: dict[str, Any]) -> dict[str, Any]:
    sanitized = dict(options)
    if sanitized.get(CONF_DEFAULT_RECIPIENT):
        sanitized[CONF_DEFAULT_RECIPIENT] = "**redacted**"
    if sanitized.get(CONF_FORWARD_EXCLUDED_NUMBERS):
        sanitized[CONF_FORWARD_EXCLUDED_NUMBERS] = ["**redacted**"]
    for recipient_ids_key in (
        CONF_DEFAULT_RECIPIENT_IDS,
        CONF_FORWARD_RECIPIENT_IDS,
        CONF_FORWARD_EXCLUDED_RECIPIENT_IDS,
    ):
        if sanitized.get(recipient_ids_key):
            sanitized[recipient_ids_key] = ["**redacted**"]
    recipients = []
    for item in options.get(CONF_SAVED_RECIPIENTS, []):
        if not isinstance(item, dict):
            continue
        recipients.append(
            {
                "id": "**redacted**",
                "name": "**redacted**",
                "phone": "**redacted**",
                "pin_configured": bool(item.get("pin_hash")),
            }
        )
    sanitized[CONF_SAVED_RECIPIENTS] = recipients
    rules = []
    for item in options.get(CONF_SMS_COMMAND_RULES, []):
        if not isinstance(item, dict):
            continue
        rule = dict(item)
        rule[SMS_RULE_SENDER_PHONE] = (
            "**redacted**" if rule.get(SMS_RULE_SENDER_PHONE) else ""
        )
        if rule.get(SMS_RULE_SAVED_RECIPIENT_ID):
            rule[SMS_RULE_SAVED_RECIPIENT_ID] = "**redacted**"
        rule[SMS_RULE_COMMAND] = "**redacted**"
        rule[SMS_RULE_SUCCESS_REPLY] = "**redacted**"
        rule[SMS_RULE_FAILURE_REPLY] = "**redacted**"
        rule["pin_configured"] = bool(rule.pop(SMS_RULE_PIN_HASH, ""))
        if rule.get("service_data"):
            rule["service_data"] = "**redacted**"
        rules.append(rule)
    sanitized[CONF_SMS_COMMAND_RULES] = rules
    return sanitized


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return useful state without message contents, phone numbers, PINs or API token."""
    hub: QTronicSmsGatewayHub = entry.runtime_data
    safe_states = {
        key: value
        for key, value in hub._states.items()  # noqa: SLF001 - diagnostics snapshot
        if key not in {"sms_sender", "sms_message", "incoming_call", ROLE_USSD}
    }
    command_engine = getattr(hub, "sms_command_engine", None)
    return {
        "entry": {
            "entry_id": entry.entry_id,
            "title": entry.title,
            "unique_id": entry.unique_id,
            "data": {
                key: ("**redacted**" if "key" in key or "token" in key else value)
                for key, value in entry.data.items()
            },
            "options": _sanitized_options(dict(entry.options)),
        },
        "backend": {
            "available": hub.available,
            "base_url": hub.addon_base_url,
            "gateway_host": hub.gateway_host,
            "api_token_configured": hub.api_token_configured,
            "last_connect_error": hub.last_connect_error,
            "component_status": {
                "esp": hub.component_status("esp"),
                "sim800": hub.component_status("sim800"),
            },
            "states": safe_states,
            "queue_depth": hub.queued_job_count,
            "active_job_kind": hub.active_job_kind,
            "active_job_id": hub.active_job_id,
            "capabilities": {
                "send_sms": hub.can_send_sms,
                "send_unicode_sms": hub.can_send_unicode_sms,
                "call": hub.can_place_calls,
                "hangup": hub.can_hangup,
                "cancel_batch": hub.can_cancel_batch,
                "send_ussd": hub.can_send_ussd,
            },
        },
        "sms_command_statistics": (
            command_engine.statistics if command_engine is not None else {}
        ),
    }
