from __future__ import annotations

from pathlib import Path
import sys
import unittest


APP_PATH = Path(__file__).resolve().parents[2] / "qtronic_sms_gateway" / "app"
sys.path.insert(0, str(APP_PATH))

from qtronic_gateway.sms import (  # noqa: E402
    ENCODING_PASSTHROUGH,
    ENCODING_UCS2,
    resolve_auto_encoding,
    sms_segment_info,
    split_sms_message,
)
from qtronic_gateway.validation import (  # noqa: E402
    CallRequest,
    RequestValidationError,
    SmsSendRequest,
    UssdRequest,
    normalize_external_phone,
)


class SmsSegmentTests(unittest.TestCase):
    def test_gsm_and_extension_units(self) -> None:
        self.assertEqual(sms_segment_info("a" * 160).segments, 1)
        self.assertEqual(sms_segment_info("a" * 161).segments, 2)
        self.assertEqual(sms_segment_info("^" * 80).segments, 1)
        self.assertEqual(sms_segment_info("^" * 81).segments, 2)
        self.assertEqual(sms_segment_info("\f" * 80).segments, 1)

    def test_auto_keeps_non_ascii_gsm_alphabet(self) -> None:
        self.assertEqual(resolve_auto_encoding("£éÄ", True), ENCODING_PASSTHROUGH)
        self.assertEqual(resolve_auto_encoding("zażółć", True), ENCODING_UCS2)

    def test_ucs2_counts_utf16_units_and_splits_without_surrogate_break(self) -> None:
        info = sms_segment_info("🙂" * 36, "ucs2")
        self.assertEqual(info.units, 72)
        self.assertEqual(info.segments, 2)
        parts = split_sms_message("🙂" * 36, info)
        self.assertEqual("".join(parts), "🙂" * 36)
        self.assertTrue(
            all(sms_segment_info(part, "ucs2").units <= 67 for part in parts)
        )


class ValidationTests(unittest.TestCase):
    def test_phone_is_strict_and_normalized(self) -> None:
        self.assertEqual(normalize_external_phone("+48 535-000-111"), "+48535000111")
        with self.assertRaises(RequestValidationError):
            normalize_external_phone("tel: +48535000111")
        with self.assertRaises(RequestValidationError):
            normalize_external_phone("+01234567")

    def test_ring_time_rejects_fractional_values(self) -> None:
        with self.assertRaises(RequestValidationError):
            CallRequest.parse({"recipient": "+48535000111", "ring_time_s": 2.5})
        with self.assertRaises(RequestValidationError):
            CallRequest.parse({"recipient": "+48535000111", "ring_time_s": "2.0"})

    def test_multipart_requires_explicit_split(self) -> None:
        command = SmsSendRequest.parse(
            {"recipient": "+48535000111", "message": "a" * 161}
        )
        with self.assertRaisesRegex(RequestValidationError, "split_long"):
            command.segments(unicode_available=True, max_segments=10, split_long=False)
        info, parts = command.segments(
            unicode_available=True, max_segments=10, split_long=True
        )
        self.assertEqual(info.segments, 2)
        self.assertEqual(parts, ["a" * 153, "a" * 8])

    def test_idempotency_key_and_recipient_limit_are_bounded(self) -> None:
        with self.assertRaises(RequestValidationError):
            SmsSendRequest.parse(
                {
                    "recipient": "+48535000111",
                    "message": "ok",
                    "idempotency_key": "contains spaces",
                }
            )
        with self.assertRaises(RequestValidationError):
            SmsSendRequest.parse(
                {
                    "recipients": [f"+48535{i:06d}" for i in range(51)],
                    "message": "ok",
                }
            )

    def test_ussd_is_strict(self) -> None:
        self.assertEqual(UssdRequest.parse({"code": "*100#"}).code, "*100#")
        with self.assertRaises(RequestValidationError):
            UssdRequest.parse({"code": "AT+CUSD=1"})


if __name__ == "__main__":
    unittest.main()
