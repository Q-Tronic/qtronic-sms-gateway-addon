"""Pure security regression tests that do not require Home Assistant."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

MODULE_PATH = (
    Path(__file__).parents[2]
    / "qtronic_sms_gateway"
    / "ha_custom_components"
    / "qtronic_sms_gateway"
    / "security.py"
)
SPEC = importlib.util.spec_from_file_location("qtronic_security", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
security = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(security)


def test_pin_is_salted_and_verified() -> None:
    first = security.hash_pin("1234")
    second = security.hash_pin("1234")

    assert first != second
    assert "1234" not in first
    assert security.verify_pin("1234", first)
    assert not security.verify_pin("1235", first)


@pytest.mark.parametrize("pin", ["123", "1234567890123", "12ab"])
def test_invalid_pin_rejected(pin: str) -> None:
    with pytest.raises(ValueError):
        security.hash_pin(pin)


def test_authorization_requires_full_number() -> None:
    assert security.authorization_numbers_match("+48500100200", "0048500100200")
    assert not security.authorization_numbers_match("500100200", "+48500100200")
    assert not security.authorization_numbers_match("500100200", "500100200")
    assert not security.authorization_numbers_match("100200", "+48500100200")


def test_trailing_pin_format() -> None:
    assert security.split_trailing_pin("ustaw salon 22 1234") == (
        "ustaw salon 22",
        "1234",
    )
    assert security.split_trailing_pin("ustaw salon 22") == ("ustaw salon 22", "")
    assert security.split_trailing_pin("temperatura salon") == (
        "temperatura salon",
        "",
    )
