"""Sensors for the Q-Tronic SMS Gateway integration."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ROLE_CALL_STATE,
    ROLE_INCOMING_CALL,
    ROLE_RSSI,
    ROLE_SMS_MESSAGE,
    ROLE_SMS_SENDER,
    ROLE_USSD,
)
from .entity import QTronicSmsGatewayEntity
from .hub import (
    CallBatchDiagnostics,
    QTronicSmsGatewayHub,
    SmsBatchDiagnostics,
    state_as_float,
    state_as_text,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Q-Tronic SMS Gateway sensors."""
    hub: QTronicSmsGatewayHub = entry.runtime_data
    entities: list[SensorEntity] = [
        QTronicSmsGatewayComponentStatusSensor(hub, "esp"),
        QTronicSmsGatewayComponentStatusSensor(hub, "sim800"),
        QTronicSmsGatewayLastBatchSensor(hub),
        QTronicSmsGatewayLastCallBatchSensor(hub),
    ]

    for role in (
        ROLE_RSSI,
        ROLE_SMS_SENDER,
        ROLE_SMS_MESSAGE,
        ROLE_INCOMING_CALL,
        ROLE_CALL_STATE,
        ROLE_USSD,
    ):
        info = hub.entity_info_for_role(role)
        if info is None:
            continue
        if role == ROLE_RSSI:
            entities.append(QTronicSmsGatewayRssiSensor(hub, info))
        else:
            entities.append(QTronicSmsGatewayTextValueSensor(hub, info, role))

    async_add_entities(entities)


class QTronicSmsGatewayComponentStatusSensor(SensorEntity):
    """Diagnostic status for the ESP or SIM800C layer."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False

    def __init__(self, hub: QTronicSmsGatewayHub, component: str) -> None:
        self.hub = hub
        self._component = component
        self._attr_unique_id = f"{hub.unique_id_prefix}_{component}_status"
        self._attr_name = "ESP Status" if component == "esp" else "SIM800C Status"
        self._attr_icon = "mdi:chip" if component == "esp" else "mdi:sim-alert"
        self._attr_options = (
            ["ok", "offline"]
            if component == "esp"
            else ["online", "offline", "not_registered", "unknown"]
        )
        self._remove_listener = None

    @property
    def available(self) -> bool:
        return True

    @property
    def device_info(self):
        return self.hub.ha_device_info

    @property
    def native_value(self) -> str:
        return self.hub.component_status(self._component)

    async def async_added_to_hass(self) -> None:
        self._remove_listener = self.hub.async_add_listener(self._handle_hub_update)

    async def async_will_remove_from_hass(self) -> None:
        if self._remove_listener is not None:
            self._remove_listener()
            self._remove_listener = None

    def _handle_hub_update(self) -> None:
        self.async_write_ha_state()


class QTronicSmsGatewayRssiSensor(QTronicSmsGatewayEntity, SensorEntity):
    """Signal strength sensor."""

    def __init__(self, hub: QTronicSmsGatewayHub, info) -> None:
        super().__init__(hub, info)
        self._attr_native_unit_of_measurement = info.unit_of_measurement or None
        if info.accuracy_decimals >= 0:
            self._attr_suggested_display_precision = info.accuracy_decimals

    @property
    def native_value(self) -> float | None:
        return state_as_float(self.hub.state_for_role(ROLE_RSSI))


class QTronicSmsGatewayTextValueSensor(QTronicSmsGatewayEntity, SensorEntity):
    """Read-only text-like sensor for SMS and call metadata."""

    def __init__(self, hub: QTronicSmsGatewayHub, info, role: str) -> None:
        super().__init__(hub, info)
        self._role = role

    @property
    def native_value(self) -> str | None:
        return state_as_text(self.hub.state_for_role(self._role))


class QTronicSmsGatewayLastBatchSensor(SensorEntity):
    """Diagnostic sensor with the last SMS batch result."""

    _attr_has_entity_name = True
    _attr_name = "Last SMS Batch"
    _attr_icon = "mdi:message-badge"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["idle", "in_progress", "success", "failed"]
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False

    def __init__(self, hub: QTronicSmsGatewayHub) -> None:
        self.hub = hub
        self._attr_unique_id = f"{hub.unique_id_prefix}_last_sms_batch"
        self._remove_listener = None

    @property
    def available(self) -> bool:
        return True

    @property
    def device_info(self):
        return self.hub.ha_device_info

    @property
    def native_value(self) -> str:
        return self.hub.last_sms_batch.status

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        batch: SmsBatchDiagnostics = self.hub.last_sms_batch
        return {
            "batch_id": batch.batch_id,
            "gateway_host": batch.gateway_host or self.hub.host,
            "queue_depth": self.hub.queued_job_count,
            "active_job_kind": self.hub.active_job_kind,
            "active_job_id": self.hub.active_job_id,
            "started_at": batch.started_at,
            "finished_at": batch.finished_at,
            "recipient_count": batch.recipient_count,
            "recipients": list(batch.recipients),
            "completed_recipients": list(batch.completed_recipients),
            "completed_recipient_count": len(batch.completed_recipients),
            "failed_recipient": batch.failed_recipient,
            "last_error": batch.last_error,
            "encoding": batch.encoding,
            "message_length": batch.message_length,
        }

    async def async_added_to_hass(self) -> None:
        self._remove_listener = self.hub.async_add_listener(self._handle_hub_update)

    async def async_will_remove_from_hass(self) -> None:
        if self._remove_listener is not None:
            self._remove_listener()
            self._remove_listener = None

    def _handle_hub_update(self) -> None:
        self.async_write_ha_state()


class QTronicSmsGatewayLastCallBatchSensor(SensorEntity):
    """Diagnostic sensor with the last call batch result."""

    _attr_has_entity_name = True
    _attr_name = "Last Call Batch"
    _attr_icon = "mdi:phone-log"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["idle", "in_progress", "success", "failed", "unknown"]
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False

    def __init__(self, hub: QTronicSmsGatewayHub) -> None:
        self.hub = hub
        self._attr_unique_id = f"{hub.unique_id_prefix}_last_call_batch"
        self._remove_listener = None

    @property
    def available(self) -> bool:
        return True

    @property
    def device_info(self):
        return self.hub.ha_device_info

    @property
    def native_value(self) -> str:
        return self.hub.last_call_batch.status

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        batch: CallBatchDiagnostics = self.hub.last_call_batch
        return {
            "batch_id": batch.batch_id,
            "gateway_host": batch.gateway_host or self.hub.host,
            "queue_depth": self.hub.queued_job_count,
            "active_job_kind": self.hub.active_job_kind,
            "active_job_id": self.hub.active_job_id,
            "started_at": batch.started_at,
            "finished_at": batch.finished_at,
            "recipient_count": batch.recipient_count,
            "recipients": list(batch.recipients),
            "completed_recipients": list(batch.completed_recipients),
            "completed_recipient_count": len(batch.completed_recipients),
            "failed_recipients": list(batch.failed_recipients),
            "unknown_recipients": list(batch.unknown_recipients),
            "failed_recipient": batch.failed_recipient,
            "last_error": batch.last_error,
            "ring_time_s": batch.ring_time_s,
            "attempts": {name: count for name, count in batch.attempts},
            "state_tracking_available": batch.state_tracking_available,
        }

    async def async_added_to_hass(self) -> None:
        self._remove_listener = self.hub.async_add_listener(self._handle_hub_update)

    async def async_will_remove_from_hass(self) -> None:
        if self._remove_listener is not None:
            self._remove_listener()
            self._remove_listener = None

    def _handle_hub_update(self) -> None:
        self.async_write_ha_state()
