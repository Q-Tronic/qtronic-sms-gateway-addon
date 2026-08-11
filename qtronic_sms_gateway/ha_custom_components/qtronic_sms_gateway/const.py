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
CONF_SMS_COMMAND_RULES = "sms_command_rules"
CONF_SMS_SECURITY_GLOBAL_LIMIT = "sms_security_global_limit"
CONF_SMS_SECURITY_SENDER_LIMIT = "sms_security_sender_limit"
CONF_SMS_SECURITY_WINDOW_S = "sms_security_window_s"
CONF_SMS_SECURITY_PIN_FAILURE_LIMIT = "sms_security_pin_failure_limit"
CONF_SMS_SECURITY_LOCKOUT_S = "sms_security_lockout_s"
CONF_FORWARD_SMS_ENABLED = "forward_sms_enabled"
CONF_FORWARD_CALLS_ENABLED = "forward_calls_enabled"
CONF_FORWARD_COMMAND_MESSAGES = "forward_command_messages"
CONF_FORWARD_RECIPIENT_IDS = "forward_recipient_ids"
CONF_FORWARD_EXCLUDED_RECIPIENT_IDS = "forward_excluded_recipient_ids"
CONF_FORWARD_EXCLUDED_NUMBERS = "forward_excluded_numbers"
CONF_FORWARD_SMS_TEMPLATE = "forward_sms_template"
CONF_FORWARD_CALL_TEMPLATE = "forward_call_template"
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
CONF_MODEM_ONLINE_OBJECT_ID = "modem_online_object_id"
CONF_EXPECTED_NAME = "expected_name"
CONF_EXPECTED_MAC = "expected_mac"

SERVICE_SEND_SMS = "send_sms"
SERVICE_CALL_TO = "call_to"
SERVICE_HANGUP = "hangup"
SERVICE_CANCEL_BATCH = "cancel_batch"
SERVICE_SEND_USSD = "send_ussd"
ATTR_RECIPIENT = "recipient"
ATTR_MESSAGE = "message"
ATTR_ENCODING = "encoding"
ATTR_SAVED_RECIPIENTS = "saved_recipients"
ATTR_RING_TIME_S = "ring_time_s"
ATTR_PHONE_NUMBER = "phone_number"
ATTR_MESSAGE_SEARCH = "message_search"
ATTR_BATCH_ID = "batch_id"
ATTR_USSD_CODE = "code"

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
DEFAULT_SMS_SECURITY_GLOBAL_LIMIT = 30
DEFAULT_SMS_SECURITY_SENDER_LIMIT = 10
DEFAULT_SMS_SECURITY_WINDOW_S = 60
DEFAULT_SMS_SECURITY_PIN_FAILURE_LIMIT = 5
DEFAULT_SMS_SECURITY_LOCKOUT_S = 300
DEFAULT_SMS_RULE_COOLDOWN_S = 2
DEFAULT_SMS_RULE_CHALLENGE_TTL_S = 120

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
EVENT_ATTR_ADDON_HOSTNAME = "addon_hostname"
EVENT_ATTR_GATEWAY_HOST = "gateway_host"

EVENT_SMS_COMMAND_EXECUTED = "qtronic_sms_gateway_command_executed"
EVENT_SMS_COMMAND_FAILED = "qtronic_sms_gateway_command_failed"

SMS_RULE_ID = "id"
SMS_RULE_NAME = "name"
SMS_RULE_ENABLED = "enabled"
SMS_RULE_SENDER_MODE = "sender_mode"
SMS_RULE_SAVED_RECIPIENT_ID = "saved_recipient_id"
SMS_RULE_SENDER_PHONE = "sender_phone"
SMS_RULE_COMMAND = "command"
SMS_RULE_MATCH_MODE = "match_mode"
SMS_RULE_ACTION = "action"
SMS_RULE_ENTITY_ID = "entity_id"
SMS_RULE_REPLY_ENABLED = "reply_enabled"
SMS_RULE_SUCCESS_REPLY = "success_reply"
SMS_RULE_FAILURE_REPLY = "failure_reply"
SMS_RULE_ENTITY_IDS = "entity_ids"
SMS_RULE_PRIORITY = "priority"
SMS_RULE_COOLDOWN_S = "cooldown_s"
SMS_RULE_PIN_HASH = "pin_hash"
SMS_RULE_PIN = "pin"
SMS_RULE_PIN_REQUIRED = "pin_required"
SMS_RULE_CHALLENGE_REQUIRED = "challenge_required"
SMS_RULE_CHALLENGE_TTL_S = "challenge_ttl_s"
SMS_RULE_CONDITION_AFTER = "condition_after"
SMS_RULE_CONDITION_BEFORE = "condition_before"
SMS_RULE_CONDITION_ENTITY_ID = "condition_entity_id"
SMS_RULE_CONDITION_STATE = "condition_state"
SMS_RULE_SERVICE_DATA = "service_data"

SMS_RULE_SENDER_SAVED = "saved"
SMS_RULE_SENDER_MANUAL = "manual"
SMS_RULE_SENDER_MODES = (SMS_RULE_SENDER_SAVED, SMS_RULE_SENDER_MANUAL)

SMS_RULE_MATCH_EXACT = "exact"
SMS_RULE_MATCH_CONTAINS = "contains"
SMS_RULE_MATCH_STARTS_WITH = "starts_with"
SMS_RULE_MATCH_MODES = (
    SMS_RULE_MATCH_EXACT,
    SMS_RULE_MATCH_CONTAINS,
    SMS_RULE_MATCH_STARTS_WITH,
)

SMS_RULE_ACTION_TURN_ON = "turn_on"
SMS_RULE_ACTION_TURN_OFF = "turn_off"
SMS_RULE_ACTION_TOGGLE = "toggle"
SMS_RULE_ACTION_REPORT_STATE = "report_state"
SMS_RULE_ACTION_OPEN_COVER = "open_cover"
SMS_RULE_ACTION_CLOSE_COVER = "close_cover"
SMS_RULE_ACTION_STOP_COVER = "stop_cover"
SMS_RULE_ACTION_LOCK = "lock"
SMS_RULE_ACTION_UNLOCK = "unlock"
SMS_RULE_ACTION_ALARM_ARM_HOME = "alarm_arm_home"
SMS_RULE_ACTION_ALARM_ARM_AWAY = "alarm_arm_away"
SMS_RULE_ACTION_ALARM_ARM_NIGHT = "alarm_arm_night"
SMS_RULE_ACTION_ALARM_DISARM = "alarm_disarm"
SMS_RULE_ACTION_ALARM_TRIGGER = "alarm_trigger"
SMS_RULE_ACTION_RUN_SCRIPT = "run_script"
SMS_RULE_ACTION_ACTIVATE_SCENE = "activate_scene"
SMS_RULE_ACTION_SET_VALUE = "set_value"
SMS_RULE_ACTIONS = (
    SMS_RULE_ACTION_TURN_ON,
    SMS_RULE_ACTION_TURN_OFF,
    SMS_RULE_ACTION_TOGGLE,
    SMS_RULE_ACTION_REPORT_STATE,
    SMS_RULE_ACTION_OPEN_COVER,
    SMS_RULE_ACTION_CLOSE_COVER,
    SMS_RULE_ACTION_STOP_COVER,
    SMS_RULE_ACTION_LOCK,
    SMS_RULE_ACTION_UNLOCK,
    SMS_RULE_ACTION_ALARM_ARM_HOME,
    SMS_RULE_ACTION_ALARM_ARM_AWAY,
    SMS_RULE_ACTION_ALARM_ARM_NIGHT,
    SMS_RULE_ACTION_ALARM_DISARM,
    SMS_RULE_ACTION_ALARM_TRIGGER,
    SMS_RULE_ACTION_RUN_SCRIPT,
    SMS_RULE_ACTION_ACTIVATE_SCENE,
    SMS_RULE_ACTION_SET_VALUE,
)

TRIGGER_SMS_RECEIVED = "sms_received"
TRIGGER_INCOMING_CALL = "incoming_call"

ROLE_RSSI = "rssi"
ROLE_REGISTERED = "registered"
ROLE_MODEM_ONLINE = "modem_online"
ROLE_SMS_SENDER = "sms_sender"
ROLE_SMS_MESSAGE = "sms_message"
ROLE_INCOMING_CALL = "incoming_call"
ROLE_CALL_STATE = "call_state"
ROLE_USSD = "ussd"
ROLE_SMS_STATUS = "sms_status"
ROLE_SMS_LAST_ERROR = "sms_last_error"
ROLE_SMS_QUEUE_DEPTH = "sms_queue_depth"
ROLE_SMS_SENT_COUNT = "sms_sent_count"
ROLE_SMS_FAILED_COUNT = "sms_failed_count"
ROLE_SMS_UNKNOWN_COUNT = "sms_unknown_count"
ROLE_SIM800_TIMEOUT_COUNT = "sim800_timeout_count"
ROLE_SIM800_RECOVERY_COUNT = "sim800_recovery_count"
ROLE_UART_RX_OVERFLOW_COUNT = "uart_rx_overflow_count"
ROLE_SIM800_LAST_RESPONSE_AGE = "sim800_last_response_age"
ROLE_SIM800_STATE = "sim800_state"

AUTO_DETECT_OBJECT_IDS: dict[str, tuple[str, ...]] = {
    ROLE_RSSI: ("rssi", "signal", "signal_strength"),
    ROLE_REGISTERED: ("registered", "network_registered"),
    ROLE_MODEM_ONLINE: ("modem_online", "sim800_online", "modem_available"),
    ROLE_SMS_SENDER: ("sms_sender", "sender"),
    ROLE_SMS_MESSAGE: ("sms_message", "message", "sms"),
    ROLE_INCOMING_CALL: ("incoming_call", "caller_id", "call"),
    ROLE_CALL_STATE: ("call_state", "gsm_call_state", "sim800_call_state"),
    ROLE_USSD: ("ussd", "ussd_message"),
    ROLE_SMS_STATUS: ("sms_status",),
    ROLE_SMS_LAST_ERROR: ("sms_last_error",),
    ROLE_SMS_QUEUE_DEPTH: ("sms_queue_depth",),
    ROLE_SMS_SENT_COUNT: ("sms_sent_count",),
    ROLE_SMS_FAILED_COUNT: ("sms_failed_count",),
    ROLE_SMS_UNKNOWN_COUNT: ("sms_unknown_count",),
    ROLE_SIM800_TIMEOUT_COUNT: ("sim800_timeout_count",),
    ROLE_SIM800_RECOVERY_COUNT: ("sim800_recovery_count",),
    ROLE_UART_RX_OVERFLOW_COUNT: ("uart_rx_overflow_count",),
    ROLE_SIM800_LAST_RESPONSE_AGE: ("sim800_last_response_age",),
    ROLE_SIM800_STATE: ("sim800_state",),
}

ROLE_TO_OPTION_KEY: dict[str, str] = {
    ROLE_RSSI: CONF_RSSI_OBJECT_ID,
    ROLE_REGISTERED: CONF_REGISTERED_OBJECT_ID,
    ROLE_MODEM_ONLINE: CONF_MODEM_ONLINE_OBJECT_ID,
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
RECIPIENT_PIN_HASH = "pin_hash"
RECIPIENT_PIN_REQUIRED = "pin_required"

API_TOKEN_FILENAME = ".qtronic_sms_gateway/api_token"
