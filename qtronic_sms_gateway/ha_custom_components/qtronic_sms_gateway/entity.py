"""Shared entity helpers for the Q-Tronic SMS Gateway integration."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.entity import Entity

from .hub import GatewayEntityInfo, QTronicSmsGatewayHub


class QTronicSmsGatewayEntity(Entity):
    """Base entity backed by the local Q-Tronic add-on state."""

    _attr_should_poll = False

    def __init__(self, hub: QTronicSmsGatewayHub, info: GatewayEntityInfo) -> None:
        self.hub = hub
        self.info = info
        self._attr_has_entity_name = True
        self._attr_name = info.name
        self._attr_unique_id = f"{hub.unique_id_prefix}_{info.object_id}"
        if info.icon:
            self._attr_icon = info.icon
        self._remove_listener: Any = None

    @property
    def available(self) -> bool:
        return self.hub.available

    @property
    def device_info(self):
        return self.hub.ha_device_info

    async def async_added_to_hass(self) -> None:
        self._remove_listener = self.hub.async_add_listener(self._handle_hub_update)

    async def async_will_remove_from_hass(self) -> None:
        if self._remove_listener is not None:
            self._remove_listener()
            self._remove_listener = None

    def _handle_hub_update(self) -> None:
        self.async_write_ha_state()
