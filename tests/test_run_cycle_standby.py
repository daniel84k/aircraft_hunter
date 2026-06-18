from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import replace

import main
from config import load_settings
from models import AircraftState, CelestialBodyState


class FailingADSBClient:
    def fetch_aircraft(self, lat: float, lon: float, radius_nm: float):
        raise AssertionError("ADSB fetch should be skipped while all bodies are below standby elevation")


class FailingStorage:
    def insert_observations(self, observations):
        raise AssertionError("observations should not be stored during standby")

    def start_prediction_run(self, started_at, settings, aircraft_count_total):
        raise AssertionError("prediction run should not start during standby")


def test_run_cycle_skips_adsb_fetch_when_all_bodies_below_standby(monkeypatch) -> None:
    settings = replace(load_settings(), standby_body_elevation_deg=5.0)

    def body_states(lat, lon, timestamp):
        return [
            CelestialBodyState("Sun", timestamp, 0.0, -14.4, 0.2666),
            CelestialBodyState("Moon", timestamp, 0.0, -10.0, 0.2725),
        ]

    monkeypatch.setattr(main, "get_body_states", body_states)

    histories: dict[str, deque[AircraftState]] = defaultdict(lambda: deque(maxlen=240))
    main.run_cycle(settings, FailingADSBClient(), FailingStorage(), histories)
