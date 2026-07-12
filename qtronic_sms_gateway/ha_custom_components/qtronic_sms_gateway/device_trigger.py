"""Device triggers for the Q-Tronic SMS Gateway integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.device_automation import TRIGGER_BASE_SCHEMA
from homeassistant.components.homeassistant.triggers import event as event_trigger
from homeassistant.const import CONF_DEVICE_ID, CONF_DOMAIN, CONF_PLATFORM, CONF_TYPE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import selector

from .const import (
    ATTR_MESSAGE_SEARCH,
    ATTR_PHONE_NUMBER,
    DOMAIN,
    EVENT_ATTR_CALLER_NORMALIZED,
    EVENT_ATTR_MESSAGE_SEARCH,
    EVENT_ATTR_SAVED_RECIPIENT_ID,
    EVENT_ATTR_SENDER_NORMALIZED,
    EVENT_INCOMING_CALL,
    EVENT_SMS_RECEIVED,
    TRIGGER_INCOMING_CALL,
    TRIGGER_SMS_RECEIVED,
)
from .hub import QTronicSmsGatewayHub
from .recipients import phone_match_key, recipient_select_options
from .sms import normalize_inbound_text

CONF_SAVED_RECIPIENT_ID = "saved_recipient_id"

TRIGGER_TYPES = {
    TRIGGER_SMS_RECEIVED,
    TRIGGER_INCOMING_CALL,
}

TRIGGER_SCHEMA = TRIGGER_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_TYPE): vol.In(TRIGGER_TYPES),
        vol.Optional(CONF_SAVED_RECIPIENT_ID): str,
        vol.Optional(ATTR_PHONE_NUMBER): str,
        vol.Optional(ATTR_MESSAGE_SEARCH): str,
    }
)


def _hub_for_device_id(
    hass: HomeAssistant,
    device_id: str,
) -> QTronicSmsGatewayHub | None:
    """Resolve the gateway hub for a Home Assistant device."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        return None

    identifiers = {identifier for identifier in device.identifiers if identifier[0] == DOMAIN}
    if not identifiers:
        return None

    hubs: dict[str, QTronicSmsGatewayHub] = hass.data.get(DOMAIN, {})
    for hub in hubs.values():
        if (DOMAIN, hub.unique_id_prefix) in identifiers:
            return hub
    return None


async def async_get_triggers(hass: HomeAssistant, device_id: str) -> list[dict[str, str]]:
    """Return triggers supported by a Q-Tronic SMS Gateway device."""
    if _hub_for_device_id(hass, device_id) is None:
        return []

    return [
        {
            CONF_PLATFORM: "device",
            CONF_DOMAIN: DOMAIN,
            CONF_DEVICE_ID: device_id,
            CONF_TYPE: TRIGGER_SMS_RECEIVED,
        },
        {
            CONF_PLATFORM: "device",
            CONF_DOMAIN: DOMAIN,
            CONF_DEVICE_ID: device_id,
            CONF_TYPE: TRIGGER_INCOMING_CALL,
        },
    ]


async def async_get_trigger_capabilities(
    hass: HomeAssistant, config: dict[str, Any]
) -> dict[str, vol.Schema]:
    """Return trigger capabilities."""
    hub = _hub_for_device_id(hass, config[CONF_DEVICE_ID])
    recipient_options = recipient_select_options(hub.saved_recipients) if hub else []

    if config[CONF_TYPE] == TRIGGER_SMS_RECEIVED:
        return {
            "extra_fields": vol.Schema(
                {
                    vol.Optional(CONF_SAVED_RECIPIENT_ID): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=recipient_options,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Optional(ATTR_PHONE_NUMBER): selector.TextSelector(),
                    vol.Optional(ATTR_MESSAGE_SEARCH): selector.TextSelector(),
                }
            )
        }

    return {
        "extra_fields": vol.Schema(
            {
                vol.Optional(CONF_SAVED_RECIPIENT_ID): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=recipient_options,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(ATTR_PHONE_NUMBER): selector.TextSelector(),
            }
        )
    }


async def async_attach_trigger(
    hass: HomeAssistant,
    config: dict[str, Any],
    action,
    trigger_info: dict[str, Any],
):
    """Attach a Q-Tronic SMS Gateway device trigger."""
    event_type = (
        EVENT_SMS_RECEIVED
        if config[CONF_TYPE] == TRIGGER_SMS_RECEIVED
        else EVENT_INCOMING_CALL
    )
    event_data: dict[str, str] = {}

    saved_recipient_id = config.get(CONF_SAVED_RECIPIENT_ID)
    if saved_recipient_id:
        event_data[EVENT_ATTR_SAVED_RECIPIENT_ID] = saved_recipient_id

    phone_number = config.get(ATTR_PHONE_NUMBER)
    if phone_number:
        if config[CONF_TYPE] == TRIGGER_SMS_RECEIVED:
            event_data[EVENT_ATTR_SENDER_NORMALIZED] = phone_match_key(phone_number)
        else:
            event_data[EVENT_ATTR_CALLER_NORMALIZED] = phone_match_key(phone_number)

    message_search = config.get(ATTR_MESSAGE_SEARCH)
    if message_search and config[CONF_TYPE] == TRIGGER_SMS_RECEIVED:
        event_data[EVENT_ATTR_MESSAGE_SEARCH] = normalize_inbound_text(message_search)

    event_config = event_trigger.TRIGGER_SCHEMA(
        {
            event_trigger.CONF_PLATFORM: "event",
            event_trigger.CONF_EVENT_TYPE: event_type,
            event_trigger.CONF_EVENT_DATA: event_data,
        }
    )
    return await event_trigger.async_attach_trigger(
        hass, event_config, action, trigger_info, platform_type="device"
    )
