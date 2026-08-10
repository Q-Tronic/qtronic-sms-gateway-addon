# Q-Tronic ESPHome components

Ten katalog zawiera komponenty używane przez firmware bramki NodeMCU ESP8266 + SIM800C.
Są oparte na źródłach ESPHome 2026.7.4 i objęte licencjami opisanymi w
`ESPHome-LICENSE`.

## Zmiany Q-Tronic

- `sim800l` ma 15-sekundowy timeout maszyny stanów, dzięki czemu brak odpowiedzi
  modemu nie blokuje jej bez końca. Udostępnia też czas ostatniej rzeczywistej
  odpowiedzi przez `get_last_response_ms()`.
- `uart` zachowuje standardowy sterownik ESPHome, ale na ESP8266 nie włącza
  `USE_UART_WAKE_LOOP_ON_RX`. Programowy UART nadal odbiera wszystkie bajty do
  bufora, lecz nie wywołuje planisty sieci z ISR po każdym bajcie. Poprawka jest
  przeznaczona dla bramki pracującej na GPIO14/GPIO13 przy 9600 baud.

## Użycie

Skopiuj katalog `esphome_components` obok pliku YAML ESPHome i dodaj:

```yaml
external_components:
  - source:
      type: local
      path: esphome_components
    components:
      - sim800l
      - uart
```

Po kompilacji dla ESP8266 `USE_UART_WAKE_LOOP_ON_RX` nie może występować w
`.esphome/build/<nazwa>/src/esphome/core/defines.h`. Dla ESP32 zachowane jest
standardowe zachowanie ESPHome.
