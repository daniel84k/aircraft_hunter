# Status

Data: 2026-06-18

## Aktualny stan

- Aplikacja `app` zostala przebudowana i uruchomiona po ostatnich zmianach.
- Testy lokalne przeszly: `31 passed`.
- Powiadomienia Telegram sa aktywne; nie zapisano sekretow w tym pliku.
- Wyslano testowa wiadomosc Telegram dla `PGT1DB` z linkiem Flightradar24 i lokalizacja testowa.

## Zmiany w detekcji i alertach

- Poluzowano scoring dystansu obserwatora o 20%.
- Progi `observer_distance_score` sa teraz:
  - do `2.4 km`: `1.0`
  - do `4.8 km`: `0.8`
  - do `6.0 km`: `0.4`
  - powyzej `6.0 km`: `0.0`
- Nie zmieniano geometrii tranzytu, cooldownow Telegrama ani limitow wysylki.

## Telegram

- Do tresci alertu dodano link Flightradar24:
  - format: `https://www.flightradar24.com/{CALLSIGN}`
  - fallback: ICAO, jesli callsign jest pusty.
- Link jest dodawany w `format_alert()` w `src/alerts.py`.
- Dodano testy w `tests/test_alerts.py`.

## Ostatnie obserwacje z logow

- W ostatnich sprawdzeniach nie dominowalo odrzucenie `OBSERVER_POINT_TOO_FAR`.
- Dominujacy powod odrzucen kandydatow to `LOW_SCORE`.
- W praktyce dystans nadal jest czescia score, ale po zmianie nie zeruje wyniku juz po `5 km`, tylko po `6 km`.

## Pliki zmienione w ostatnim kroku

- `src/scoring.py`
- `tests/test_scoring.py`
- `src/alerts.py`
- `tests/test_alerts.py`
- `status.md`
