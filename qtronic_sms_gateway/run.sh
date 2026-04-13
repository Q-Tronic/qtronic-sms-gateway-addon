#!/usr/bin/with-contenv bashio

export PYTHONUNBUFFERED=1

bashio::log.info "Starting Q-Tronic SMS Gateway add-on"
bashio::log.info "ESPHome host: $(bashio::config 'esphome.host')"
bashio::log.info "MQTT enabled: $(bashio::config 'mqtt.enabled')"

if ! python3 /app/qtronic_gateway/component_sync.py; then
  bashio::log.warning "Automatic custom_component sync failed; add-on will continue to start"
fi

exec python3 /app/server.py
