"""Concurrency regression tests for inbound SMS command cooldowns."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
import unittest

try:
    from homeassistant.const import STATE_OFF, STATE_ON
    from homeassistant.core import Event, State
except ImportError as err:  # pragma: no cover - local environment without HA
    raise unittest.SkipTest("Home Assistant is not installed") from err

from qtronic_sms_gateway.ha_custom_components.qtronic_sms_gateway.const import (
    CONF_SMS_COMMAND_RULES,
    EVENT_SMS_RECEIVED,
)
from qtronic_sms_gateway.ha_custom_components.qtronic_sms_gateway.sms_commands import (
    SmsCommandRuleEngine,
)


class _FakeStates:
    def __init__(self) -> None:
        self.values = {"switch.salon": State("switch.salon", STATE_OFF)}

    def get(self, entity_id: str) -> State | None:
        return self.values.get(entity_id)


class _FakeBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def async_fire(self, event_type: str, data: dict) -> None:
        self.events.append((event_type, data))


class _FakeServices:
    def __init__(self, states: _FakeStates) -> None:
        self.states = states
        self.calls = 0

    def has_service(self, domain: str, service: str) -> bool:
        return domain == "switch" and service == "turn_on"

    async def async_call(
        self,
        domain: str,
        service: str,
        data: dict,
        *,
        blocking: bool,
        context,
    ) -> None:
        self.calls += 1
        # Keep the first execution in flight long enough for the duplicate
        # event to reach and wait on the engine's execution lock.
        await asyncio.sleep(0.05)
        entity_id = data["entity_id"]
        self.states.values[entity_id] = State(entity_id, STATE_ON)


class _FakeHass:
    def __init__(self) -> None:
        self.states = _FakeStates()
        self.services = _FakeServices(self.states)
        self.bus = _FakeBus()


class _FakeHub:
    def __init__(self, hass: _FakeHass) -> None:
        self.entry = SimpleNamespace(
            entry_id="gateway-1",
            options={
                CONF_SMS_COMMAND_RULES: [
                    {
                        "id": "salon_on",
                        "name": "Salon on",
                        "enabled": True,
                        "sender_mode": "manual",
                        "saved_recipient_id": "",
                        "sender_phone": "+48500100200",
                        "command": "wlacz salon",
                        "match_mode": "exact",
                        "action": "turn_on",
                        "entity_id": "switch.salon",
                        "reply_enabled": False,
                        "success_reply": "OK",
                        "failure_reply": "",
                        "cooldown_s": 60,
                    }
                ]
            },
        )
        self.saved_recipient_map = {}
        self.gateway_host = "esp-sim800c"

    def notify_listeners(self) -> None:
        return


class SmsCommandConcurrencyTest(unittest.IsolatedAsyncioTestCase):
    """Exercise the real rule engine against Home Assistant core classes."""

    async def test_duplicate_events_execute_once_with_cooldown(self) -> None:
        hass = _FakeHass()
        hub = _FakeHub(hass)
        engine = SmsCommandRuleEngine(hass, hub)
        event_data = {
            "config_entry_id": hub.entry.entry_id,
            "sender": "+48500100200",
            "message": "WŁĄCZ SALON",
        }

        await asyncio.gather(
            engine._async_process_sms(Event(EVENT_SMS_RECEIVED, event_data)),
            engine._async_process_sms(Event(EVENT_SMS_RECEIVED, event_data)),
        )

        self.assertEqual(hass.services.calls, 1)
        self.assertEqual(engine.statistics["executed"], 1)
        self.assertEqual(engine.statistics["rate_limited"], 1)
        self.assertEqual(engine.statistics["last_error"], "cooldown")


if __name__ == "__main__":  # pragma: no cover - remote HA compatibility smoke
    unittest.main(verbosity=2)
