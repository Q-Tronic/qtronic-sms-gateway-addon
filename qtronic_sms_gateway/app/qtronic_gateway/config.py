"""Configuration loader for the Q-Tronic SMS Gateway add-on."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any

from .recipients import (
    SavedRecipient,
    make_recipient_id,
    normalize_phone_number,
    normalize_recipient_name,
    slugify_recipient_name,
)


@dataclass(frozen=True, slots=True)
class ESPHomeConfig:
    host: str
    port: int
    encryption_key: str
    send_sms_action: str
    unicode_send_sms_action: str
    dial_action: str
    disconnect_action: str
    rssi_object_id: str
    registered_object_id: str
    modem_online_object_id: str
    sms_sender_object_id: str
    sms_message_object_id: str
    incoming_call_object_id: str
    call_state_object_id: str
    ussd_object_id: str


@dataclass(frozen=True, slots=True)
class SMSConfig:
    default_encoding: str
    send_delay_ms: int


@dataclass(frozen=True, slots=True)
class CallingConfig:
    default_ring_time_s: int
    delay_between_calls_s: int
    max_retries: int
    retry_delay_s: int
    retry_forever: bool
    failure_action: str


@dataclass(frozen=True, slots=True)
class MQTTConfig:
    enabled: bool
    host: str
    port: int
    username: str | None
    password: str | None
    topic_prefix: str
    discovery_enabled: bool
    discovery_prefix: str


@dataclass(frozen=True, slots=True)
class AddonConfig:
    esphome: ESPHomeConfig
    sms: SMSConfig
    calling: CallingConfig
    mqtt: MQTTConfig
    recipients: tuple[SavedRecipient, ...]

    def sanitized(self) -> dict[str, Any]:
        """Return a safe version of the config for UI/debug endpoints."""
        payload = asdict(self)
        payload["esphome"]["encryption_key"] = _mask_secret(self.esphome.encryption_key)
        payload["mqtt"]["password"] = _mask_secret(self.mqtt.password)
        return payload


def _mask_secret(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 4:
        return "*" * len(value)
    return value[:2] + "*" * (len(value) - 4) + value[-2:]


def _load_recipients(raw_value: Any) -> tuple[SavedRecipient, ...]:
    if not isinstance(raw_value, list):
        return ()

    recipients: list[SavedRecipient] = []
    seen_ids: set[str] = set()
    for item in raw_value:
        if not isinstance(item, dict):
            continue
        raw_name = item.get("name")
        raw_phone = item.get("phone")
        raw_id = item.get("id")
        if not isinstance(raw_name, str) or not isinstance(raw_phone, str):
            continue
        name = normalize_recipient_name(raw_name)
        phone = normalize_phone_number(raw_phone)
        if isinstance(raw_id, str) and raw_id.strip():
            recipient_id = slugify_recipient_name(raw_id)
        else:
            recipient_id = make_recipient_id(name, seen_ids)
        if recipient_id in seen_ids:
            recipient_id = make_recipient_id(name, seen_ids)
        seen_ids.add(recipient_id)
        recipients.append(SavedRecipient(id=recipient_id, name=name, phone=phone))
    return tuple(recipients)


def load_config(path: str | Path = "/data/options.json") -> AddonConfig:
    """Load the add-on configuration from Home Assistant options JSON."""
    raw_data = json.loads(Path(path).read_text(encoding="utf-8"))

    esphome_data = raw_data.get("esphome", {})
    sms_data = raw_data.get("sms", {})
    calling_data = raw_data.get("calling", {})
    mqtt_data = raw_data.get("mqtt", {})

    config = AddonConfig(
        esphome=ESPHomeConfig(
            host=str(esphome_data.get("host", "")).strip(),
            port=int(esphome_data.get("port", 6053)),
            encryption_key=str(esphome_data.get("encryption_key", "")).strip(),
            send_sms_action=str(esphome_data.get("send_sms_action", "send_sms")).strip(),
            unicode_send_sms_action=str(
                esphome_data.get("unicode_send_sms_action", "send_sms_unicode")
            ).strip(),
            dial_action=str(esphome_data.get("dial_action", "dial")).strip(),
            disconnect_action=str(esphome_data.get("disconnect_action", "disconnect")).strip(),
            rssi_object_id=str(esphome_data.get("rssi_object_id", "rssi")).strip(),
            registered_object_id=str(
                esphome_data.get("registered_object_id", "registered")
            ).strip(),
            modem_online_object_id=str(
                esphome_data.get("modem_online_object_id", "modem_online")
            ).strip(),
            sms_sender_object_id=str(
                esphome_data.get("sms_sender_object_id", "sms_sender")
            ).strip(),
            sms_message_object_id=str(
                esphome_data.get("sms_message_object_id", "sms_message")
            ).strip(),
            incoming_call_object_id=str(
                esphome_data.get("incoming_call_object_id", "incoming_call")
            ).strip(),
            call_state_object_id=str(
                esphome_data.get("call_state_object_id", "call_state")
            ).strip(),
            ussd_object_id=str(esphome_data.get("ussd_object_id", "ussd")).strip(),
        ),
        sms=SMSConfig(
            default_encoding=str(sms_data.get("default_encoding", "auto")).strip(),
            send_delay_ms=int(sms_data.get("send_delay_ms", 3000)),
        ),
        calling=CallingConfig(
            default_ring_time_s=int(calling_data.get("default_ring_time_s", 20)),
            delay_between_calls_s=int(calling_data.get("delay_between_calls_s", 5)),
            max_retries=int(calling_data.get("max_retries", 0)),
            retry_delay_s=int(calling_data.get("retry_delay_s", 10)),
            retry_forever=bool(calling_data.get("retry_forever", False)),
            failure_action=str(calling_data.get("failure_action", "next_recipient")).strip(),
        ),
        mqtt=MQTTConfig(
            enabled=bool(mqtt_data.get("enabled", True)),
            host=str(mqtt_data.get("host", "core-mosquitto")).strip(),
            port=int(mqtt_data.get("port", 1883)),
            username=str(mqtt_data.get("username", "")).strip() or None,
            password=str(mqtt_data.get("password", "")).strip() or None,
            topic_prefix=str(mqtt_data.get("topic_prefix", "qtronic_sms_gateway")).strip(),
            discovery_enabled=bool(mqtt_data.get("discovery_enabled", True)),
            discovery_prefix=str(mqtt_data.get("discovery_prefix", "homeassistant")).strip(),
        ),
        recipients=_load_recipients(raw_data.get("recipients", [])),
    )

    if not config.esphome.host:
        raise ValueError("ESPHome host is required in the add-on configuration.")
    return config
