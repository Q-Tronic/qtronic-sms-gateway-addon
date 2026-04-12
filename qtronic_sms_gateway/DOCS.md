# Q-Tronic SMS Gateway

`Q-Tronic SMS Gateway` to add-on Home Assistant dla ESPHome-based modemów GSM, takich jak NodeMCU + SIM800C.

## Co daje add-on

- konfigurację w `Aplikacje -> Q-Tronic SMS Gateway -> Konfiguracja`
- zakładki `Info`, `Dokumentacja`, `Konfiguracja`, `Log`
- Ingress web UI przez `Otwórz interfejs użytkownika`
- połączenie do węzła ESPHome po `Native API`
- REST API dla przyszłej integracji
- MQTT publish/subscribe w stylu bramki SMS

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
