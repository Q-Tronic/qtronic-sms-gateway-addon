"""Rule model tests for a Home Assistant development environment."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("homeassistant")

from homeassistant.core import HomeAssistant, State  # noqa: E402

from qtronic_sms_gateway.ha_custom_components.qtronic_sms_gateway.config_flow import (  # noqa: E402
    QTronicSmsGatewayOptionsFlow,
)
from qtronic_sms_gateway.ha_custom_components.qtronic_sms_gateway.const import (  # noqa: E402
    CONF_SMS_COMMAND_RULES,
    SMS_RULE_ENTITY_ID,
)

from qtronic_sms_gateway.ha_custom_components.qtronic_sms_gateway.recipients import (  # noqa: E402
    SavedRecipient,
)
from qtronic_sms_gateway.ha_custom_components.qtronic_sms_gateway.sms_commands import (  # noqa: E402
    DEFAULT_STATE_REPLY,
    action_supports_domain,
    find_rule_collisions,
    load_sms_command_rules,
    match_rule_message,
    render_reply_template,
    reply_template_values_many,
    sms_rule_matches_sender,
    validate_reply_template,
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


def test_multi_entity_reply_variables_follow_selection_order() -> None:
    states = [
        State(
            "sensor.temperatura_salon",
            "21.5",
            {
                "friendly_name": "Temperatura salon",
                "unit_of_measurement": "°C",
            },
        ),
        State(
            "binary_sensor.brama",
            "off",
            {"friendly_name": "Brama", "device_class": "garage_door"},
        ),
    ]

    values = reply_template_values_many(
        states,
        data_czas="11.08.2026 - 12:34:56",
        sender_name="Przemek",
        command="status domu",
        value="",
        entity_ids=("sensor.temperatura_salon", "binary_sensor.brama"),
    )

    # Legacy multi-entity variables retain their pre-0.5.1 aggregate meaning.
    assert values["stan"] == (
        "Temperatura salon: 21.5 °C; Brama: zamknięta"
    )
    assert values["jednostka"] == ""
    assert values["entity_id"] == (
        "sensor.temperatura_salon, binary_sensor.brama"
    )
    # Indexed variables describe the matching item in selector order.
    assert values["stan_1"] == "21.5"
    assert values["jednostka_1"] == "°C"
    assert values["stan_2"] == "zamknięta"
    assert values["entity_id_2"] == "binary_sensor.brama"
    assert values["wynik_2"] == "Brama: zamknięta"
    assert values["wynik"] == (
        "Temperatura salon: 21.5 °C; Brama: zamknięta"
    )
    assert render_reply_template(DEFAULT_STATE_REPLY, values) == values["wynik"]
    assert render_reply_template(
        "{nazwa_encji}: {stan} {jednostka}", values
    ) == (
        "Temperatura salon, Brama: "
        "Temperatura salon: 21.5 °C; Brama: zamknięta"
    )
    assert render_reply_template(
        "Salon: {stan_1} {jednostka_1}; brama: {stan_2}", values
    ) == "Salon: 21.5 °C; brama: zamknięta"


def test_reply_template_rejects_index_beyond_selected_entities() -> None:
    validate_reply_template("{stan_1}; {wynik_2}", entity_count=2)
    with pytest.raises(ValueError, match="has 2 target entities"):
        validate_reply_template("{stan_3}", entity_count=2)
    with pytest.raises(ValueError, match="Unsupported reply template field"):
        validate_reply_template("{stan_21}", entity_count=20)


def test_missing_entity_keeps_index_and_never_breaks_failure_template() -> None:
    brama = State(
        "binary_sensor.brama",
        "on",
        {"friendly_name": "Brama", "device_class": "garage_door"},
    )
    values = reply_template_values_many(
        [None, brama],
        data_czas="11.08.2026 - 12:34:56",
        sender_name="Przemek",
        command="status domu",
        value="",
        entity_ids=("sensor.temperatura_salon", "binary_sensor.brama"),
    )

    assert values["entity_id_1"] == "sensor.temperatura_salon"
    assert values["stan_1"] == "nieznany"
    assert values["entity_id_2"] == "binary_sensor.brama"
    assert values["stan_2"] == "otwarta"
    assert render_reply_template("{stan_1}; {stan_2}; {stan_20}", values) == (
        "nieznany; otwarta;"
    )


def test_persisted_rule_rejects_index_beyond_its_targets() -> None:
    assert load_sms_command_rules(
        [_legacy_rule(success_reply="{stan_2}")]
    ) == ()


def test_edit_validation_error_keeps_new_entity_selection() -> None:
    """A failed edit must not restore targets from the persisted rule."""

    class _TestOptionsFlow(QTronicSmsGatewayOptionsFlow):
        @property
        def config_entry(self):
            return self._test_entry

    async def _run() -> None:
        hass = HomeAssistant("/tmp/qtronic-sms-gateway-test")
        hass.states.async_set(
            "sensor.old_target", "1", {"friendly_name": "Old target"}
        )
        hass.states.async_set(
            "sensor.new_target", "2", {"friendly_name": "New target"}
        )
        existing = load_sms_command_rules(
            [
                _legacy_rule(
                    id="edit-targets",
                    sender_mode="manual",
                    saved_recipient_id="",
                    sender_phone="+48500100200",
                    entity_id="sensor.old_target",
                    entity_ids=["sensor.old_target"],
                )
            ]
        )[0]
        flow = _TestOptionsFlow()
        flow.hass = hass
        flow._test_entry = SimpleNamespace(
            entry_id="test-entry",
            data={},
            options={CONF_SMS_COMMAND_RULES: [existing.as_dict()]},
        )
        flow._editing_sms_rule_id = existing.id
        submitted = existing.as_dict()
        submitted[SMS_RULE_ENTITY_ID] = ["sensor.new_target"]
        submitted["action"] = "report_state"
        submitted["success_reply"] = "{stan_2}"

        result = await flow.async_step_edit_sms_rule(submitted)

        assert result["errors"] == {"base": "invalid_reply_template"}
        mapping = result["description_placeholders"]["mapowanie_encji"]
        assert "sensor.new_target" in mapping
        assert "sensor.old_target" not in mapping
        entity_marker = next(
            marker
            for marker in result["data_schema"].schema
            if str(marker.schema) == SMS_RULE_ENTITY_ID
        )
        assert entity_marker.default() == ["sensor.new_target"]

    asyncio.run(_run())
