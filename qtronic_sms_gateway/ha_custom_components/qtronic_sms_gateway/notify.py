"""Notify platform for saved recipients in Q-Tronic SMS Gateway."""

from __future__ import annotations

from homeassistant.components.notify import NotifyEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .hub import QTronicSmsGatewayHub
from .recipients import SavedRecipient


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up notify entities for saved recipients."""
    hub: QTronicSmsGatewayHub = entry.runtime_data
    async_add_entities(
        [QTronicSmsGatewayRecipientNotifyEntity(hub, recipient) for recipient in hub.saved_recipients]
    )


class QTronicSmsGatewayRecipientNotifyEntity(NotifyEntity):
    """Notify entity bound to one saved recipient."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:account-arrow-right"

    def __init__(self, hub: QTronicSmsGatewayHub, recipient: SavedRecipient) -> None:
        self.hub = hub
        self.recipient = recipient
        self._attr_name = recipient.name
        self._attr_unique_id = hub.notify_unique_id_for_recipient(recipient.id)
        self._remove_listener = None

    @property
    def available(self) -> bool:
        return self.hub.available and self.hub.can_send_with_default_encoding

    @property
    def device_info(self):
        return self.hub.ha_device_info

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        return {
            "config_entry_id": self.hub.entry.entry_id,
            "recipient_id": self.recipient.id,
            "recipient_name": self.recipient.name,
            "recipient_phone": self.recipient.phone,
            "sms_encoding": self.hub.sms_encoding,
            "send_sms_action": self.hub.send_sms_action,
            "unicode_send_sms_action": self.hub.unicode_send_sms_action,
            "unicode_available": str(self.hub.can_send_unicode_sms).lower(),
            "host": self.hub.host,
        }

    async def async_added_to_hass(self) -> None:
        self._remove_listener = self.hub.async_add_listener(self._handle_hub_update)

    async def async_will_remove_from_hass(self) -> None:
        if self._remove_listener is not None:
            self._remove_listener()
            self._remove_listener = None

    async def async_send_message(self, message: str, title: str | None = None) -> None:
        """Send an SMS to this saved recipient."""
        await self.hub.async_send_sms(message=message, recipient=self.recipient.phone)

    def _handle_hub_update(self) -> None:
        self.async_write_ha_state()
