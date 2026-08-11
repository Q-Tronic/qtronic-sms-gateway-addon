"""Regression tests for Home Assistant translation rich-text syntax."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

TRANSLATIONS_DIR = (
    Path(__file__).parents[2]
    / "qtronic_sms_gateway"
    / "ha_custom_components"
    / "qtronic_sms_gateway"
    / "translations"
)
RICH_TEXT_TAG = re.compile(
    r"<(?P<closing>/)?(?P<name>[A-Za-z][\w-]*)(?:\s[^>]*)?(?P<self_closing>/)?>"
)


def _iter_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _iter_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_strings(child)


def test_translation_rich_text_tags_are_balanced() -> None:
    """Prevent frontend ``UNCLOSED_TAG`` errors in config-flow descriptions."""
    for path in TRANSLATIONS_DIR.glob("*.json"):
        translations = json.loads(path.read_text(encoding="utf-8"))
        for text in _iter_strings(translations):
            stack: list[str] = []
            for match in RICH_TEXT_TAG.finditer(text):
                name = match.group("name")
                if match.group("self_closing"):
                    continue
                if match.group("closing"):
                    assert stack and stack[-1] == name, (
                        f"Unmatched closing tag </{name}> in {path.name}: {text!r}"
                    )
                    stack.pop()
                else:
                    stack.append(name)
            assert not stack, (
                f"Unclosed rich-text tag(s) {stack!r} in {path.name}: {text!r}"
            )
