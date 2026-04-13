"""Track restart-required state after the add-on syncs the custom integration."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN, ISSUE_ID_RESTART_REQUIRED, RESTART_REQUIRED_MARKER

_LOGGER = logging.getLogger(__name__)


def _manifest_path() -> Path:
    return Path(__file__).with_name("manifest.json")


def _current_component_version() -> str:
    try:
        payload = json.loads(_manifest_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return "unknown"
    version = payload.get("version")
    return str(version).strip() if version else "unknown"


def _marker_path(hass: HomeAssistant) -> Path:
    return Path(hass.config.path("custom_components", DOMAIN, RESTART_REQUIRED_MARKER))


def _read_marker(marker_path: Path) -> dict[str, Any] | None:
    if not marker_path.exists():
        return None
    try:
        payload = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as err:
        _LOGGER.warning("Could not read Q-Tronic restart marker %s: %s", marker_path, err)
        return None
    return payload if isinstance(payload, dict) else None


def _remove_marker(marker_path: Path) -> None:
    if marker_path.exists():
        marker_path.unlink()


async def async_sync_restart_issue(hass: HomeAssistant) -> None:
    """Create or clear the restart-required repair issue based on the sync marker."""
    marker_path = _marker_path(hass)
    marker = await hass.async_add_executor_job(_read_marker, marker_path)
    current_version = _current_component_version()

    if marker is None:
        ir.async_delete_issue(hass, DOMAIN, ISSUE_ID_RESTART_REQUIRED)
        return

    synced_version = str(marker.get("source_version") or "").strip() or "unknown"
    previous_version = str(marker.get("previous_version") or "").strip() or "brak"

    if synced_version == current_version:
        await hass.async_add_executor_job(_remove_marker, marker_path)
        ir.async_delete_issue(hass, DOMAIN, ISSUE_ID_RESTART_REQUIRED)
        _LOGGER.info(
            "Q-Tronic restart marker cleared because Home Assistant is already running component version %s",
            current_version,
        )
        return

    ir.async_create_issue(
        hass,
        DOMAIN,
        ISSUE_ID_RESTART_REQUIRED,
        is_fixable=False,
        is_persistent=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key="restart_required",
        translation_placeholders={
            "current_version": current_version,
            "synced_version": synced_version,
            "previous_version": previous_version,
        },
    )
