# Q-Tronic SMS Gateway Add-on Repository

Lokalne repozytorium add-onu `Q-Tronic SMS Gateway` dla Home Assistant Supervisor.

Repo zawiera:

- add-on z zakładkami `Info`, `Dokumentacja`, `Konfiguracja`, `Log`
- web UI przez Ingress
- backend pod ESPHome Native API
- REST API
- MQTT publish/subscribe do sterowania i zdarzeń
- odtwarzalne komponenty ESPHome dla stabilnej obsługi ESP8266 + SIM800C
- trwałą kolejkę i historię z kontrolą prywatności
- reguły SMS sterujące encjami Home Assistant, z PIN-em, challenge i warunkami
- diagnostykę transportu, USSD oraz bezpieczne anulowanie połączeń i batchy

Folder add-onu:

- `qtronic_sms_gateway`

Komponenty firmware ESPHome:

- [`esphome_components`](esphome_components)

Dodanie jako lokalne repo:

1. Skopiuj cały katalog `Addon Q-Tronic SMS Gateway` do lokalnych add-onów HA albo podepnij go jako repozytorium lokalne.
2. W Home Assistant przejdź do `Ustawienia -> Aplikacje`.
3. Dodaj lokalne repozytorium i zainstaluj `Q-Tronic SMS Gateway`.
