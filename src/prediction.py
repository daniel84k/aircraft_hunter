from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
import math

from geo import destination_point, topocentric_aircraft_position
from models import AircraftState, PredictedPoint


def predict_aircraft_path(
    aircraft: AircraftState,
    observer_lat: float,
    observer_lon: float,
    horizon_seconds: int,
    step_seconds: int,
    *,
    history: Sequence[AircraftState] | None = None,
    use_history_fit: bool = False,
    fit_window_seconds: int = 90,
    fit_min_points: int = 4,
) -> list[PredictedPoint]:
    if aircraft.ground_speed_kt is None or aircraft.track_deg is None:
        return []

    ground_speed_kt = aircraft.ground_speed_kt
    track_deg = aircraft.track_deg
    vertical_rate_fpm = aircraft.vertical_rate_fpm
    if use_history_fit and history:
        fitted = _fit_motion(
            aircraft,
            history,
            window_seconds=fit_window_seconds,
            min_points=fit_min_points,
        )
        if fitted is not None:
            ground_speed_kt, track_deg, vertical_rate_fpm = fitted

    points: list[PredictedPoint] = []
    step = max(1, step_seconds)
    for dt_seconds in range(0, horizon_seconds + 1, step):
        dt_hours = dt_seconds / 3600.0
        distance_km = ground_speed_kt * 1.852 * dt_hours
        lat, lon = destination_point(aircraft.lat, aircraft.lon, track_deg, distance_km)
        altitude_ft = aircraft.altitude_ft
        if altitude_ft is not None and vertical_rate_fpm is not None:
            altitude_ft = max(0.0, altitude_ft + vertical_rate_fpm * (dt_seconds / 60.0))
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


def _fit_motion(
    aircraft: AircraftState,
    history: Sequence[AircraftState],
    *,
    window_seconds: int,
    min_points: int,
) -> tuple[float, float, float | None] | None:
    cutoff = aircraft.timestamp - timedelta(seconds=max(30, window_seconds))
    points = [point for point in history if cutoff <= point.timestamp <= aircraft.timestamp]
    if len(points) < max(3, min_points):
        return None

    times = [(point.timestamp - aircraft.timestamp).total_seconds() for point in points]
    if max(times) - min(times) < 30:
        return None
    lat_slope = _linear_slope(times, [point.lat for point in points])
    lon_slope = _linear_slope(times, [point.lon for point in points])
    if lat_slope is None or lon_slope is None:
        return None

    km_per_degree = 111.32
    north_km_s = lat_slope * km_per_degree
    east_km_s = lon_slope * km_per_degree * max(0.05, math.cos(math.radians(aircraft.lat)))
    speed_km_s = math.hypot(north_km_s, east_km_s)
    ground_speed_kt = speed_km_s * 3600.0 / 1.852
    if not 50 <= ground_speed_kt <= 750:
        return None
    track_deg = math.degrees(math.atan2(east_km_s, north_km_s)) % 360.0

    altitude_points = [(t, point.altitude_ft) for t, point in zip(times, points) if point.altitude_ft is not None]
    vertical_rate_fpm = aircraft.vertical_rate_fpm
    if len(altitude_points) >= max(3, min_points):
        altitude_slope = _linear_slope(
            [item[0] for item in altitude_points],
            [float(item[1]) for item in altitude_points],
        )
        if altitude_slope is not None:
            fitted_vertical_rate = altitude_slope * 60.0
            if abs(fitted_vertical_rate) <= 3000:
                vertical_rate_fpm = fitted_vertical_rate
    return ground_speed_kt, track_deg, vertical_rate_fpm


def _linear_slope(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    variance = sum((value - mean_x) ** 2 for value in xs)
    if variance <= 1e-9:
        return None
    return sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / variance
