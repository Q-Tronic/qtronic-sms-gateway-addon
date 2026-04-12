"""Recipient helpers for the Q-Tronic SMS Gateway add-on."""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_PHONE_ALLOWED_RE = re.compile(r"[^0-9+]")
_PHONE_DIGITS_RE = re.compile(r"[^0-9]")


@dataclass(frozen=True, slots=True)
class SavedRecipient:
    """One saved recipient."""

    id: str
    name: str
    phone: str

    @property
    def masked_phone(self) -> str:
        compact = self.phone.strip()
        if len(compact) <= 4:
            return compact
        prefix_length = 3 if compact.startswith("+") else 2
        prefix = compact[:prefix_length]
        suffix = compact[-2:]
        hidden_length = max(1, len(compact) - len(prefix) - len(suffix))
        return f"{prefix}{'*' * hidden_length}{suffix}"


def normalize_phone_number(value: str) -> str:
    """Normalize a phone number to a compact GSM-friendly form."""
    compact = _PHONE_ALLOWED_RE.sub("", value.strip())
    if compact.count("+") > 1:
        raise ValueError("Phone number can contain at most one plus sign.")
    if "+" in compact and not compact.startswith("+"):
        raise ValueError("Plus sign is only allowed at the beginning.")
    digits = compact[1:] if compact.startswith("+") else compact
    if len(digits) < 5 or len(digits) > 20:
        raise ValueError("Phone number must contain between 5 and 20 digits.")
    if not digits.isdigit():
        raise ValueError("Phone number must contain only digits and an optional leading plus.")
    return compact


def normalize_phone_number_loose(value: str) -> str:
    """Normalize a phone number for display or matching."""
    return _PHONE_ALLOWED_RE.sub("", value.strip())


def normalize_recipient_name(value: str) -> str:
    """Normalize a display name."""
    name = " ".join(value.strip().split())
    if not name:
        raise ValueError("Recipient name cannot be empty.")
    return name


def slugify_recipient_name(value: str) -> str:
    """Build a stable ASCII slug from a recipient name."""
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    slug = _NON_ALNUM_RE.sub("_", normalized.lower()).strip("_")
    return slug or "recipient"


def make_recipient_id(name: str, existing_ids: set[str]) -> str:
    """Create a unique recipient ID."""
    base = slugify_recipient_name(name)
    candidate = base
    index = 2
    while candidate in existing_ids:
        candidate = f"{base}_{index}"
        index += 1
    return candidate


def phone_match_key(value: str) -> str:
    """Create a comparison-friendly phone key."""
    digits = _PHONE_DIGITS_RE.sub("", value.strip())
    if digits.startswith("00"):
        digits = digits[2:]
    return digits


def phone_numbers_match(left: str, right: str) -> bool:
    """Return True if two phone numbers likely describe the same person."""
    left_key = phone_match_key(left)
    right_key = phone_match_key(right)
    if not left_key or not right_key:
        return False
    if left_key == right_key:
        return True
    shorter, longer = sorted((left_key, right_key), key=len)
    return len(shorter) >= 7 and longer.endswith(shorter)


def deduplicate_phone_numbers(phone_numbers: list[str]) -> list[str]:
    """Preserve order while removing duplicates and empty values."""
    seen: set[str] = set()
    unique_numbers: list[str] = []
    for phone in phone_numbers:
        if not phone:
            continue
        key = phone_match_key(phone)
        if not key or key in seen:
            continue
        seen.add(key)
        unique_numbers.append(phone)
    return unique_numbers
