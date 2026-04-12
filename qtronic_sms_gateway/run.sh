#!/usr/bin/with-contenv bashio

export PYTHONUNBUFFERED=1

bashio::log.info "Starting Q-Tronic SMS Gateway add-on"
bashio::log.info "ESPHome host: $(bashio::config 'esphome.host')"
bashio::log.info "MQTT enabled: $(bashio::config 'mqtt.enabled')"

exec python3 /app/server.py
