from __future__ import annotations

from datetime import timedelta

from geo import destination_point, topocentric_aircraft_position
from models import AircraftState, PredictedPoint


def predict_aircraft_path(
    aircraft: AircraftState,
    observer_lat: float,
    observer_lon: float,
    horizon_seconds: int,
    step_seconds: int,
) -> list[PredictedPoint]:
    if aircraft.ground_speed_kt is None or aircraft.track_deg is None:
        return []

    points: list[PredictedPoint] = []
    step = max(1, step_seconds)
    for dt_seconds in range(0, horizon_seconds + 1, step):
        dt_hours = dt_seconds / 3600.0
        distance_km = aircraft.ground_speed_kt * 1.852 * dt_hours
        lat, lon = destination_point(aircraft.lat, aircraft.lon, aircraft.track_deg, distance_km)
        altitude_ft = aircraft.altitude_ft
        if altitude_ft is not None and aircraft.vertical_rate_fpm is not None:
            altitude_ft = max(0.0, altitude_ft + aircraft.vertical_rate_fpm * (dt_seconds / 60.0))
        az, el, rng = topocentric_aircraft_position(observer_lat, observer_lon, lat, lon, altitude_ft)
        points.append(
            PredictedPoint(
                timestamp=aircraft.timestamp + timedelta(seconds=dt_seconds),
                lat=lat,
                lon=lon,
                altitude_ft=altitude_ft,
                azimuth_deg=az,
                elevation_deg=el,
                range_km=rng,
            )
        )
    return points
