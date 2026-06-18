from __future__ import annotations

from datetime import datetime
import logging
from functools import lru_cache

from models import CelestialBodyState


LOG = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _load_skyfield():
    from skyfield.api import load, wgs84

    eph = load("de421.bsp")
    ts = load.timescale()
    return eph, ts, wgs84


def get_body_states(lat: float, lon: float, timestamp: datetime) -> list[CelestialBodyState]:
    try:
        eph, ts, wgs84 = _load_skyfield()
        t = ts.from_datetime(timestamp)
        observer = eph["earth"] + wgs84.latlon(lat, lon)
        states = [
            _body_state("Sun", eph["sun"], observer, t, 0.2666, None),
            _body_state("Moon", eph["moon"], observer, t, 0.2725, _moon_illumination(eph, t)),
        ]
        return states
    except Exception as exc:
        LOG.error("Ephemeris calculation failed error=%s", exc)
        return []


def _body_state(body: str, target, observer, t, radius_deg: float, illumination: float | None) -> CelestialBodyState:
    apparent = observer.at(t).observe(target).apparent()
    alt, az, _distance = apparent.altaz()
    return CelestialBodyState(
        body=body,
        timestamp=t.utc_datetime(),
        azimuth_deg=az.degrees % 360,
        elevation_deg=alt.degrees,
        angular_radius_deg=radius_deg,
        illumination=illumination,
    )


def _moon_illumination(eph, t) -> float | None:
    try:
        from skyfield import almanac

        return float(almanac.fraction_illuminated(eph, "moon", t))
    except Exception:
        return None
