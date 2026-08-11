"""Home Assistant Repairs issues for gateway configuration/capabilities."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

if TYPE_CHECKING:
    from .hub import QTronicSmsGatewayHub

ISSUE_API_TOKEN_MISSING = "api_token_missing"
ISSUE_SMS_CAPABILITY_MISSING = "send_sms_capability_missing"


async def async_sync_health_issues(
    hass: HomeAssistant, hub: QTronicSmsGatewayHub
) -> None:
    """Create actionable issues and clear them automatically after recovery."""
    token_issue = f"{ISSUE_API_TOKEN_MISSING}_{hub.entry.entry_id}"
    if hub.api_token_configured:
        ir.async_delete_issue(hass, DOMAIN, token_issue)
    else:
        ir.async_create_issue(
            hass,
            DOMAIN,
            token_issue,
            is_fixable=False,
            is_persistent=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_API_TOKEN_MISSING,
        )

    capability_issue = f"{ISSUE_SMS_CAPABILITY_MISSING}_{hub.entry.entry_id}"
    if hub.can_send_sms or hub.can_send_unicode_sms:
        ir.async_delete_issue(hass, DOMAIN, capability_issue)
    else:
        ir.async_create_issue(
            hass,
            DOMAIN,
            capability_issue,
            is_fixable=False,
            is_persistent=True,
            severity=ir.IssueSeverity.ERROR,
            translation_key=ISSUE_SMS_CAPABILITY_MISSING,
        )
