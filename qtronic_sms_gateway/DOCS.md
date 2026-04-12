# Q-Tronic SMS Gateway

`Q-Tronic SMS Gateway` to add-on Home Assistant dla ESPHome-based modemów GSM, takich jak NodeMCU + SIM800C.

## Co daje add-on

- konfigurację w `Aplikacje -> Q-Tronic SMS Gateway -> Konfiguracja`
- zakładki `Info`, `Dokumentacja`, `Konfiguracja`, `Log`
- Ingress web UI przez `Otwórz interfejs użytkownika`
- połączenie do węzła ESPHome po `Native API`
- REST API dla przyszłej integracji
- MQTT publish/subscribe w stylu bramki SMS
- eventy Home Assistant dla triggerów automatyzacji

## Quick Start

1. Wgraj firmware ESPHome z akcjami:
   - `send_sms`
   - `send_sms_unicode`
   - `dial`
   - `disconnect`
2. W add-onie ustaw:
   - `ESPHome host`
   - `ESPHome port`
   - `ESPHome encryption key`
3. Jeśli chcesz MQTT, włącz sekcję `MQTT` i ustaw broker.
4. Uruchom add-on.
5. Wejdź w `Otwórz interfejs użytkownika`.

## MQTT

Przykładowe topic prefixes:

- publish status: `<topic_prefix>/status`
- publish state: `<topic_prefix>/state/...`
- publish inbound SMS: `<topic_prefix>/event/sms_received`
- publish incoming call: `<topic_prefix>/event/incoming_call`
- subscribe send SMS: `<topic_prefix>/send_sms/set`
- subscribe call: `<topic_prefix>/call/set`
- subscribe hangup: `<topic_prefix>/hangup/set`
- control state SMS targets: `<topic_prefix>/control/sms_targets/state`
- control state SMS message: `<topic_prefix>/control/sms_message/state`
- control state call targets: `<topic_prefix>/control/call_targets/state`
- control state call ring time: `<topic_prefix>/control/call_ring_time/state`
- action button send SMS: `<topic_prefix>/action/send_sms/press`
- action button call: `<topic_prefix>/action/call/press`
- action button hangup: `<topic_prefix>/action/hangup/press`

Przykładowy payload `send_sms`:

```json
{
  "recipient": "+48xxxxxx542",
  "message": "Test z MQTT",
  "encoding": "auto"
}
```

Przykładowy payload `call`:

```json
{
  "recipient": "+48xxxxxx542",
  "ring_time_s": 20
}
```

Możesz też używać `recipient_id`, jeśli numer jest zapisany w `recipients`.

## REST API

Przydatne endpointy:

- `GET /health`
- `GET /api/status`
- `GET /api/events`
- `GET /api/config`
- `POST /api/send-sms`
- `POST /api/call`
- `POST /api/hangup`

## Home Assistant automations

Add-on publikuje też eventy bezpośrednio do Home Assistant:

- `qtronic_sms_gateway_sms_received`
- `qtronic_sms_gateway_incoming_call`
- `qtronic_sms_gateway_sms_sent`
- `qtronic_sms_gateway_sms_batch_finished`
- `qtronic_sms_gateway_call_batch_finished`

Przykład triggera po SMS:

```yaml
triggers:
  - trigger: event
    event_type: qtronic_sms_gateway_sms_received
    event_data:
      saved_recipient_id: przemek
      message_search: swiatlo
```

Przykład triggera po połączeniu:

```yaml
triggers:
  - trigger: event
    event_type: qtronic_sms_gateway_incoming_call
    event_data:
      saved_recipient_id: przemek
```

Jeśli chcesz sterować add-onem z automatyzacji bez custom integration, po MQTT discovery pojawią się też encje:

- tekstowe pola dla SMS/call targets
- pole wiadomości SMS
- wybór kodowania SMS
- czas dzwonienia
- przyciski `Send SMS`, `Call`, `Hang Up`
- dodatkowe przyciski `Send SMS to <recipient>` i `Call <recipient>` dla zapisanych odbiorców

## Saved Recipients

W sekcji `recipients` możesz zdefiniować listę osób:

```yaml
recipients:
  - id: przemek
    name: Przemek
    phone: "+48xxxxxx542"
  - id: marta
    name: Marta
    phone: "+48xxxxxx222"
```

Jeśli `id` nie zostanie podane, add-on wygeneruje je z nazwy.

## Uwaga

Ten add-on jest fundamentem pod docelową integrację HA. Obecnie zapewnia:

- połączenie z ESPHome
- publikację stanów i zdarzeń
- REST i MQTT jako warstwę komunikacji

## Źródła

- Home Assistant app configuration: https://developers.home-assistant.io/docs/apps/configuration/
- Home Assistant app repository: https://developers.home-assistant.io/docs/add-ons/repository
- ESPHome SIM800L: https://esphome.io/components/sim800l/
