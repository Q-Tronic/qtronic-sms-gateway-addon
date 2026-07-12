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
- automatyczne przywracanie `custom_component` do `/config/custom_components/qtronic_sms_gateway`
- powrót do prostych akcji `qtronic_sms_gateway.send_sms` i `qtronic_sms_gateway.call_to` po restarcie HA
- osobne statusy diagnostyczne ESP i SIM800C w dashboardzie, MQTT i integracji HA

Status `SIM800C: ONLINE` oznacza, że modem jest zarejestrowany w sieci GSM.
`OFFLINE / brak rejestracji` oznacza brak rejestracji; bez dodatkowego sensora
zasilania nie pozwala rozróżnić wyłączonego modemu, braku karty SIM i braku zasięgu.

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
5. Add-on zsynchronizuje `custom_component` do katalogu HA.
6. Jeśli restart jest wymagany, add-on zapisze znacznik, a integracja pokaże wpis `restart required` w ustawieniach Home Assistant.
7. Zrestartuj Home Assistant, aby od razu odzyskać akcje `send_sms` / `call_to`, encje `notify` i triggery `SMS/CALL`.
8. Wejdź w `Otwórz interfejs użytkownika`.

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
- natywne encje `notify` dla zapisanych odbiorców: `Q-Tronic SMS <recipient>`

### Wysyłanie SMS z automatyzacji

Najwygodniejsza ścieżka dla zapisanych odbiorców to `notify`.

Przykład:

```yaml
actions:
  - action: notify.qtronic_sms_gateway_sms_przemek
    data:
      message: "Brama została otwarta"
```

Dla wielu osób użyj kilku akcji `notify`, po jednej na odbiorcę.

Jeśli chcesz użyć pól MQTT helpers, możesz też zrobić:

```yaml
actions:
  - action: text.set_value
    target:
      entity_id: text.qtronic_sms_gateway_sms_targets
    data:
      value: "przemek,marta"
  - action: text.set_value
    target:
      entity_id: text.qtronic_sms_gateway_sms_message_input
    data:
      value: "Alarm w garażu"
  - action: button.press
    target:
      entity_id: button.qtronic_sms_gateway_send_sms
```

### Wykonywanie połączeń z automatyzacji

Dla zapisanych odbiorców użyj przycisków `button`.

Przykład:

```yaml
actions:
  - action: button.press
    target:
      entity_id: button.qtronic_sms_gateway_call_przemek
```

Dla numeru ręcznego albo kilku odbiorców przez helpery:

```yaml
actions:
  - action: text.set_value
    target:
      entity_id: text.qtronic_sms_gateway_call_targets
    data:
      value: "przemek,marta"
  - action: number.set_value
    target:
      entity_id: number.qtronic_sms_gateway_call_ring_time
    data:
      value: 20
  - action: button.press
    target:
      entity_id: button.qtronic_sms_gateway_call
```

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

Add-on jest teraz backendem i menedżerem integracji:

- łączy się z ESPHome
- publikuje stany i zdarzenia
- wystawia REST i MQTT jako warstwę komunikacji
- automatycznie instaluje HTTP-backed `custom_component`, który przywraca klasyczne akcje HA
- przy dodawaniu integracji domyślnie podpowiada realny hostname add-onu zapisany podczas synchronizacji i nie pokazuje już zbędnego pola `encryption_key`
- dashboard ingress buduje endpointy API z bieżącego URL przeglądarki, więc nie powinien już wpadać w błędy autoryzacji Home Assistanta na `/api/config` i `/api/events`
- karta `Konfiguracja` w dashboardzie zajmuje pełną szerokość rzędu i zawija długie linie JSON-a

## Źródła

- Home Assistant app configuration: https://developers.home-assistant.io/docs/apps/configuration/
- Home Assistant app repository: https://developers.home-assistant.io/docs/add-ons/repository
- ESPHome SIM800L: https://esphome.io/components/sim800l/
