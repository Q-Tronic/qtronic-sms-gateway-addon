"""The Q-Tronic SMS Gateway integration."""

from __future__ import annotations

from collections.abc import Callable
import logging
from uuid import uuid4

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import (
    ATTR_ENCODING,
    ATTR_MESSAGE,
    ATTR_RECIPIENT,
    ATTR_RING_TIME_S,
    ATTR_SAVED_RECIPIENTS,
    CONF_CONFIG_ENTRY_ID,
    DOMAIN,
    SERVICE_CALL_TO,
    SERVICE_SEND_SMS,
    SMS_ENCODINGS,
)
from .hub import GatewayAuthenticationError, GatewayConnectionError, QTronicSmsGatewayHub
from .recipients import deduplicate_phone_numbers, mask_phone_number

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.NOTIFY,
    Platform.SENSOR,
]

SEND_SMS_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_MESSAGE): cv.string,
        vol.Optional(ATTR_RECIPIENT): cv.string,
        vol.Optional(ATTR_SAVED_RECIPIENTS): cv.entity_ids,
        vol.Optional(ATTR_ENCODING): vol.In(SMS_ENCODINGS),
        vol.Optional(CONF_CONFIG_ENTRY_ID): cv.string,
    }
)

CALL_TO_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_RECIPIENT): cv.string,
        vol.Optional(ATTR_SAVED_RECIPIENTS): cv.entity_ids,
        vol.Optional(ATTR_RING_TIME_S): vol.All(vol.Coerce(int), vol.Range(min=1, max=3600)),
        vol.Optional(CONF_CONFIG_ENTRY_ID): cv.string,
    }
)


def _get_entries(hass: HomeAssistant) -> dict[str, QTronicSmsGatewayHub]:
    """Return configured gateway hubs or raise a user-facing error."""
    entries: dict[str, QTronicSmsGatewayHub] = hass.data.get(DOMAIN, {})
    if not entries:
        raise HomeAssistantError("No Q-Tronic SMS Gateway entries are configured.")
    return entries


def _resolve_saved_recipient_numbers(
    hass: HomeAssistant,
    entity_ids: list[str],
    entries: dict[str, QTronicSmsGatewayHub],
) -> dict[str, list[str]]:
    """Resolve selected saved recipient entities to phone numbers grouped by config entry."""
    grouped: dict[str, list[str]] = {}
    unresolved: list[str] = []
    registry = er.async_get(hass)

    for entity_id in entity_ids:
        state = hass.states.get(entity_id)
        if state is not None:
            config_entry_id = state.attributes.get("config_entry_id")
            recipient_phone = state.attributes.get("recipient_phone")
            if isinstance(config_entry_id, str) and isinstance(recipient_phone, str):
                grouped.setdefault(config_entry_id, []).append(recipient_phone)
                continue

        registry_entry = registry.async_get(entity_id)
        if registry_entry is None:
            unresolved.append(entity_id)
            continue

        for config_entry_id, hub in entries.items():
            recipient = hub.saved_recipient_for_notify_unique_id(registry_entry.unique_id)
            if recipient is None:
                continue
            grouped.setdefault(config_entry_id, []).append(recipient.phone)
            break
        else:
            unresolved.append(entity_id)

    if unresolved:
        raise HomeAssistantError(
            "Some selected saved recipients could not be resolved: " + ", ".join(unresolved)
        )

    return grouped


def _masked_numbers(phone_numbers: list[str]) -> list[str]:
    """Mask phone numbers for logs."""
    return [mask_phone_number(phone) for phone in deduplicate_phone_numbers(phone_numbers)]


def _resolve_targets_for_service(
    *,
    hass: HomeAssistant,
    entries: dict[str, QTronicSmsGatewayHub],
    entry_id: str | None,
    manual_recipient: str | None,
    selected_entities: list[str],
    default_numbers_resolver: Callable[[QTronicSmsGatewayHub], list[str]],
    entity_kind_label: str,
) -> list[tuple[QTronicSmsGatewayHub, list[str]]]:
    """Resolve saved/manual recipients to concrete per-gateway phone number lists."""
    def _validated_numbers(numbers: list[str]) -> list[str]:
        resolved = deduplicate_phone_numbers(numbers)
        if resolved:
            return resolved
        raise HomeAssistantError(
            "No recipients were resolved for this action. Select at least one saved "
            "recipient, enter a manual number, or configure defaults for this action."
        )

    grouped_selected_numbers = _resolve_saved_recipient_numbers(hass, selected_entities, entries)

    if entry_id:
        hub = entries.get(entry_id)
        if hub is None:
            raise HomeAssistantError(
                f"Config entry '{entry_id}' was not found for domain '{DOMAIN}'."
            )

        numbers = list(grouped_selected_numbers.pop(entry_id, []))
        if grouped_selected_numbers:
            raise HomeAssistantError(
                f"Selected saved {entity_kind_label} belong to a different Q-Tronic SMS Gateway "
                "than the provided config_entry_id."
            )
        if manual_recipient:
            numbers.append(manual_recipient)
        if not numbers:
            numbers = default_numbers_resolver(hub)
        return [(hub, _validated_numbers(numbers))]

    if grouped_selected_numbers:
        if manual_recipient and len(grouped_selected_numbers) != 1:
            raise HomeAssistantError(
                "Manual recipient cannot be combined with saved recipients from multiple "
                "Q-Tronic SMS Gateway entries. Provide config_entry_id."
            )

        resolved: list[tuple[QTronicSmsGatewayHub, list[str]]] = []
        for selected_entry_id, numbers in grouped_selected_numbers.items():
            if manual_recipient:
                numbers.append(manual_recipient)
            resolved.append(
                (
                    entries[selected_entry_id],
                    _validated_numbers(numbers),
                )
            )
        return resolved

    if len(entries) != 1:
        raise HomeAssistantError(
            "Multiple Q-Tronic SMS Gateway entries are configured. Provide config_entry_id "
            "or select saved recipients from one gateway."
        )

    hub = next(iter(entries.values()))
    numbers = [manual_recipient] if manual_recipient else default_numbers_resolver(hub)
    return [(hub, _validated_numbers(numbers))]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the integration from YAML."""
    if not hass.services.has_service(DOMAIN, SERVICE_SEND_SMS):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SEND_SMS,
            _make_send_sms_handler(hass),
            schema=SEND_SMS_SCHEMA,
        )
    if not hass.services.has_service(DOMAIN, SERVICE_CALL_TO):
        hass.services.async_register(
            DOMAIN,
            SERVICE_CALL_TO,
            _make_call_to_handler(hass),
            schema=CALL_TO_SCHEMA,
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Q-Tronic SMS Gateway from a config entry."""
    hub = QTronicSmsGatewayHub(hass, entry)
    try:
        await hub.async_start()
    except GatewayAuthenticationError as err:
        raise ConfigEntryAuthFailed from err
    except GatewayConnectionError as err:
        raise ConfigEntryNotReady(str(err)) from err

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = hub
    entry.runtime_data = hub
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, hub.unique_id_prefix)},
        manufacturer="Q-Tronic",
        model="SMS Gateway",
        name=entry.title or "Q-Tronic SMS Gateway",
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    hub: QTronicSmsGatewayHub = entry.runtime_data
    await hub.async_stop()
    hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


def _make_send_sms_handler(hass: HomeAssistant):
    async def _handle_send_sms(call: ServiceCall) -> None:
        entries = _get_entries(hass)
        entry_id = call.data.get(CONF_CONFIG_ENTRY_ID)
        manual_recipient = call.data.get(ATTR_RECIPIENT)
        selected_entities = call.data.get(ATTR_SAVED_RECIPIENTS, [])
        if isinstance(selected_entities, str):
            selected_entities = [selected_entities]

        message = call.data[ATTR_MESSAGE]
        encoding = call.data.get(ATTR_ENCODING)
        batch_id = uuid4().hex[:8]

        _LOGGER.info(
            "SMS batch %s requested: selected_entities=%s manual_recipient=%s encoding=%s entry_id=%s",
            batch_id,
            selected_entities,
            mask_phone_number(manual_recipient) if manual_recipient else "-",
            encoding or "default",
            entry_id or "auto",
        )

        resolved_targets = _resolve_targets_for_service(
            hass=hass,
            entries=entries,
            entry_id=entry_id,
            manual_recipient=manual_recipient,
            selected_entities=selected_entities,
            default_numbers_resolver=lambda hub: hub.default_phone_numbers,
            entity_kind_label="recipients",
        )
        for hub, numbers in resolved_targets:
            _LOGGER.info(
                "SMS batch %s resolved %s recipients for gateway %s: %s",
                batch_id,
                len(numbers),
                hub.host,
                _masked_numbers(numbers),
            )
            await hub.async_send_sms_batch(
                message=message,
                recipients=numbers,
                encoding=encoding,
                batch_id=batch_id,
            )

    return _handle_send_sms


def _make_call_to_handler(hass: HomeAssistant):
    async def _handle_call_to(call: ServiceCall) -> None:
        entries = _get_entries(hass)
        entry_id = call.data.get(CONF_CONFIG_ENTRY_ID)
        manual_recipient = call.data.get(ATTR_RECIPIENT)
        selected_entities = call.data.get(ATTR_SAVED_RECIPIENTS, [])
        if isinstance(selected_entities, str):
            selected_entities = [selected_entities]
        ring_time_s = call.data.get(ATTR_RING_TIME_S)
        batch_id = uuid4().hex[:8]

        _LOGGER.info(
            "Call batch %s requested: selected_entities=%s manual_recipient=%s ring_time_s=%s entry_id=%s",
            batch_id,
            selected_entities,
            mask_phone_number(manual_recipient) if manual_recipient else "-",
            ring_time_s or "default",
            entry_id or "auto",
        )

        resolved_targets = _resolve_targets_for_service(
            hass=hass,
            entries=entries,
            entry_id=entry_id,
            manual_recipient=manual_recipient,
            selected_entities=selected_entities,
            default_numbers_resolver=lambda hub: [],
            entity_kind_label="recipients",
        )
        for hub, numbers in resolved_targets:
            _LOGGER.info(
                "Call batch %s resolved %s recipients for gateway %s: %s",
                batch_id,
                len(numbers),
                hub.host,
                _masked_numbers(numbers),
            )
            await hub.async_call_batch(
                recipients=numbers,
                ring_time_s=ring_time_s,
                batch_id=batch_id,
            )

    return _handle_call_to
