"""Rule model tests for a Home Assistant development environment."""

from __future__ import annotations

import pytest

pytest.importorskip("homeassistant")

from qtronic_sms_gateway.ha_custom_components.qtronic_sms_gateway.recipients import (  # noqa: E402
    SavedRecipient,
)
from qtronic_sms_gateway.ha_custom_components.qtronic_sms_gateway.sms_commands import (  # noqa: E402
    action_supports_domain,
    find_rule_collisions,
    load_sms_command_rules,
    match_rule_message,
    sms_rule_matches_sender,
)


def _legacy_rule(**overrides):
    value = {
        "id": "salon",
        "name": "Salon",
        "enabled": True,
        "sender_mode": "saved",
        "saved_recipient_id": "owner",
        "sender_phone": "",
        "command": "ustaw salon {value}",
        "match_mode": "exact",
        "action": "set_value",
        "entity_id": "number.temperatura_salon",
        "reply_enabled": True,
        "success_reply": "Ustawiono {value}; {wynik}",
        "failure_reply": "Błąd",
    }
    value.update(overrides)
    return value


def test_legacy_single_entity_rule_migrates_to_targets() -> None:
    rule = load_sms_command_rules([_legacy_rule()])[0]
    assert rule.targets == ("number.temperatura_salon",)
    assert rule.as_dict()["entity_ids"] == ["number.temperatura_salon"]


def test_value_capture_ignores_case_and_polish_diacritics() -> None:
    rule = load_sms_command_rules([_legacy_rule(command="ustaw żaluzję {value}")])[0]
    assert match_rule_message(rule, "USTAW ZALUZJE 42") == "42"


def test_sender_authorization_does_not_suffix_match() -> None:
    rule = load_sms_command_rules([_legacy_rule()])[0]
    contacts = {"owner": SavedRecipient("owner", "Owner", "+48500100200")}
    assert sms_rule_matches_sender(rule, "+48500100200", contacts)
    assert not sms_rule_matches_sender(rule, "500100200", contacts)


def test_collision_and_domain_allowlist() -> None:
    first, second = load_sms_command_rules(
        [_legacy_rule(), _legacy_rule(id="salon_2", command="ustaw salon")]
    )
    assert find_rule_collisions((first, second)) == [(first.id, second.id)]
    assert action_supports_domain("unlock", "lock")
    assert not action_supports_domain("unlock", "switch")
