"""Typed request validation shared by REST, MQTT, and the dashboard."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping, Sequence

from .sms import SMS_ENCODINGS, SmsSegmentInfo, sms_segment_info, split_sms_message

MAX_RECIPIENTS = 50
MAX_IDEMPOTENCY_KEY_LENGTH = 128
MAX_HISTORY_LIMIT = 5000
MAX_SMS_CHARACTERS = 20_000
DEFAULT_WAIT_TIMEOUT_S = 30
MAX_WAIT_TIMEOUT_S = 300
MIN_RING_TIME_S = 1
MAX_RING_TIME_S = 3600

_PHONE_INPUT_RE = re.compile(r"^\+?[0-9][0-9 ()-]*$")
_RECIPIENT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9._:-]+$")
_HISTORY_TYPE_RE = re.compile(r"^[a-z0-9_*.-]{1,64}$")
_USSD_RE = re.compile(r"^[0-9*#]{1,32}$")


class RequestValidationError(ValueError):
    """Raised when an external command payload is malformed or unsafe."""


def _mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RequestValidationError("Request body must be a JSON object.")
    return value


def _optional_string(value: Any, field: str, *, max_length: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise RequestValidationError(f"'{field}' must be a string.")
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > max_length:
        raise RequestValidationError(
            f"'{field}' cannot exceed {max_length} characters."
        )
    return normalized


def _string_list(
    value: Any,
    field: str,
    *,
    max_items: int = MAX_RECIPIENTS,
    item_max_length: int = 64,
) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise RequestValidationError(f"'{field}' must be an array of strings.")
    if len(value) > max_items:
        raise RequestValidationError(
            f"'{field}' cannot contain more than {max_items} items."
        )
    result: list[str] = []
    for item in value:
        normalized = _optional_string(item, field, max_length=item_max_length)
        if normalized is not None:
            result.append(normalized)
    return tuple(result)


def _integer(value: Any, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise RequestValidationError(f"'{field}' must be an integer.")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and re.fullmatch(r"-?[0-9]+", value.strip()):
        parsed = int(value.strip())
    else:
        raise RequestValidationError(f"'{field}' must be an integer.")
    if parsed < minimum or parsed > maximum:
        raise RequestValidationError(
            f"'{field}' must be between {minimum} and {maximum}."
        )
    return parsed


def parse_ring_time(value: Any) -> int:
    """Validate a ring time shared by JSON commands and MQTT number controls."""
    return _integer(value, "ring_time_s", MIN_RING_TIME_S, MAX_RING_TIME_S)


def _boolean(value: Any, field: str, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    raise RequestValidationError(f"'{field}' must be a boolean.")


def normalize_external_phone(value: str) -> str:
    """Strictly validate a direct phone number while preserving legacy national input."""
    candidate = value.strip()
    if not _PHONE_INPUT_RE.fullmatch(candidate):
        raise RequestValidationError(
            "Phone numbers may contain only digits, spaces, parentheses, hyphens, "
            "and one leading plus sign."
        )
    compact = re.sub(r"[ ()-]", "", candidate)
    if "+" in compact[1:]:
        raise RequestValidationError("Plus sign is only allowed at the beginning.")
    digits = compact[1:] if compact.startswith("+") else compact
    if not digits.isdigit() or len(digits) < 5 or len(digits) > 15:
        raise RequestValidationError(
            "Phone number must contain between 5 and 15 digits."
        )
    if compact.startswith("+") and digits.startswith("0"):
        raise RequestValidationError(
            "International phone numbers cannot start with +0."
        )
    return compact


def validate_resolved_recipients(recipients: Sequence[str]) -> list[str]:
    if not recipients:
        raise RequestValidationError("At least one recipient is required.")
    if len(recipients) > MAX_RECIPIENTS:
        raise RequestValidationError(
            f"A request cannot target more than {MAX_RECIPIENTS} recipients."
        )
    normalized: list[str] = []
    seen: set[str] = set()
    for recipient in recipients:
        phone = normalize_external_phone(str(recipient))
        comparison = phone.lstrip("+")
        if comparison in seen:
            continue
        seen.add(comparison)
        normalized.append(phone)
    return normalized


def _recipient_id(value: str, field: str) -> str:
    normalized = value.strip().lower()
    if not _RECIPIENT_ID_RE.fullmatch(normalized):
        raise RequestValidationError(f"'{field}' contains an invalid recipient ID.")
    return normalized


def _idempotency_key(value: Any) -> str | None:
    key = _optional_string(
        value,
        "idempotency_key",
        max_length=MAX_IDEMPOTENCY_KEY_LENGTH,
    )
    if key is not None and not _IDEMPOTENCY_RE.fullmatch(key):
        raise RequestValidationError(
            "'idempotency_key' may contain only letters, digits, dot, underscore, colon, or hyphen."
        )
    return key


@dataclass(frozen=True, slots=True)
class SmsSendRequest:
    message: str
    recipient: str | None
    recipient_id: str | None
    recipients: tuple[str, ...]
    recipient_ids: tuple[str, ...]
    encoding: str
    wait: bool
    wait_timeout_s: int
    idempotency_key: str | None

    @classmethod
    def parse(cls, value: Any, *, default_encoding: str = "auto") -> "SmsSendRequest":
        payload = _mapping(value)
        message = payload.get("message")
        if not isinstance(message, str) or not message.strip():
            raise RequestValidationError("'message' must be a non-empty string.")
        if len(message) > MAX_SMS_CHARACTERS:
            raise RequestValidationError(
                f"'message' cannot exceed {MAX_SMS_CHARACTERS} characters."
            )

        recipient = _optional_string(
            payload.get("recipient"), "recipient", max_length=32
        )
        if recipient is not None:
            recipient = normalize_external_phone(recipient)
        recipient_id = _optional_string(
            payload.get("recipient_id"), "recipient_id", max_length=64
        )
        if recipient_id is not None:
            recipient_id = _recipient_id(recipient_id, "recipient_id")

        recipients = tuple(
            normalize_external_phone(item)
            for item in _string_list(
                payload.get("recipients"), "recipients", item_max_length=32
            )
        )
        recipient_ids = tuple(
            _recipient_id(item, "recipient_ids")
            for item in _string_list(payload.get("recipient_ids"), "recipient_ids")
        )
        if not any((recipient, recipient_id, recipients, recipient_ids)):
            raise RequestValidationError(
                "At least one recipient or recipient ID is required."
            )

        encoding = str(payload.get("encoding") or default_encoding).strip().lower()
        if encoding not in SMS_ENCODINGS:
            raise RequestValidationError(f"Unsupported SMS encoding mode: {encoding}")
        return cls(
            message=message,
            recipient=recipient,
            recipient_id=recipient_id,
            recipients=recipients,
            recipient_ids=recipient_ids,
            encoding=encoding,
            wait=_boolean(payload.get("wait"), "wait", True),
            wait_timeout_s=_integer(
                payload.get("wait_timeout_s", DEFAULT_WAIT_TIMEOUT_S),
                "wait_timeout_s",
                1,
                MAX_WAIT_TIMEOUT_S,
            ),
            idempotency_key=_idempotency_key(payload.get("idempotency_key")),
        )

    def recipient_kwargs(self) -> dict[str, Any]:
        return {
            "recipient": self.recipient,
            "recipient_id": self.recipient_id,
            "recipients": list(self.recipients),
            "recipient_ids": list(self.recipient_ids),
        }

    def segments(
        self,
        *,
        unicode_available: bool,
        max_segments: int,
        split_long: bool,
    ) -> tuple[SmsSegmentInfo, list[str]]:
        info = sms_segment_info(
            self.message,
            self.encoding,
            unicode_available=unicode_available,
        )
        if info.segments > max_segments:
            raise RequestValidationError(
                f"Message needs {info.segments} SMS segments; configured maximum is {max_segments}."
            )
        if info.segments > 1 and not split_long:
            raise RequestValidationError(
                f"Message needs {info.segments} SMS segments, but automatic splitting is disabled; "
                "enable sms.split_long to send it as explicitly tracked parts."
            )
        return info, split_sms_message(self.message, info) if split_long else [
            self.message
        ]


@dataclass(frozen=True, slots=True)
class CallRequest:
    recipient: str | None
    recipient_id: str | None
    recipients: tuple[str, ...]
    recipient_ids: tuple[str, ...]
    ring_time_s: int
    idempotency_key: str | None

    @classmethod
    def parse(cls, value: Any, *, default_ring_time_s: int = 20) -> "CallRequest":
        payload = _mapping(value)
        recipient = _optional_string(
            payload.get("recipient"), "recipient", max_length=32
        )
        if recipient is not None:
            recipient = normalize_external_phone(recipient)
        recipient_id = _optional_string(
            payload.get("recipient_id"), "recipient_id", max_length=64
        )
        if recipient_id is not None:
            recipient_id = _recipient_id(recipient_id, "recipient_id")
        recipients = tuple(
            normalize_external_phone(item)
            for item in _string_list(
                payload.get("recipients"), "recipients", item_max_length=32
            )
        )
        recipient_ids = tuple(
            _recipient_id(item, "recipient_ids")
            for item in _string_list(payload.get("recipient_ids"), "recipient_ids")
        )
        if not any((recipient, recipient_id, recipients, recipient_ids)):
            raise RequestValidationError(
                "At least one recipient or recipient ID is required."
            )
        return cls(
            recipient=recipient,
            recipient_id=recipient_id,
            recipients=recipients,
            recipient_ids=recipient_ids,
            ring_time_s=_integer(
                payload.get("ring_time_s", default_ring_time_s),
                "ring_time_s",
                MIN_RING_TIME_S,
                MAX_RING_TIME_S,
            ),
            idempotency_key=_idempotency_key(payload.get("idempotency_key")),
        )

    def recipient_kwargs(self) -> dict[str, Any]:
        return {
            "recipient": self.recipient,
            "recipient_id": self.recipient_id,
            "recipients": list(self.recipients),
            "recipient_ids": list(self.recipient_ids),
        }


@dataclass(frozen=True, slots=True)
class CancelRequest:
    job_id: str | None

    @classmethod
    def parse(cls, value: Any) -> "CancelRequest":
        payload = _mapping(value or {})
        return cls(
            job_id=_optional_string(payload.get("job_id"), "job_id", max_length=128)
        )


@dataclass(frozen=True, slots=True)
class UssdRequest:
    code: str

    @classmethod
    def parse(cls, value: Any) -> "UssdRequest":
        payload = _mapping(value)
        code = _optional_string(payload.get("code"), "code", max_length=32)
        if code is None or not _USSD_RE.fullmatch(code):
            raise RequestValidationError(
                "'code' must contain 1-32 digits, asterisks, or hash characters."
            )
        return cls(code=code)


@dataclass(frozen=True, slots=True)
class HistoryQuery:
    event_type: str | None = None
    since: float | None = None
    until: float | None = None
    limit: int = 100
    offset: int = 0

    @classmethod
    def parse(cls, value: Mapping[str, Any]) -> "HistoryQuery":
        event_type = _optional_string(value.get("type"), "type", max_length=64)
        if event_type and not _HISTORY_TYPE_RE.fullmatch(event_type):
            raise RequestValidationError("Invalid history event type filter.")

        def timestamp(name: str) -> float | None:
            raw = value.get(name)
            if raw in (None, ""):
                return None
            try:
                parsed = float(raw)
            except (TypeError, ValueError) as err:
                raise RequestValidationError(
                    f"'{name}' must be a UNIX timestamp."
                ) from err
            if parsed < 0:
                raise RequestValidationError(f"'{name}' cannot be negative.")
            return parsed

        since = timestamp("since")
        until = timestamp("until")
        if since is not None and until is not None and since > until:
            raise RequestValidationError("'since' cannot be later than 'until'.")
        return cls(
            event_type=event_type,
            since=since,
            until=until,
            limit=_integer(value.get("limit", 100), "limit", 1, MAX_HISTORY_LIMIT),
            offset=_integer(value.get("offset", 0), "offset", 0, 10_000_000),
        )
