"""Binary sensors for the Q-Tronic SMS Gateway integration."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ROLE_MODEM_ONLINE, ROLE_REGISTERED
from .entity import QTronicSmsGatewayEntity
from .hub import QTronicSmsGatewayHub, state_as_bool


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Q-Tronic SMS Gateway binary sensors."""
    hub: QTronicSmsGatewayHub = entry.runtime_data
    entities: list[BinarySensorEntity] = []
    registered_info = hub.entity_info_for_role(ROLE_REGISTERED)
    if registered_info is not None:
        entities.append(
            QTronicSmsGatewayRoleBinarySensor(hub, registered_info, ROLE_REGISTERED)
        )
    modem_info = hub.entity_info_for_role(ROLE_MODEM_ONLINE)
    if modem_info is not None:
        entities.append(
            QTronicSmsGatewayRoleBinarySensor(hub, modem_info, ROLE_MODEM_ONLINE)
        )
    async_add_entities(entities)


class QTronicSmsGatewayRoleBinarySensor(
    QTronicSmsGatewayEntity, BinarySensorEntity
):
    """Expose a boolean modem state."""

    def __init__(self, hub: QTronicSmsGatewayHub, info, role: str) -> None:
        super().__init__(hub, info)
        self._role = role

    @property
    def is_on(self) -> bool | None:
        return state_as_bool(self.hub.state_for_role(self._role))
