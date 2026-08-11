import esphome.codegen as cg
from esphome.components import text_sensor
import esphome.config_validation as cv
from esphome.const import ENTITY_CATEGORY_DIAGNOSTIC

from . import CONF_SIM800L_ID, Sim800LComponent

DEPENDENCIES = ["sim800l"]

CONF_SMS_STATUS = "sms_status"
CONF_SMS_LAST_ERROR = "sms_last_error"
CONF_SIM800_STATE = "sim800_state"

CONFIG_SCHEMA = cv.Schema(
    {
        cv.GenerateID(CONF_SIM800L_ID): cv.use_id(Sim800LComponent),
        cv.Optional(CONF_SMS_STATUS): text_sensor.text_sensor_schema(
            icon="mdi:message-processing-outline",
            entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
        ),
        cv.Optional(CONF_SMS_LAST_ERROR): text_sensor.text_sensor_schema(
            icon="mdi:alert-circle-outline",
            entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
        ),
        cv.Optional(CONF_SIM800_STATE): text_sensor.text_sensor_schema(
            icon="mdi:state-machine",
            entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
        ),
    }
)


async def to_code(config):
    parent = await cg.get_variable(config[CONF_SIM800L_ID])
    for key in (CONF_SMS_STATUS, CONF_SMS_LAST_ERROR, CONF_SIM800_STATE):
        if key not in config:
            continue
        sens = await text_sensor.new_text_sensor(config[key])
        cg.add(getattr(parent, f"set_{key}_text_sensor")(sens))
