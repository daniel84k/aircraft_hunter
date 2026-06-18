from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any

import requests

from models import AircraftState


LOG = logging.getLogger(__name__)


class ADSBClient:
    def __init__(self, base_url: str, timeout_seconds: float = 8.0, retries: int = 2) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        self.session = requests.Session()

    def fetch_aircraft(self, lat: float, lon: float, radius_nm: float) -> list[AircraftState]:
        url = f"{self.base_url}/v2/lat/{lat}/lon/{lon}/dist/{radius_nm}"
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                started = datetime.now(timezone.utc)
                response = self.session.get(url, timeout=self.timeout_seconds)
                response.raise_for_status()
                data = response.json()
                elapsed_ms = (datetime.now(timezone.utc) - started).total_seconds() * 1000
                aircraft = self._parse_aircraft(data)
                LOG.info("ADSB fetch complete count=%s elapsed_ms=%.0f", len(aircraft), elapsed_ms)
                return aircraft
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                LOG.warning("ADSB fetch failed attempt=%s error=%s", attempt + 1, exc)
        LOG.error("ADSB API unavailable after retries error=%s", last_error)
        return []

    def _parse_aircraft(self, data: dict[str, Any]) -> list[AircraftState]:
        now = datetime.now(timezone.utc)
        records = data.get("aircraft") or data.get("ac") or []
        parsed: list[AircraftState] = []
        skipped = 0
        for item in records:
            state = parse_adsb_record(item, now)
            if state is None:
                skipped += 1
                continue
            parsed.append(state)
        if skipped:
            LOG.info("ADSB records skipped missing_position_track_or_speed=%s", skipped)
        return parsed


def _first(record: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return value
    return None


def parse_adsb_record(record: dict[str, Any], observed_at: datetime) -> AircraftState | None:
    lat = _first(record, "lat")
    lon = _first(record, "lon")
    track = _first(record, "track", "trak", "heading", "true_heading")
    gs = _first(record, "gs", "ground_speed", "speed")
    icao = _first(record, "hex", "icao", "icao24")
    if lat is None or lon is None or track is None or gs is None or icao is None:
        return None

    altitude = _first(record, "alt_baro", "alt_geom", "altitude")
    if isinstance(altitude, str) and not altitude.replace(".", "", 1).isdigit():
        altitude = None

    return AircraftState(
        icao=str(icao).lower(),
        callsign=(str(_first(record, "flight", "callsign")).strip() or None)
        if _first(record, "flight", "callsign") is not None
        else None,
        lat=float(lat),
        lon=float(lon),
        altitude_ft=float(altitude) if altitude is not None else None,
        ground_speed_kt=float(gs),
        track_deg=float(track) % 360,
        vertical_rate_fpm=float(_first(record, "baro_rate", "geom_rate", "vertical_rate") or 0.0),
        aircraft_type=_first(record, "t", "type", "aircraft_type"),
        origin=_first(record, "orig", "origin"),
        destination=_first(record, "dest", "destination"),
        timestamp=observed_at,
        raw_json=record,
    )
