"""SMS encoding helpers for the Q-Tronic SMS Gateway integration."""

from __future__ import annotations

import unicodedata

from .const import ENCODING_AUTO, ENCODING_PASSTHROUGH, ENCODING_TRANSLITERATE, ENCODING_UCS2, SMS_ENCODINGS

POLISH_TRANSLITERATION = str.maketrans(
    {
        "ą": "a",
        "ć": "c",
        "ę": "e",
        "ł": "l",
        "ń": "n",
        "ó": "o",
        "ś": "s",
        "ź": "z",
        "ż": "z",
        "Ą": "A",
        "Ć": "C",
        "Ę": "E",
        "Ł": "L",
        "Ń": "N",
        "Ó": "O",
        "Ś": "S",
        "Ź": "Z",
        "Ż": "Z",
        "–": "-",
        "—": "-",
        "„": "\"",
        "”": "\"",
        "’": "'",
        "•": "*",
    }
)


def normalize_encoding(value: str | None) -> str:
    """Normalize and validate the requested SMS encoding mode."""
    normalized = (value or ENCODING_AUTO).strip().lower()
    if normalized not in SMS_ENCODINGS:
        raise ValueError(f"Unsupported SMS encoding mode: {value}")
    return normalized


def message_needs_unicode(message: str) -> bool:
    """Return True when the message contains non-ASCII characters."""
    return any(ord(char) > 127 for char in message)


def transliterate_sms_text(message: str) -> str:
    """Replace Polish diacritics and strip unsupported accents."""
    translated = message.translate(POLISH_TRANSLITERATION)
    normalized = unicodedata.normalize("NFKD", translated)
    return normalized.encode("ascii", "ignore").decode("ascii")


def normalize_inbound_text(message: str) -> str:
    """Normalize inbound text for case-insensitive command matching."""
    return " ".join(transliterate_sms_text(message).casefold().split())


def encode_sms_ucs2(message: str) -> str:
    """Encode SMS text to UCS2 hex for SIM800 text mode."""
    return message.encode("utf-16-be").hex().upper()


def resolve_auto_encoding(message: str, unicode_available: bool) -> str:
    """Select the best transport for a message in auto mode."""
    if not message_needs_unicode(message):
        return ENCODING_PASSTHROUGH
    if unicode_available:
        return ENCODING_UCS2
    return ENCODING_TRANSLITERATE
