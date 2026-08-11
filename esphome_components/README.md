# Q-Tronic ESPHome components

Ten katalog zawiera komponenty firmware dla bramki NodeMCU ESP8266 + SIM800C.
Źródła bazują na ESPHome 2026.7.4 i podlegają licencjom z pliku
`ESPHome-LICENSE`.

## Zmiany Q-Tronic

### `uart`

Fallback programowego UART-u ESP8266 korzysta z `EspSoftwareSerial` 8.0.1.
ISR zapisuje wyłącznie poziom i czas zbocza, a dekodowanie ramki odbywa się w
zwykłej pętli. Transmisja nie blokuje przerwań Wi-Fi przez całą ramkę.
`USE_UART_WAKE_LOOP_ON_RX` nie jest włączane na ESP8266. Wariant był testowany
dla GPIO14/GPIO13 przy 9600 baud.

### `sim800l`

- jeden właściciel UART i jedna maszyna stanów dla pollingu, SMS, UCS2, USSD i
  połączeń;
- kolejka maksymalnie czterech SMS-ów łącznie z aktualnie wysyłanym;
- osobne ścieżki `sim800l.send_sms` oraz `sim800l.send_sms_unicode`;
- pojedyncze żądanie odpowiada jednemu SMS-owi: maksymalnie 160 jednostek GSM-7
  albo 70 jednostek UCS2 (280 znaków hex); dzielenie dłuższych wiadomości na
  osobne części wykonuje add-on przed przekazaniem ich do ESP;
- `send_sms_unicode` zachowuje kontrakt legacy: numer i wiadomość muszą być już
  zakodowane jako UCS2 hex (UTF-16BE), bez ponownego kodowania w ESP;
- oczekiwanie na prompt `>`, `+CMGS` i końcowe `OK`; potwierdzenie wysyłki ma
  deadline 65 sekund;
- timeout przed przesłaniem treści może ponowić żądanie maksymalnie dwa razy;
  timeout po Ctrl-Z ma wynik `unknown` i nie jest automatycznie ponawiany, aby
  nie tworzyć duplikatów;
- recovery używa `ESC` wyłącznie podczas oczekiwania na prompt. Ctrl-Z występuje
  tylko podczas świadomego zatwierdzania gotowej treści SMS;
- `ERROR`, `+CME ERROR` i `+CMS ERROR` są obsługiwane natychmiast;
- CSQ jest przeliczane na dBm: `-113 + 2 * CSQ`; wartość 99 publikuje `NaN`;
- brak restartowania ESP z powodu niedostępnego modemu.

## Konfiguracja docelowa

Poniższe fragmenty zastępują surowe komendy AT i opóźnienia w dotychczasowej
akcji `send_sms_unicode`.

```yaml
external_components:
  - source:
      type: local
      path: esphome_components
    components:
      - sim800l
      - uart

api:
  # Zalecane dla autonomicznej bramki: restart HA nie restartuje ESP po 15 min.
  reboot_timeout: 0s
  encryption:
    key: !secret esphome_api_key
  actions:
    - action: send_sms
      variables:
        recipient: string
        message: string
      then:
        - sim800l.send_sms:
            recipient: !lambda "return recipient;"
            message: !lambda "return message;"

    - action: send_sms_unicode
      variables:
        recipient: string
        message: string
      then:
        - sim800l.send_sms_unicode:
            recipient: !lambda "return recipient;"
            message: !lambda "return message;"

    - action: send_ussd
      variables:
        ussd: string
      then:
        - sim800l.send_ussd:
            ussd: !lambda "return ussd;"

    - action: dial
      variables:
        recipient: string
      then:
        - sim800l.dial:
            recipient: !lambda "return recipient;"

    - action: disconnect
      then:
        - sim800l.disconnect

uart:
  id: sim800_uart
  tx_pin: GPIO14
  rx_pin:
    number: GPIO13
    mode:
      input: true
      pullup: true
  baud_rate: 9600

text_sensor:
  # Istniejące template text_sensor mogą pozostać obok tej platformy.
  - platform: sim800l
    sim800l_id: sim800_modem
    sms_status:
      id: sms_status
      name: "SMS Status"
    sms_last_error:
      id: sms_last_error
      name: "SMS Last Error"
    sim800_state:
      id: sim800_state
      name: "SIM800 State"

sensor:
  - platform: sim800l
    sim800l_id: sim800_modem
    rssi:
      id: sim800_rssi
      name: "RSSI"
    sms_queue_depth:
      id: sms_queue_depth
      name: "SMS Queue Depth"
    sms_sent_count:
      id: sms_sent_count
      name: "SMS Sent Count"
    sms_failed_count:
      id: sms_failed_count
      name: "SMS Failed Count"
    sms_unknown_count:
      id: sms_unknown_count
      name: "SMS Unknown Count"
    sim800_timeout_count:
      id: sim800_timeout_count
      name: "SIM800 Timeout Count"
    sim800_recovery_count:
      id: sim800_recovery_count
      name: "SIM800 Recovery Count"
    uart_rx_overflow_count:
      id: uart_rx_overflow_count
      name: "UART RX Overflow Count"
    sim800_last_response_age:
      id: sim800_last_response_age
      name: "SIM800 Last Response Age"

binary_sensor:
  - platform: sim800l
    sim800l_id: sim800_modem
    registered:
      id: registered
      name: "Registered"

sim800l:
  id: sim800_modem
  uart_id: sim800_uart
  update_interval: 10s
  on_sms_received:
    - lambda: |-
        id(sms_sender).publish_state(sender);
        id(sms_message).publish_state(message);
  on_incoming_call:
    - lambda: |-
        id(incoming_call).publish_state(caller_id);
        id(call_state).publish_state("ringing");
  on_call_connected:
    - lambda: |-
        id(call_state).publish_state("connected");
  on_call_disconnected:
    - lambda: |-
        id(call_state).publish_state("disconnected");
  on_ussd_received:
    - lambda: |-
        id(ussd_message).publish_state(ussd);
```

## Znaczenie diagnostyki

- `sms_status`: `idle`, `queued`, `sending`, `sent`, `failed` albo `unknown`;
- `sent`: SIM800 zwrócił `+CMGS` i końcowe `OK`; nie jest to raport doręczenia
  do telefonu odbiorcy;
- `unknown`: treść została zatwierdzona Ctrl-Z, ale zabrakło jednoznacznego
  potwierdzenia; firmware celowo nie ponawia takiego SMS-a;
- liczniki są zerowane po restarcie ESP;
- `uart_rx_overflow_count` obejmuje błędy odczytu oraz przepełnienie 1024-bajtowego
  bufora linii parsera SIM800. Prywatny bufor krawędzi `EspSoftwareSerial` nie
  udostępnia obecnie osobnego licznika przez API komponentu UART.

Po aktualizacji ESPHome trzeba porównać lokalne kopie `uart` i `sim800l` z nowym
upstreamem przed kompilacją. Nie należy bezrefleksyjnie nadpisywać tych plików.
