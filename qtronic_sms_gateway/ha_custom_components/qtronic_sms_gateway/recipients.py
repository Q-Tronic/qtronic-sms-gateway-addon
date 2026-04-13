"""Saved recipient helpers for the Q-Tronic SMS Gateway integration."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
import unicodedata
from typing import Any

from homeassistant.helpers.selector import SelectOptionDict

from .const import RECIPIENT_ID, RECIPIENT_NAME, RECIPIENT_PHONE

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_PHONE_ALLOWED_RE = re.compile(r"[^0-9+]")
_PHONE_DIGITS_RE = re.compile(r"[^0-9]")


@dataclass(frozen=True, slots=True)
class SavedRecipient:
    """A named SMS recipient saved in integration options."""

    id: str
    name: str
    phone: str

    def as_dict(self) -> dict[str, str]:
        """Serialize to config-entry-safe dict."""
        return asdict(self)


def normalize_phone_number(value: str) -> str:
    """Normalize a phone number to a compact SMS-friendly form."""
    compact = _PHONE_ALLOWED_RE.sub("", value.strip())
    if compact.count("+") > 1:
        raise ValueError("Phone number can contain at most one plus sign.")
    if "+" in compact and not compact.startswith("+"):
        raise ValueError("Plus sign is only allowed at the beginning of the phone number.")
    digits = compact[1:] if compact.startswith("+") else compact
    if len(digits) < 5 or len(digits) > 20:
        raise ValueError("Phone number must contain between 5 and 20 digits.")
    if not digits.isdigit():
        raise ValueError("Phone number must contain only digits and an optional leading plus.")
    return compact


def normalize_phone_number_loose(value: str) -> str:
    """Normalize a phone number for matching without strict validation."""
    return _PHONE_ALLOWED_RE.sub("", value.strip())


def phone_match_key(value: str) -> str:
    """Create a comparison-friendly phone key."""
    digits = _PHONE_DIGITS_RE.sub("", value.strip())
    if digits.startswith("00"):
        digits = digits[2:]
    return digits


def phone_numbers_match(left: str, right: str) -> bool:
    """Return True if two phone numbers likely refer to the same sender."""
    left_key = phone_match_key(left)
    right_key = phone_match_key(right)
    if not left_key or not right_key:
        return False
    if left_key == right_key:
        return True

    shorter, longer = sorted((left_key, right_key), key=len)
    return len(shorter) >= 7 and longer.endswith(shorter)


def normalize_recipient_name(value: str) -> str:
    """Normalize a recipient display name."""
    name = " ".join(value.strip().split())
    if not name:
        raise ValueError("Recipient name cannot be empty.")
    return name


def slugify_recipient_name(value: str) -> str:
    """Build a stable ASCII slug from the recipient name."""
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    slug = _NON_ALNUM_RE.sub("_", normalized.lower()).strip("_")
    return slug or "recipient"


def make_recipient_id(name: str, existing_ids: set[str]) -> str:
    """Create a unique saved-recipient ID from the display name."""
    base = slugify_recipient_name(name)
    candidate = base
    index = 2
    while candidate in existing_ids:
        candidate = f"{base}_{index}"
        index += 1
    return candidate


def load_saved_recipients(raw_value: Any) -> tuple[SavedRecipient, ...]:
    """Load saved recipients from config entry options."""
    if not isinstance(raw_value, list):
        return ()

    recipients: list[SavedRecipient] = []
    seen_ids: set[str] = set()
    for item in raw_value:
        if not isinstance(item, dict):
            continue
        name = item.get(RECIPIENT_NAME)
        phone = item.get(RECIPIENT_PHONE)
        recipient_id = item.get(RECIPIENT_ID)
        if not isinstance(name, str) or not isinstance(phone, str):
            continue
        try:
            clean_name = normalize_recipient_name(name)
            clean_phone = normalize_phone_number(phone)
        except ValueError:
            continue

        if not isinstance(recipient_id, str) or not recipient_id.strip():
            recipient_id = make_recipient_id(clean_name, seen_ids)
        recipient_id = slugify_recipient_name(recipient_id)
        if recipient_id in seen_ids:
            recipient_id = make_recipient_id(clean_name, seen_ids)

        seen_ids.add(recipient_id)
        recipients.append(SavedRecipient(id=recipient_id, name=clean_name, phone=clean_phone))

    return tuple(recipients)


def serialize_saved_recipients(recipients: tuple[SavedRecipient, ...] | list[SavedRecipient]) -> list[dict[str, str]]:
    """Serialize saved recipients for config entry options."""
    return [recipient.as_dict() for recipient in recipients]


def recipient_select_options(
    recipients: tuple[SavedRecipient, ...] | list[SavedRecipient],
) -> list[SelectOptionDict]:
    """Build select options for config flows."""
    return [
        SelectOptionDict(
            value=recipient.id,
            label=f"{recipient.name} ({recipient.phone})",
        )
        for recipient in recipients
    ]


def recipient_summary_lines(
    recipients: tuple[SavedRecipient, ...] | list[SavedRecipient],
    max_items: int = 12,
) -> str:
    """Build a short markdown-like summary used in options flow descriptions."""
    recipients_list = list(recipients)
    if not recipients_list:
        return "- brak zapisanych odbiorcow -"

    lines = [f"- {recipient.name}: {recipient.phone}" for recipient in recipients_list[:max_items]]
    remaining = len(recipients_list) - max_items
    if remaining > 0:
        lines.append(f"- ... i jeszcze {remaining}")
    return "\n".join(lines)


def deduplicate_phone_numbers(phone_numbers: list[str]) -> list[str]:
    """Preserve order while removing duplicates and empty values."""
    seen: set[str] = set()
    unique_numbers: list[str] = []
    for phone in phone_numbers:
        if not phone or phone in seen:
            continue
        seen.add(phone)
        unique_numbers.append(phone)
    return unique_numbers


def mask_phone_number(phone: str) -> str:
    """Mask a phone number for logs while keeping it identifiable."""
    compact = phone.strip()
    if len(compact) <= 4:
        return compact
    prefix_length = 3 if compact.startswith("+") else 2
    prefix = compact[:prefix_length]
    suffix = compact[-2:]
    hidden_length = max(1, len(compact) - len(prefix) - len(suffix))
    return f"{prefix}{'*' * hidden_length}{suffix}"
