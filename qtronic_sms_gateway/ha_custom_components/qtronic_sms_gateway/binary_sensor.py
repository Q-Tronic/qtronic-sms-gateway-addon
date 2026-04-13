"""Binary sensors for the Q-Tronic SMS Gateway integration."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ROLE_REGISTERED
from .entity import QTronicSmsGatewayEntity
from .hub import QTronicSmsGatewayHub, state_as_bool


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Q-Tronic SMS Gateway binary sensors."""
    hub: QTronicSmsGatewayHub = entry.runtime_data
    info = hub.entity_info_for_role(ROLE_REGISTERED)
    if info is None:
        return
    async_add_entities([QTronicSmsGatewayRegisteredBinarySensor(hub, info)])


class QTronicSmsGatewayRegisteredBinarySensor(
    QTronicSmsGatewayEntity, BinarySensorEntity
):
    """Expose SIM registration state."""

    @property
    def is_on(self) -> bool | None:
        return state_as_bool(self.hub.state_for_role(ROLE_REGISTERED))
