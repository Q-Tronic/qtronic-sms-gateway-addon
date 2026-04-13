"""Synchronize the vendored custom integration into Home Assistant config."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import shutil
from urllib import error as urllib_error
from urllib import request as urllib_request

_LOGGER = logging.getLogger("qtronic_sms_gateway.component_sync")

SOURCE_COMPONENT = Path(
    os.environ.get(
        "QTRONIC_COMPONENT_SOURCE_DIR",
        "/opt/qtronic/ha_custom_components/qtronic_sms_gateway",
    )
)
TARGET_CONFIG_ROOT = Path(os.environ.get("QTRONIC_HA_CONFIG_DIR", "/homeassistant"))
TARGET_COMPONENT = TARGET_CONFIG_ROOT / "custom_components" / "qtronic_sms_gateway"
TEMP_COMPONENT = TARGET_CONFIG_ROOT / "custom_components" / ".qtronic_sms_gateway.tmp"
MARKER_FILE = TARGET_COMPONENT / ".addon_sync_state.json"
NOTIFICATION_URL = "http://supervisor/core/api/services/persistent_notification/create"
NOTIFICATION_ID = "qtronic_sms_gateway_component_sync"
REQUIRED_FILES = (
    "__init__.py",
    "manifest.json",
    "config_flow.py",
    "hub.py",
    "services.yaml",
)


def _read_manifest_version(component_path: Path) -> str | None:
    manifest_path = component_path / "manifest.json"
    if not manifest_path.exists():
        return None
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    version = payload.get("version")
    return str(version).strip() if version else None


def _component_is_incomplete(component_path: Path) -> bool:
    if not component_path.exists():
        return True
    return any(not (component_path / relative_path).exists() for relative_path in REQUIRED_FILES)


def _send_restart_notification(source_version: str, previous_version: str | None) -> None:
    token = os.environ.get("SUPERVISOR_TOKEN", "").strip()
    if not token:
        _LOGGER.warning(
            "SUPERVISOR_TOKEN is missing; cannot create Home Assistant restart notification"
        )
        return

    previous_label = previous_version or "brak"
    payload = {
        "title": "Q-Tronic SMS Gateway",
        "message": (
            "Add-on zsynchronizowal custom_component Q-Tronic SMS Gateway do "
            "/config/custom_components. "
            f"Wersja poprzednia: {previous_label}, nowa wersja: {source_version}. "
            "Zrestartuj Home Assistant, aby przywrocic akcje send_sms/call_to, "
            "powiadomienia notify i triggery SMS/CALL."
        ),
        "notification_id": NOTIFICATION_ID,
    }
    request = urllib_request.Request(
        NOTIFICATION_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib_request.urlopen(request, timeout=10) as response:
            response.read()
        _LOGGER.info("Restart notification was sent to Home Assistant")
    except urllib_error.URLError as err:
        _LOGGER.warning("Failed to create restart notification in Home Assistant: %s", err)


def _write_restart_marker(source_version: str, previous_version: str | None) -> None:
    payload = {
        "source_version": source_version,
        "previous_version": previous_version,
        "addon_hostname": os.environ.get("HOSTNAME", "").strip(),
        "synced_at": datetime.now(timezone.utc).isoformat(),
    }
    MARKER_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _remove_restart_marker() -> None:
    if MARKER_FILE.exists():
        MARKER_FILE.unlink()


def sync_custom_component() -> dict[str, object]:
    """Copy the vendored integration into Home Assistant's config directory."""
    if not SOURCE_COMPONENT.exists():
        raise FileNotFoundError(
            f"Vendored custom component was not found in image: {SOURCE_COMPONENT}"
        )

    source_version = _read_manifest_version(SOURCE_COMPONENT) or "unknown"
    previous_version = _read_manifest_version(TARGET_COMPONENT)
    needs_restart_notice = (
        _component_is_incomplete(TARGET_COMPONENT) or previous_version != source_version
    )

    TARGET_COMPONENT.parent.mkdir(parents=True, exist_ok=True)
    if TEMP_COMPONENT.exists():
        shutil.rmtree(TEMP_COMPONENT)
    shutil.copytree(
        SOURCE_COMPONENT,
        TEMP_COMPONENT,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )

    if TARGET_COMPONENT.exists():
        shutil.rmtree(TARGET_COMPONENT)
    TEMP_COMPONENT.replace(TARGET_COMPONENT)

    _LOGGER.info(
        "Synchronized custom_component qtronic_sms_gateway to %s (version %s)",
        TARGET_COMPONENT,
        source_version,
    )

    if needs_restart_notice:
        _write_restart_marker(source_version, previous_version)
        _send_restart_notification(source_version, previous_version)
    else:
        _remove_restart_marker()

    return {
        "source_version": source_version,
        "previous_version": previous_version,
        "needs_restart_notice": needs_restart_notice,
        "target_path": str(TARGET_COMPONENT),
    }


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s: %(message)s",
    )
    try:
        result = sync_custom_component()
    except Exception as err:
        _LOGGER.exception("Custom component sync failed: %s", err)
        return 1

    if result["needs_restart_notice"]:
        _LOGGER.warning(
            "Home Assistant restart is required to pick up custom_component version %s",
            result["source_version"],
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
