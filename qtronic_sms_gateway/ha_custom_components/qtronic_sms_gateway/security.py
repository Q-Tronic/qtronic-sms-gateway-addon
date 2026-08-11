"""Security helpers for inbound SMS control."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets

_PIN_RE = re.compile(r"^[0-9]{4,12}$")
_PHONE_FORMAT_RE = re.compile(r"[\s().-]+")
_PBKDF2_ITERATIONS = 210_000


def validate_pin(pin: str) -> str:
    """Return a validated numeric SMS PIN."""
    clean = pin.strip()
    if clean and not _PIN_RE.fullmatch(clean):
        raise ValueError("PIN must contain between 4 and 12 digits.")
    return clean


def hash_pin(pin: str) -> str:
    """Hash a PIN using a salted PBKDF2 representation safe for config storage."""
    clean = validate_pin(pin)
    if not clean:
        return ""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", clean.encode("utf-8"), salt, _PBKDF2_ITERATIONS
    )
    return "pbkdf2_sha256${}${}${}".format(
        _PBKDF2_ITERATIONS,
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def verify_pin(pin: str, encoded: str) -> bool:
    """Verify a PIN without leaking timing information."""
    if not encoded:
        return True
    try:
        algorithm, iterations_text, salt_text, digest_text = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_text)
        if iterations < 100_000 or iterations > 1_000_000:
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_text.encode("ascii"))
        actual = hashlib.pbkdf2_hmac(
            "sha256", pin.strip().encode("utf-8"), salt, iterations
        )
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(actual, expected)


def canonical_authorization_number(value: str) -> str:
    """Canonicalize a number without suffix matching or country-code guessing."""
    compact = _PHONE_FORMAT_RE.sub("", value.strip())
    if compact.startswith("00"):
        compact = "+" + compact[2:]
    if not compact.startswith("+"):
        return ""
    digits = compact[1:]
    if (
        not digits.isdigit()
        or not 5 <= len(digits) <= 15
        or digits.startswith("0")
    ):
        return ""
    return "+" + digits


def authorization_numbers_match(left: str, right: str) -> bool:
    """Compare full normalized numbers for security decisions."""
    left_key = canonical_authorization_number(left)
    right_key = canonical_authorization_number(right)
    return bool(left_key and hmac.compare_digest(left_key, right_key))


def split_trailing_pin(message: str) -> tuple[str, str]:
    """Split an optional final numeric token from an SMS command."""
    command, separator, candidate = message.strip().rpartition(" ")
    if separator and _PIN_RE.fullmatch(candidate):
        return command.strip(), candidate
    return message.strip(), ""
