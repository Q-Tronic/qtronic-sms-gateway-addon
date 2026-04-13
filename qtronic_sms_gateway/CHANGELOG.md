# Changelog

## 0.4.0

- Vendor the `qtronic_sms_gateway` custom integration inside the add-on image
- Automatically synchronize the custom component into `/config/custom_components/qtronic_sms_gateway` on add-on startup
- Request writable access to Home Assistant config so the add-on can restore the HTTP-backed integration UX
- Create a Home Assistant notification when the synced custom component requires a core restart

## 0.3.1

- Fix Ingress frontend API URLs by building them from the actual Home Assistant ingress `root_path`
- Disable dashboard HTML caching to avoid stale frontend code after add-on updates

## 0.3.0

- Add MQTT-discovered `notify` entities for saved-recipient SMS sending from Home Assistant automations
- Improve MQTT discovery for call/SMS controls with stable entity IDs and extra discovery logs
- Request `homeassistant` API role so inbound SMS and call events can be bridged back to the Home Assistant event bus

## 0.2.1

- Fix Ingress dashboard API URLs so the web UI talks to the add-on instead of Home Assistant core `/api/*`

## 0.2.0

- Add detailed SMS and call logging in the add-on logs
- Expose MQTT-discovered controls for sending SMS, calling, and hanging up from Home Assistant
- Bridge inbound SMS and incoming call events back to the Home Assistant event bus for automations

## 0.1.3

- Fix Alpine PEP 668 build failure by allowing pip installs in the add-on image

## 0.1.2

- Fix add-on image build by quoting pip version specifiers in Dockerfile

## 0.1.1

- Mask public example phone numbers in documentation and UI placeholders
- Publish refreshed add-on package so Home Assistant can detect the update

## 0.1.0

- Initial add-on scaffold
- Ingress web UI
- ESPHome Native API backend
- MQTT publish/subscribe support
- REST API skeleton for SMS and calls
