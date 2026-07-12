"""Helpers for assigning inbound Home Assistant events to a gateway entry."""

from __future__ import annotations

from homeassistant.const import CONF_HOST
from homeassistant.core import Event

from .const import EVENT_ATTR_ADDON_HOSTNAME, EVENT_ATTR_GATEWAY_HOST
from .hub import QTronicSmsGatewayHub


def event_belongs_to_hub(event: Event, hub: QTronicSmsGatewayHub) -> bool:
    """Return whether an add-on event belongs to this gateway hub."""
    event_gateway_host = str(event.data.get(EVENT_ATTR_GATEWAY_HOST, "")).lower()
    if event_gateway_host:
        return event_gateway_host == hub.gateway_host.lower()

    event_addon_hostname = str(
        event.data.get(EVENT_ATTR_ADDON_HOSTNAME, "")
    ).lower()
    if event_addon_hostname:
        candidates = {
            hub.host.lower(),
            str(hub.entry.data.get(CONF_HOST, "")).lower(),
        }
        return event_addon_hostname in candidates

    return False
