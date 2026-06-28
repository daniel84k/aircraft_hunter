from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo

from geo import haversine_distance_km
from models import TransitCandidate


def rounded_transit_time(dt: datetime, window_seconds: int = 10) -> datetime:
    utc = dt.astimezone(timezone.utc)
    rounded = int(round(utc.timestamp() / window_seconds) * window_seconds)
    return datetime.fromtimestamp(rounded, tz=timezone.utc)


def dedupe_key(icao: str, body: str, transit_time_utc: datetime, observer_lat: float, observer_lon: float) -> str:
    rounded_time = rounded_transit_time(transit_time_utc).isoformat()
    lat = round(observer_lat, 3)
    lon = round(observer_lon, 3)
    return f"{icao.lower()}:{body.lower()}:{rounded_time}:{lat:.3f}:{lon:.3f}"


def should_repeat_alert(previous: TransitCandidate, current: TransitCandidate) -> bool:
    if current.score >= previous.score + 0.10:
        return True
    moved_km = haversine_distance_km(
        previous.observer_lat,
        previous.observer_lon,
        current.observer_lat,
        current.observer_lon,
    )
    return moved_km > 0.5


def flightradar_url(candidate: TransitCandidate) -> str:
    flight_id = (candidate.aircraft.callsign or candidate.aircraft.icao).strip()
    return f"https://www.flightradar24.com/{quote(flight_id)}"


def format_alert(candidate: TransitCandidate, *, better: bool = False, phase: str | None = None) -> str:
    aircraft = candidate.aircraft
    warsaw_time = candidate.transit_time_utc.astimezone(ZoneInfo("Europe/Warsaw")).strftime("%Y-%m-%d %H:%M:%S %Z")
    seconds = max(0, int((candidate.transit_time_utc - datetime.now(timezone.utc)).total_seconds()))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if better:
        title = "BETTER TRANSIT ALERT"
    elif phase == "EARLY":
        title = "EARLY TRANSIT FORECAST"
    elif phase == "CONFIRMED":
        title = "CONFIRMED TRANSIT ALERT"
    elif phase == "LAST_CHANCE":
        title = "LAST CHANCE OBSERVATION ALERT"
    else:
        title = "TRANSIT ALERT" if candidate.status == "ALERT_READY" else "OBSERVATION CANDIDATE"
    if phase == "LAST_CHANCE":
        guidance = "Last chance   : quick look now; use current/nearby spot if travel is not realistic"
    elif phase == "EARLY":
        guidance = "Observation   : early heads-up; go out and watch the object area"
    elif phase == "CONFIRMED":
        guidance = "Observation   : confirmed geometry; use navigation if travel time allows"
    else:
        guidance = "Observation   : watch the object area"
    return f"""===
{title}
Object        : {candidate.body}
Aircraft      : {aircraft.callsign or '-'} / {aircraft.aircraft_type or '-'} / icao {aircraft.icao}
Transit Warsaw: {warsaw_time}
Time to go    : {h:02d}:{m:02d}:{s:02d}
{guidance}

Go to:
{candidate.observer_lat:.6f}, {candidate.observer_lon:.6f}
Distance      : {candidate.observer_distance_km:.1f} km

Google Maps   : {candidate.google_maps_url}
Navigation    : {candidate.google_nav_url}
Flightradar24 : {flightradar_url(candidate)}

Offset        : {candidate.offset_body_diameters:.2f} {candidate.body} diameters
Score         : {candidate.score:.2f}
Confidence    : {candidate.confidence:.2f}

Aircraft alt  : {candidate.aircraft_altitude_ft or 0:.0f} ft
Aircraft range: {candidate.aircraft_range_km or 0:.1f} km
Body elevation: {candidate.body_elevation_deg:.1f} deg
==="""
