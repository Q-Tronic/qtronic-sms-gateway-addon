import esphome.codegen as cg
from esphome.components import sensor
import esphome.config_validation as cv
from esphome.const import (
    DEVICE_CLASS_DURATION,
    DEVICE_CLASS_SIGNAL_STRENGTH,
    ENTITY_CATEGORY_DIAGNOSTIC,
    ICON_COUNTER,
    ICON_TIMER,
    STATE_CLASS_MEASUREMENT,
    STATE_CLASS_TOTAL_INCREASING,
    UNIT_DECIBEL_MILLIWATT,
    UNIT_SECOND,
)

from . import CONF_SIM800L_ID, Sim800LComponent

DEPENDENCIES = ["sim800l"]

CONF_RSSI = "rssi"
CONF_SMS_QUEUE_DEPTH = "sms_queue_depth"
CONF_SMS_SENT_COUNT = "sms_sent_count"
CONF_SMS_FAILED_COUNT = "sms_failed_count"
CONF_SMS_UNKNOWN_COUNT = "sms_unknown_count"
CONF_SIM800_TIMEOUT_COUNT = "sim800_timeout_count"
CONF_SIM800_RECOVERY_COUNT = "sim800_recovery_count"
CONF_UART_RX_OVERFLOW_COUNT = "uart_rx_overflow_count"
CONF_SIM800_LAST_RESPONSE_AGE = "sim800_last_response_age"


def _counter_schema():
    return sensor.sensor_schema(
        accuracy_decimals=0,
        icon=ICON_COUNTER,
        state_class=STATE_CLASS_TOTAL_INCREASING,
        entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
    )


CONFIG_SCHEMA = cv.Schema(
    {
        cv.GenerateID(CONF_SIM800L_ID): cv.use_id(Sim800LComponent),
        cv.Optional(CONF_RSSI): sensor.sensor_schema(
            unit_of_measurement=UNIT_DECIBEL_MILLIWATT,
            accuracy_decimals=0,
            device_class=DEVICE_CLASS_SIGNAL_STRENGTH,
            state_class=STATE_CLASS_MEASUREMENT,
            entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
        ),
        cv.Optional(CONF_SMS_QUEUE_DEPTH): sensor.sensor_schema(
            accuracy_decimals=0,
            icon="mdi:tray-full",
            state_class=STATE_CLASS_MEASUREMENT,
            entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
        ),
        cv.Optional(CONF_SMS_SENT_COUNT): _counter_schema(),
        cv.Optional(CONF_SMS_FAILED_COUNT): _counter_schema(),
        cv.Optional(CONF_SMS_UNKNOWN_COUNT): _counter_schema(),
        cv.Optional(CONF_SIM800_TIMEOUT_COUNT): _counter_schema(),
        cv.Optional(CONF_SIM800_RECOVERY_COUNT): _counter_schema(),
        cv.Optional(CONF_UART_RX_OVERFLOW_COUNT): _counter_schema(),
        cv.Optional(CONF_SIM800_LAST_RESPONSE_AGE): sensor.sensor_schema(
            unit_of_measurement=UNIT_SECOND,
            accuracy_decimals=0,
            device_class=DEVICE_CLASS_DURATION,
            icon=ICON_TIMER,
            state_class=STATE_CLASS_MEASUREMENT,
            entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
        ),
    }
)


async def to_code(config):
    parent = await cg.get_variable(config[CONF_SIM800L_ID])
    for key in (
        CONF_RSSI,
        CONF_SMS_QUEUE_DEPTH,
        CONF_SMS_SENT_COUNT,
        CONF_SMS_FAILED_COUNT,
        CONF_SMS_UNKNOWN_COUNT,
        CONF_SIM800_TIMEOUT_COUNT,
        CONF_SIM800_RECOVERY_COUNT,
        CONF_UART_RX_OVERFLOW_COUNT,
        CONF_SIM800_LAST_RESPONSE_AGE,
    ):
        if key not in config:
            continue
        sens = await sensor.new_sensor(config[key])
        cg.add(getattr(parent, f"set_{key}_sensor")(sens))
