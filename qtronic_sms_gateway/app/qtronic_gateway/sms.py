"""SMS encoding helpers for the Q-Tronic SMS Gateway add-on."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import unicodedata

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
        "„": '"',
        "”": '"',
        "’": "'",
        "•": "*",
    }
)

ENCODING_AUTO = "auto"
ENCODING_PASSTHROUGH = "passthrough"
ENCODING_TRANSLITERATE = "transliterate"
ENCODING_UCS2 = "ucs2"
SMS_ENCODINGS = (
    ENCODING_AUTO,
    ENCODING_PASSTHROUGH,
    ENCODING_TRANSLITERATE,
    ENCODING_UCS2,
)

# GSM 03.38 default alphabet. Characters in the extension table consume two
# septets, which matters for multipart billing and modem limits.
GSM7_BASIC = frozenset(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ "
    "!\"#¤%&'()*+,-./0123456789:;<=>?¡"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿"
    "abcdefghijklmnopqrstuvwxyzäöñüà"
)
GSM7_EXTENSION = frozenset("^{}\\[~]|\f€")


@dataclass(frozen=True, slots=True)
class SmsSegmentInfo:
    """Length and multipart information for one outgoing message."""

    alphabet: str
    encoding: str
    characters: int
    units: int
    segments: int
    single_capacity: int
    multipart_capacity: int

    def as_dict(self) -> dict[str, str | int]:
        return asdict(self)


def _gsm7_units(message: str) -> int | None:
    units = 0
    for character in message:
        if character in GSM7_BASIC:
            units += 1
        elif character in GSM7_EXTENSION:
            units += 2
        else:
            return None
    return units


def _ucs2_units(message: str) -> int:
    # UTF-16 code units are what the 70/67 character SMS capacities count.
    return len(message.encode("utf-16-be")) // 2


def sms_segment_info(
    message: str,
    encoding: str | None = None,
    *,
    unicode_available: bool = True,
) -> SmsSegmentInfo:
    """Return an accurate GSM-7/UCS2 segment count for a message."""
    mode = normalize_encoding(encoding)
    measured_message = message
    if mode == ENCODING_AUTO:
        mode = resolve_auto_encoding(message, unicode_available)
    if mode == ENCODING_TRANSLITERATE:
        measured_message = transliterate_sms_text(message)

    gsm_units = _gsm7_units(measured_message) if mode != ENCODING_UCS2 else None
    if gsm_units is not None:
        segments = 1 if gsm_units <= 160 else math.ceil(gsm_units / 153)
        return SmsSegmentInfo(
            alphabet="gsm7",
            encoding=mode,
            characters=len(measured_message),
            units=gsm_units,
            segments=max(1, segments),
            single_capacity=160,
            multipart_capacity=153,
        )

    units = _ucs2_units(message)
    segments = 1 if units <= 70 else math.ceil(units / 67)
    return SmsSegmentInfo(
        alphabet="ucs2",
        encoding=ENCODING_UCS2,
        characters=len(message),
        units=units,
        segments=max(1, segments),
        single_capacity=70,
        multipart_capacity=67,
    )


def split_sms_message(
    message: str,
    info: SmsSegmentInfo,
) -> list[str]:
    """Split a long message at alphabet-unit boundaries when explicitly enabled."""
    if info.segments <= 1:
        return [message]
    capacity = info.multipart_capacity
    chunks: list[str] = []
    current: list[str] = []
    current_units = 0
    for character in message:
        if info.alphabet == "gsm7":
            character_units = 2 if character in GSM7_EXTENSION else 1
        else:
            character_units = _ucs2_units(character)
        if current and current_units + character_units > capacity:
            chunks.append("".join(current))
            current = []
            current_units = 0
        current.append(character)
        current_units += character_units
    if current:
        chunks.append("".join(current))
    return chunks


def normalize_encoding(value: str | None) -> str:
    """Normalize and validate the requested SMS encoding mode."""
    normalized = (value or ENCODING_AUTO).strip().lower()
    if normalized not in SMS_ENCODINGS:
        raise ValueError(f"Unsupported SMS encoding mode: {value}")
    return normalized


def message_needs_unicode(message: str) -> bool:
    """Return True when the message cannot be represented by GSM 03.38."""
    return _gsm7_units(message) is None


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
    if _gsm7_units(message) is not None:
        return ENCODING_PASSTHROUGH
    if unicode_available:
        return ENCODING_UCS2
    return ENCODING_TRANSLITERATE
