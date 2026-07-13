# Changelog

## 0.4.12

- Add the `{data_czas}` variable to success, state, and failure SMS reply templates
- Format the reply dispatch time in the Home Assistant local time zone

## 0.4.11

- Add native inbound SMS and incoming-call forwarding to the bundled Home Assistant integration
- Configure forwarding recipients and excluded saved contacts or phone numbers from the integration UI
- Preserve the existing timestamp, sender, message, and incoming-call SMS formatting with editable templates
- Prevent self-echo by always removing the inbound sender or caller from forwarding targets
- Skip forwarding messages recognized as SMS command rules by default
- Isolate forwarded events per configured gateway and process tasks through the config-entry lifecycle

## 0.4.10

- Fix inbound SMS command listeners being dispatched to a worker thread on Home Assistant 2026.7
- Schedule SMS rule processing safely on the Home Assistant event loop
- Move custom-integration manifest reads out of the event loop to avoid blocking-I/O warnings

## 0.4.9

- Add native inbound SMS command rules to the bundled Home Assistant integration
- Select searchable Home Assistant entities by friendly name or entity ID
- Match multi-word SMS commands while ignoring Polish diacritics, letter case, and repeated whitespace
- Authorize senders by saved recipient or manually configured phone number
- Support `turn_on`, `turn_off`, `toggle`, and state-reporting rules
- Send configurable success, state, and failure replies back to the original sender
- Add reply variables for entity value, localized state, unit, name, sender, and command
- Enrich Home Assistant events with add-on and ESPHome gateway source identifiers
- Fix persistence of the Modem Online entity-mapping override

## 0.4.8

- Add the ESPHome `modem_online` watchdog role to detect a powered-off or unresponsive SIM800C
- Distinguish modem `offline`, `not_registered`, `online`, and `unknown` states
- Publish the Modem Online binary sensor through REST, MQTT discovery, and the bundled integration
- Document the required ESPHome heartbeat configuration and 30-second timeout
- Replace placeholder repository URLs with the public GitHub repository URL

## 0.4.7

- Add separate ESP and SIM800C diagnostic status to the Ingress dashboard and REST status payload
- Publish ESP and SIM800C diagnostic sensors through MQTT discovery
- Add ESP Status and SIM800C Status diagnostic entities to the bundled Home Assistant integration
- Report SIM800C as `not_registered` instead of claiming a definite power failure when registration is unavailable

## 0.4.6

- Re-emit `incoming_call` events for repeated calls from the same number instead of requiring the sensor text to change
- Re-emit `sms_received` events for repeated identical SMS messages instead of requiring the message sensor text to change
- Add inbound SMS and call debounce logs so duplicate suppression is visible in add-on logs

## 0.4.5

- Make the `Konfiguracja` card span the full dashboard row
- Wrap long JSON lines in the configuration preview so the full text stays readable

## 0.4.4

- Fix the ingress dashboard frontend to build API URLs from the current browser path instead of relying on FastAPI `root_path`
- Prevent the dashboard from accidentally calling Home Assistant core `/api/config` and `/api/events`

## 0.4.3

- Capture the real add-on hostname during component sync and use it as the suggested integration host
- Bump the vendored custom integration to `0.6.3`

## 0.4.2

- Remove the unused compatibility `encryption_key` field from the synced integration config flow
- Pre-fill the integration host with the add-on hostname `qtronic_sms_gateway`
- Bump the vendored custom integration to `0.6.2`

## 0.4.1

- Write a restart-required marker after syncing the vendored custom component
- Let the synced integration surface a native Home Assistant repair issue when a core restart is required
- Update the synced integration metadata and translations for the add-on HTTP backend flow

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
