# Q-Tronic SMS Gateway Add-on

Add-on dla Home Assistant Supervisor, który łączy się z węzłem ESPHome przez sieć i wystawia:

- Ingress UI
- REST API
- MQTT publish/subscribe
- obsługę wysyłki SMS
- obsługę połączeń
- publikację zdarzeń przychodzących
- uwierzytelnione REST API, trwałą historię i kolejkę operacji
- reguły SMS z PIN-em/challenge, warunkami i sterowaniem encjami Home Assistant
- USSD, anulowanie aktywnego batcha i diagnostykę transportu SIM800C

Dokumentacja zawiera również sprawdzoną konfigurację lokalnych komponentów
ESPHome dla stabilnej pracy NodeMCU ESP8266 z modemem SIM800C na GPIO14/GPIO13.

Pełna konfiguracja, opis zmiennych i instrukcja aktualizacji znajdują się w
[`DOCS.md`](DOCS.md). Wydanie `0.5.0` wymaga aktualnego firmware z lokalnymi
komponentami z katalogu `esphome_components`.
