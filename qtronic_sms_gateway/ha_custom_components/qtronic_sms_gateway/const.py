"""Constants for the Q-Tronic SMS Gateway integration."""

from __future__ import annotations

from homeassistant.const import CONF_HOST, CONF_PORT

DOMAIN = "qtronic_sms_gateway"
ISSUE_ID_RESTART_REQUIRED = "restart_required_after_addon_sync"
RESTART_REQUIRED_MARKER = ".addon_sync_state.json"

CONF_ENCRYPTION_KEY = "encryption_key"
CONF_CONFIG_ENTRY_ID = "config_entry_id"
CONF_DEFAULT_RECIPIENT = "default_recipient"
CONF_DEFAULT_RECIPIENT_IDS = "default_recipient_ids"
CONF_SEND_SMS_ACTION = "send_sms_action"
CONF_UNICODE_SEND_SMS_ACTION = "unicode_send_sms_action"
CONF_DIAL_ACTION = "dial_action"
CONF_DISCONNECT_ACTION = "disconnect_action"
CONF_SMS_ENCODING = "sms_encoding"
CONF_SAVED_RECIPIENTS = "saved_recipients"
CONF_SEND_DELAY_MS = "send_delay_ms"
CONF_DEFAULT_RING_TIME_S = "default_ring_time_s"
CONF_DELAY_BETWEEN_CALLS_S = "delay_between_calls_s"
CONF_CALL_MAX_RETRIES = "call_max_retries"
CONF_CALL_RETRY_DELAY_S = "call_retry_delay_s"
CONF_CALL_RETRY_FOREVER = "call_retry_forever"
CONF_CALL_FAILURE_ACTION = "call_failure_action"
CONF_SMS_SENDER_OBJECT_ID = "sms_sender_object_id"
CONF_SMS_MESSAGE_OBJECT_ID = "sms_message_object_id"
CONF_INCOMING_CALL_OBJECT_ID = "incoming_call_object_id"
CONF_CALL_STATE_OBJECT_ID = "call_state_object_id"
CONF_USSD_OBJECT_ID = "ussd_object_id"
CONF_RSSI_OBJECT_ID = "rssi_object_id"
CONF_REGISTERED_OBJECT_ID = "registered_object_id"
CONF_EXPECTED_NAME = "expected_name"
CONF_EXPECTED_MAC = "expected_mac"

SERVICE_SEND_SMS = "send_sms"
SERVICE_CALL_TO = "call_to"
ATTR_RECIPIENT = "recipient"
ATTR_MESSAGE = "message"
ATTR_ENCODING = "encoding"
ATTR_SAVED_RECIPIENTS = "saved_recipients"
ATTR_RING_TIME_S = "ring_time_s"
ATTR_PHONE_NUMBER = "phone_number"
ATTR_MESSAGE_SEARCH = "message_search"

DEFAULT_PORT = 8099
DEFAULT_ADDON_HOSTNAME = "qtronic_sms_gateway"
DEFAULT_SEND_SMS_ACTION = "send_sms"
DEFAULT_UNICODE_SEND_SMS_ACTION = "send_sms_unicode"
DEFAULT_DIAL_ACTION = "dial"
DEFAULT_DISCONNECT_ACTION = "disconnect"
DEFAULT_STARTUP_TIMEOUT = 20
DEFAULT_SEND_DELAY_MS = 3000
DEFAULT_RING_TIME_S = 20
DEFAULT_DELAY_BETWEEN_CALLS_S = 5
DEFAULT_CALL_MAX_RETRIES = 0
DEFAULT_CALL_RETRY_DELAY_S = 10
DEFAULT_CALL_RETRY_FOREVER = False
DEFAULT_CALL_FAILURE_ACTION = "next_recipient"
DEFAULT_INBOUND_EVENT_WARMUP_S = 5

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
DEFAULT_SMS_ENCODING = ENCODING_AUTO

CALL_FAILURE_ACTION_NEXT = "next_recipient"
CALL_FAILURE_ACTION_STOP = "stop_batch"
CALL_FAILURE_ACTIONS = (
    CALL_FAILURE_ACTION_NEXT,
    CALL_FAILURE_ACTION_STOP,
)

EVENT_SMS_RECEIVED = "qtronic_sms_gateway_sms_received"
EVENT_INCOMING_CALL = "qtronic_sms_gateway_incoming_call"
EVENT_ATTR_DEVICE_ID = "device_id"
EVENT_ATTR_CONFIG_ENTRY_ID = "config_entry_id"
EVENT_ATTR_HOST = "host"
EVENT_ATTR_SAVED_RECIPIENT_ID = "saved_recipient_id"
EVENT_ATTR_SAVED_RECIPIENT_NAME = "saved_recipient_name"
EVENT_ATTR_SENDER = "sender"
EVENT_ATTR_SENDER_NORMALIZED = "sender_normalized"
EVENT_ATTR_CALLER = "caller"
EVENT_ATTR_CALLER_NORMALIZED = "caller_normalized"
EVENT_ATTR_MESSAGE = "message"
EVENT_ATTR_MESSAGE_SEARCH = "message_search"

TRIGGER_SMS_RECEIVED = "sms_received"
TRIGGER_INCOMING_CALL = "incoming_call"

ROLE_RSSI = "rssi"
ROLE_REGISTERED = "registered"
ROLE_SMS_SENDER = "sms_sender"
ROLE_SMS_MESSAGE = "sms_message"
ROLE_INCOMING_CALL = "incoming_call"
ROLE_CALL_STATE = "call_state"
ROLE_USSD = "ussd"

AUTO_DETECT_OBJECT_IDS: dict[str, tuple[str, ...]] = {
    ROLE_RSSI: ("rssi", "signal", "signal_strength"),
    ROLE_REGISTERED: ("registered", "network_registered"),
    ROLE_SMS_SENDER: ("sms_sender", "sender"),
    ROLE_SMS_MESSAGE: ("sms_message", "message", "sms"),
    ROLE_INCOMING_CALL: ("incoming_call", "caller_id", "call"),
    ROLE_CALL_STATE: ("call_state", "gsm_call_state", "sim800_call_state"),
    ROLE_USSD: ("ussd", "ussd_message"),
}

ROLE_TO_OPTION_KEY: dict[str, str] = {
    ROLE_RSSI: CONF_RSSI_OBJECT_ID,
    ROLE_REGISTERED: CONF_REGISTERED_OBJECT_ID,
    ROLE_SMS_SENDER: CONF_SMS_SENDER_OBJECT_ID,
    ROLE_SMS_MESSAGE: CONF_SMS_MESSAGE_OBJECT_ID,
    ROLE_INCOMING_CALL: CONF_INCOMING_CALL_OBJECT_ID,
    ROLE_CALL_STATE: CONF_CALL_STATE_OBJECT_ID,
    ROLE_USSD: CONF_USSD_OBJECT_ID,
}

CONNECTION_KEYS = {
    CONF_HOST,
    CONF_PORT,
    CONF_ENCRYPTION_KEY,
}

RECIPIENT_ID = "id"
RECIPIENT_NAME = "name"
RECIPIENT_PHONE = "phone"
