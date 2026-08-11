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
- trwałą historię i kolejkę operacji, które nie giną po restarcie add-onu
- uwierzytelnione REST API, limity żądań i zamaskowane dane w historii
- chronione PIN-em lub jednorazowym potwierdzeniem polecenia SMS

Status `SIM800C: ONLINE` oznacza, że modem odpowiada i jest zarejestrowany w sieci GSM.
`OFFLINE (brak odpowiedzi)` oznacza, że watchdog nie otrzymał odpowiedzi modemu przez
30 sekund. `OFFLINE / brak rejestracji` oznacza, że modem odpowiada, ale nie jest
zarejestrowany (np. brak karty SIM, zasięgu albo problem operatora).

## Stabilniejszy UART i watchdog odpowiedzi SIM800C

Dla NodeMCU ESP8266 z SIM800C na GPIO14/GPIO13 użyj komponentów z katalogu
[`esphome_components`](https://github.com/Q-Tronic/qtronic-sms-gateway-addon/tree/main/esphome_components).
Lokalny `sim800l` ma jedną kolejkę i jedną maszynę stanów dla SMS-ów GSM/UCS2,
USSD, połączeń, odczytu wiadomości oraz odzyskiwania po zaniku zasilania modemu.
Nie wysyła surowych komend AT równolegle, rozróżnia wynik `sent`, `failed` i
`unknown`, a po niejednoznacznym timeoutcie nie ponawia automatycznie wiadomości,
co ogranicza ryzyko duplikatów. Lokalny `uart` używa na ESP8266 biblioteki
`EspSoftwareSerial` 8.0.1:
przerwanie zapisuje wyłącznie znaczniki zboczy, a dekodowanie pełnej ramki
odbywa się poza ISR. Dzięki temu odbiór SIM800C nie blokuje obsługi Wi-Fi przez
około 1 ms na każdy bajt. Transmisja również pozostawia przerwania włączone.

Komponenty są oparte i zweryfikowane z ESPHome `2026.7.4`. Po aktualizacji
Device Buildera do innej wersji trzeba porównać je z odpowiadającymi źródłami
ESPHome, ponownie skompilować firmware i wykonać test zaniku zasilania modemu.

Skopiuj cały katalog obok YAML-a ESPHome i dodaj:

```yaml
external_components:
  - source:
      type: local
      path: esphome_components
    components:
      - sim800l
      - uart
```

Skonfiguruj programowy UART z podciągnięciem RX. Nie używaj równolegle
`uart.debug` ani własnych cyklicznych komend `AT`, ponieważ konkurują one z
maszyną stanów komponentu SIM800L.

```yaml
uart:
  id: sim800_uart
  tx_pin: GPIO14
  rx_pin:
    number: GPIO13
    mode:
      input: true
      pullup: true
  baud_rate: 9600
```

Watchdog korzysta z czasu ostatniej rzeczywistej linii odebranej z modemu,
a nie ze zmiany stanu rejestracji:

```yaml
interval:
  - interval: 10s
    then:
      - lambda: |-
          const uint32_t last_response =
              id(sim800_modem).get_last_response_ms();
          const bool responsive =
              last_response != 0 &&
              static_cast<uint32_t>(millis() - last_response) < 30000;

          if (!id(modem_online).has_state() ||
              id(modem_online).state != responsive) {
            id(modem_online).publish_state(responsive);
          }

binary_sensor:
  - platform: template
    id: modem_online
    name: "Modem Online"
    device_class: connectivity

  - platform: sim800l
    registered:
      id: registered
      name: "Registered"

sim800l:
  id: sim800_modem
  uart_id: sim800_uart
  update_interval: 10s
```

Nie dodawaj drugiego sensora `registered`. Usuń starszy filtr, który aktualizował
watchdog przy każdej publikacji `registered`: stan `false` może pochodzić z
wewnętrznego timeoutu i nie dowodzi, że modem odpowiedział. Po utracie odpowiedzi
`Modem Online` przejdzie na `OFF` najpóźniej po około 30–40 sekundach. Powrót
zasilania modemu nie powoduje okresowego restartowania ESP; lokalny komponent
resetuje wyłącznie własną maszynę stanów.

## Quick Start

1. Skopiuj lokalne komponenty `sim800l` i `uart`, a następnie wgraj firmware
   ESPHome z akcjami:
   - `send_sms`
   - `send_sms_unicode`
   - `dial`
   - `disconnect`
   - `send_ussd`
   Pełny, aktualny YAML wraz z diagnostyką znajduje się w
   [`esphome_components/README.md`](https://github.com/Q-Tronic/qtronic-sms-gateway-addon/tree/main/esphome_components).
2. W add-onie ustaw:
   - `ESPHome host`
   - `ESPHome port`
   - `ESPHome encryption key`
3. Jeśli chcesz MQTT, włącz sekcję `MQTT` i ustaw broker.
4. Uruchom add-on.
5. Add-on zsynchronizuje `custom_component` do katalogu HA.
6. Jeśli restart jest wymagany, add-on zapisze znacznik, a integracja pokaże wpis `restart required` w ustawieniach Home Assistant.
7. Zrestartuj Home Assistant, aby od razu odzyskać akcje `send_sms`, `call_to`,
   `hangup`, `cancel_batch`, `send_ussd`, encje `notify` i triggery `SMS/CALL`.
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
- subscribe USSD: `<topic_prefix>/send_ussd/set`
- subscribe anulowanie batcha: `<topic_prefix>/cancel/set`
- control state SMS targets: `<topic_prefix>/control/sms_targets/state`
- control state SMS message: `<topic_prefix>/control/sms_message/state`
- control state call targets: `<topic_prefix>/control/call_targets/state`
- control state call ring time: `<topic_prefix>/control/call_ring_time/state`
- action button send SMS: `<topic_prefix>/action/send_sms/press`
- action button call: `<topic_prefix>/action/call/press`
- action button hangup: `<topic_prefix>/action/hangup/press`
- action button USSD: `<topic_prefix>/action/send_ussd/press`

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

`GET /health` pozostaje publiczny dla watchdoga Supervisora. Wszystkie endpointy
`/api/...` wymagają tokenu Bearer, chyba że żądanie pochodzi przez zaufany Ingress.
Token 256-bit jest tworzony automatycznie w
`/config/.qtronic_sms_gateway/api_token`; integracja Home Assistant odczytuje go
lokalnie, więc użytkownik nie musi go przepisywać. Przy bezpośrednim wywołaniu:

```text
Authorization: Bearer <token>
```

Najważniejsze endpointy:

- `GET /health`
- `GET /api/status`
- `GET /api/events`
- `GET /api/config`
- `GET /api/outbox`
- `GET /api/jobs/{job_id}`
- `GET /api/history`
- `GET /api/history/export?format=json|csv`
- `GET /api/config/export`
- `POST /api/send-sms`
- `POST /api/call`
- `POST /api/hangup`
- `POST /api/send-ussd`
- `POST /api/cancel`
- `POST /api/jobs/{job_id}/cancel`
- `POST /api/segments`

Operacje modyfikujące są limitowane per klient. Żądania mają wspólną, ścisłą
walidację typów, numerów, kodowania, długości wiadomości i czasu połączenia.
Wysłanie SMS-a zwraca identyfikator zadania; stan można sprawdzić w outboxie.
`accepted` oznacza przyjęcie do kolejki, `sent` oznacza `+CMGS` i końcowe `OK`
modemu, ale nie jest raportem doręczenia do telefonu.

Historia jest przechowywana w SQLite z retencją i limitem wpisów. Poziom
prywatności `masked` ukrywa numery oraz treści w zapisie audytowym; `metadata`
pozostawia tylko metadane, a `full` zapisuje pełną treść.

## Home Assistant automations

Add-on publikuje też eventy bezpośrednio do Home Assistant:

- `qtronic_sms_gateway_sms_received`
- `qtronic_sms_gateway_incoming_call`
- `qtronic_sms_gateway_sms_sent`
- `qtronic_sms_gateway_sms_batch_finished`
- `qtronic_sms_gateway_call_batch_finished`

## Polecenia i odpowiedzi SMS

Bundled custom integration pozwala wykonywać bezpieczne polecenia Home Assistant
na podstawie odebranych wiadomości SMS. Otwórz:

`Ustawienia -> Urządzenia i usługi -> Q-Tronic SMS Gateway -> Konfiguruj -> Polecenia i odpowiedzi SMS`

Każda reguła zawiera:

- zapisanego użytkownika albo ręcznie wpisany dozwolony numer nadawcy
- wielowyrazową treść polecenia
- dopasowanie dokładne, `zawiera` albo `zaczyna się od`
- jedną lub wiele wyszukiwalnych encji Home Assistant wybieranych po nazwie lub `entity_id`
- operację sterującą, odczyt stanu albo bezpiecznie ograniczone wywołanie usługi
- opcjonalne odpowiedzi po sukcesie i błędzie
- priorytet i cooldown
- opcjonalny PIN, jednorazowe potwierdzenie i warunki czasu/stanu

Komendy są porównywane bez uwzględniania wielkości liter, polskich znaków oraz
nadmiarowych spacji. Przykładowo wszystkie poniższe wiadomości są równoważne:

```text
WŁĄCZ ŚWIATŁO W GARAŻU
włącz światło w garażu
Wlacz   swiatlo   w garazu
```

Tryb odczytu stanu nie zmienia encji. Przykładowa reguła może reagować na SMS
`temperatura salon` i odesłać wartość sensora temperatury albo reagować na
`stan bramy` i zwrócić `otwarta` / `zamknięta`.

Szablony odpowiedzi obsługują następujące zmienne:

- `{data_czas}` - data i czas wysłania odpowiedzi w lokalnej strefie Home Assistanta
- `{zmienna}` - surowa wartość encji, np. `21.5`
- `{stan}` - czytelny stan, np. `otwarta`, `zamknięta`, `włączony`
- `{jednostka}` - jednostka encji, np. `°C`
- `{nazwa_encji}` - przyjazna nazwa encji
- `{entity_id}` - identyfikator encji
- `{nadawca}` - nazwa zapisanego użytkownika albo zamaskowany numer
- `{komenda}` - oryginalna odebrana wiadomość

Przykładowe odpowiedzi:

```text
{data_czas}
Temperatura w salonie: {stan} {jednostka}

Temperatura w salonie to {zmienna} {jednostka}
Brama jest {stan}
Wykonano polecenie dla {nazwa_encji}. Nowy stan: {stan}
```

Pierwsza pasująca reguła jest wykonywana, a identyczne komendy dla tego samego
nadawcy są blokowane podczas konfiguracji, aby uniknąć niejednoznacznych akcji.
Reguły są oceniane według priorytetu. W menu można zmienić ich kolejność,
uruchomić tester bez wykonania akcji oraz zobaczyć ostrzeżenia o kolizjach dla
trybów `dokładnie`, `zaczyna się od` i `zawiera`.

Polecenie może przechwycić parametr jako `{value}`, np. `ustaw salon 21.5`, i
przekazać go do `input_number`, `number` albo obsługiwanej usługi. Obsługiwane są
również akcje dla `switch`, `light`, `fan`, `input_boolean`, `cover`, `lock`,
`alarm_control_panel`, `script` i `scene`. Operacje wrażliwe można zabezpieczyć:

- PIN-em zapisanym wyłącznie jako PBKDF2-SHA256 z losową solą;
- jednorazowym kodem o krótkim TTL, odsyłanym jako prośba o wiadomość
  `POTWIERDZ 123456`;
- limitem globalnym, limitem per nadawca, blokadą po błędnych PIN-ach i cooldownem;
- przedziałem godzinowym albo wymaganym stanem innej encji.

Do autoryzacji używany jest pełny znormalizowany numer, a nie zgodność po kilku
ostatnich cyfrach. Luźne dopasowanie służy wyłącznie do wyświetlenia nazwy
kontaktu. Kontakty, reguły, ich kolejność i ustawienia bezpieczeństwa można
eksportować oraz importować jako zwalidowany JSON; sekrety PIN nie są eksportowane
w postaci jawnej.

## Diagnostyka transportu

Nowy firmware publikuje między innymi `SMS Status`, `SMS Last Error`, `SIM800
State`, długość kolejki, liczniki `sent`/`failed`/`unknown`, timeouty, recovery,
błędy UART RX i wiek ostatniej odpowiedzi modemu. Add-on udostępnia je przez REST,
MQTT i natywną integrację. Diagnostyka integracji maskuje tokeny, numery i treść
wiadomości, a Repairs zgłasza brak wymaganych akcji lub mapowań.

Wysyłka długiej wiadomości jest liczona według GSM 03.38 albo UTF-16/UCS2.
Przekroczenie skonfigurowanej liczby segmentów powoduje odrzucenie. Gdy włączysz
`split_long`, add-on dzieli tekst na bezpieczne części bez rozcinania par
zastępczych Unicode.

## Przekazywanie SMS-ów i informacji o połączeniach

Przekazywanie można skonfigurować bez osobnej automatyzacji Home Assistant:

`Ustawienia -> Urządzenia i usługi -> Q-Tronic SMS Gateway -> Konfiguruj -> Przekazywanie SMS i połączeń`

Formularz pozwala:

- włączyć niezależnie przekazywanie odebranych SMS-ów
- włączyć wysyłanie SMS-a informacyjnego o połączeniu przychodzącym
- wybrać jednego lub wielu zapisanych odbiorców
- wykluczyć zapisanych nadawców oraz dodatkowe ręcznie wpisane numery
- zdecydować, czy SMS-y rozpoznane jako polecenia również mają być przekazywane
- zmienić szablony wiadomości

Integracja zawsze porównuje numer źródłowy z numerami odbiorców i usuwa go z
listy docelowej. Dzięki temu wiadomość nigdy nie jest odsyłana jako kopia do
osoby, która ją wysłała. Komendy SMS domyślnie nie są przekazywane.

Domyślne szablony zachowują format znany z automatyzacji:

```text
{data_czas}
OD: {nadawca}
SMS: {wiadomosc}
```

```text
{data_czas}
POŁĄCZENIE OD: {dzwoniacy}
```

Dostępne zmienne:

- `{data_czas}` - data i czas odebrania wiadomości lub połączenia w lokalnej strefie Home Assistanta
- `{nadawca}` - numer telefonu nadawcy SMS-a lub osoby dzwoniącej
- `{nazwa_nadawcy}` - nazwa zapisanego nadawcy lub jego numer
- `{wiadomosc}` - oryginalna treść odebranego SMS-a
- `{dzwoniacy}` - numer telefonu osoby dzwoniącej
- `{typ}` - rodzaj zdarzenia: `SMS` albo `POŁĄCZENIE`

Powiadomienie o połączeniu jest wiadomością SMS; nie jest przekierowaniem
rozmowy w sieci GSM.

Po zapisaniu tej konfiguracji wyłącz lub usuń wcześniejszą automatyzację
forwardującą, aby nie otrzymywać zdublowanych powiadomień.

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
- ESPHome UART: https://esphome.io/components/uart/
- Lokalne komponenty Q-Tronic: https://github.com/Q-Tronic/qtronic-sms-gateway-addon/tree/main/esphome_components
