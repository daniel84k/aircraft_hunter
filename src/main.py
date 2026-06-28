from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import math
import time

from adsb_client import ADSBClient
from airport_filter import classify_airport_traffic, parse_airport_filter_profiles
from alerts import dedupe_key, format_alert
from config import Settings, load_settings
from data_retention import retention_due, run_data_retention
from db import connect, run_migrations
from ephemeris import get_body_states, get_body_states_many
from geo import haversine_distance_km
from logging_config import configure_logging
from models import AircraftState, TransitCandidate
from observer_solver import google_maps_url, google_nav_url, solve_observer_point
from prediction import predict_aircraft_path
from scoring import final_score
from stability import has_stable_vertical_trend, rejection_reason_for_unstable, stability_score
from storage import Storage, serialize_prediction_path
from telegram import TelegramNotifier
from transit_detector import closest_alignment, detect_transit_candidates
from ui import start_ui_server
from validation import format_validation_message, validate_actual_transit


LOG = logging.getLogger(__name__)
STEPS_TOTAL = 5
COARSE_GEOMETRY_STEP_SECONDS = 60
MAX_RAW_CANDIDATES_TO_SOLVE = 20
REJECTION_REASONS = {
    "LOW_SCORE",
    "TOO_LATE",
    "OBSERVER_POINT_TOO_FAR",
    "UNSTABLE_TRACK",
    "HIGH_VERTICAL_RATE",
    "LOW_ALTITUDE",
    "BODY_TOO_LOW",
    "OFFSET_TOO_LARGE",
    "TOO_EARLY_FOR_ALERT",
    "NEAR_ORIGIN_AIRPORT",
    "NEAR_DESTINATION_AIRPORT",
    "NEAR_EPWA_APPROACH",
    "NEAR_EPWA_DEPARTURE",
    "NEAR_EPMO_APPROACH",
    "NEAR_EPMO_DEPARTURE",
    "DUPLICATE_ALERT",
    "INSUFFICIENT_DATA",
}


@dataclass
class CandidateConvergence:
    consecutive_cycles: int
    last_seen_at: datetime
    transit_time_utc: datetime
    observer_lat: float
    observer_lon: float
    offset_body_diameters: float


def update_candidate_convergence(
    candidate: TransitCandidate,
    tracker: dict[tuple[str, str, int], CandidateConvergence],
    settings: Settings,
    now: datetime,
) -> tuple[bool, int, str]:
    key = notification_event_key(candidate, settings.locked_alert_window_seconds)
    previous = tracker.get(key)
    reason = "FIRST_OBSERVATION"
    consecutive = 1
    if previous is not None:
        gap_seconds = (now - previous.last_seen_at).total_seconds()
        time_shift = abs((candidate.transit_time_utc - previous.transit_time_utc).total_seconds())
        observer_shift = haversine_distance_km(
            previous.observer_lat,
            previous.observer_lon,
            candidate.observer_lat,
            candidate.observer_lon,
        )
        offset_worsening = candidate.offset_body_diameters - previous.offset_body_diameters
        if gap_seconds > max(30, settings.poll_interval_seconds * 3):
            reason = "CYCLE_GAP"
        elif time_shift > settings.notification_max_time_shift_seconds:
            reason = "TRANSIT_TIME_MOVED"
        elif observer_shift > settings.notification_max_observer_shift_km:
            reason = "OBSERVER_POINT_MOVED"
        elif offset_worsening > settings.notification_max_offset_worsening_diameters:
            reason = "OFFSET_WORSENED"
        else:
            consecutive = previous.consecutive_cycles + 1
            reason = "CONVERGED"
    tracker[key] = CandidateConvergence(
        consecutive_cycles=consecutive,
        last_seen_at=now,
        transit_time_utc=candidate.transit_time_utc,
        observer_lat=candidate.observer_lat,
        observer_lon=candidate.observer_lon,
        offset_body_diameters=candidate.offset_body_diameters,
    )
    required = max(1, settings.notification_consecutive_cycles)
    return consecutive >= required, consecutive, reason


def prune_candidate_convergence(
    tracker: dict[tuple[str, str, int], CandidateConvergence],
    now: datetime,
    max_age_seconds: int,
) -> None:
    stale = [key for key, value in tracker.items() if (now - value.last_seen_at).total_seconds() > max_age_seconds]
    for key in stale:
        tracker.pop(key, None)


def reachable_relocation_km(settings: Settings, lead_time_seconds: int) -> float:
    travel_hours = max(0, lead_time_seconds) / 3600.0
    reachable = settings.travel_speed_kmh * travel_hours * max(0.1, min(1.0, settings.reach_safety))
    return min(settings.max_observer_relocation_km, reachable)


def cycle_log(cycle_id: int, step: int, label: str, message: str, *args) -> None:
    LOG.info("cycle=%s step=%s/%s %s | " + message, cycle_id, step, STEPS_TOTAL, label, *args)


def notification_event_key(candidate: TransitCandidate, event_window_seconds: int = 600) -> tuple[str, str, int]:
    event_slot = int(candidate.transit_time_utc.timestamp()) // max(60, event_window_seconds)
    return (candidate.aircraft.icao.lower(), candidate.body.lower(), event_slot)


def notification_same_event(
    first: TransitCandidate,
    second: TransitCandidate,
    event_window_seconds: int = 600,
) -> bool:
    if first.aircraft.icao.lower() != second.aircraft.icao.lower():
        return False
    if first.body.lower() != second.body.lower():
        return False
    half_window_seconds = max(60, int(event_window_seconds)) / 2
    delta_seconds = abs((first.transit_time_utc - second.transit_time_utc).total_seconds())
    return delta_seconds <= half_window_seconds


def notification_event_seen(
    candidate: TransitCandidate,
    seen_candidates: list[TransitCandidate],
    event_window_seconds: int = 600,
) -> bool:
    return any(
        notification_same_event(candidate, seen_candidate, event_window_seconds)
        for seen_candidate in seen_candidates
    )


def notification_sort_key(candidate: TransitCandidate) -> tuple[int, float, float, float]:
    status_rank = 0 if candidate.status == "ALERT_READY" else 1
    return (status_rank, candidate.observer_distance_km, candidate.angular_separation_deg, -candidate.score)


def candidate_notification_phase(
    candidate: TransitCandidate,
    convergence_count: int,
    settings: Settings,
    *,
    convergence_enabled: bool = True,
) -> str | None:
    if candidate.status not in {"ALERT_READY", "OBSERVATION_CANDIDATE"}:
        return None
    if not convergence_enabled:
        convergence_count = max(
            settings.early_notification_consecutive_cycles,
            settings.notification_consecutive_cycles,
        )
    if (
        candidate.status == "ALERT_READY"
        and convergence_count >= max(1, settings.notification_consecutive_cycles)
    ):
        return "CONFIRMED"
    early_quality = (
        candidate.score >= settings.alert_min_score
        and candidate.offset_body_diameters <= settings.max_offset_body_diameters_for_alert
    )
    if early_quality and convergence_count >= max(1, settings.early_notification_consecutive_cycles):
        return "EARLY"
    return None


def is_better_notification(
    candidate: TransitCandidate,
    previous_event: dict[str, float] | None,
    *,
    min_distance_improvement_km: float,
    min_offset_improvement_ratio: float,
) -> bool:
    if not previous_event:
        return False
    distance_improvement = previous_event["best_distance_km"] - candidate.observer_distance_km
    if distance_improvement >= min_distance_improvement_km:
        return True
    previous_offset = previous_event["best_offset_body_diameters"]
    if previous_offset <= 0:
        return False
    offset_improvement = previous_offset - candidate.offset_body_diameters
    return offset_improvement / previous_offset >= min_offset_improvement_ratio


def classify_candidate(candidate: TransitCandidate, settings: Settings, stable: bool) -> TransitCandidate:
    lead_time = int((candidate.transit_time_utc - datetime.now(timezone.utc)).total_seconds())
    reachable_km = reachable_relocation_km(settings, lead_time)
    practical = (
        stable
        and lead_time >= 0
        and lead_time <= settings.prediction_horizon_seconds
        and lead_time <= settings.observation_candidate_max_lead_seconds
        and candidate.observer_distance_km <= reachable_km
        and candidate.angular_separation_deg <= settings.observation_candidate_max_separation_deg
        and candidate.body_elevation_deg >= settings.min_body_elevation_deg_for_candidate
        and candidate.score >= settings.observation_candidate_min_score
    )
    reason = None
    if not stable:
        reason = "UNSTABLE_TRACK"
    elif lead_time < 0:
        reason = "TOO_LATE"
    elif lead_time > settings.prediction_horizon_seconds:
        reason = "TOO_LATE"
    elif candidate.score < settings.alert_min_score:
        reason = "LOW_SCORE"
    elif candidate.observer_distance_km > reachable_km:
        reason = "OBSERVER_POINT_TOO_FAR"
    elif candidate.offset_body_diameters > settings.max_offset_body_diameters_for_alert:
        reason = "OFFSET_TOO_LARGE"
    elif candidate.body_elevation_deg < settings.min_body_elevation_deg:
        reason = "BODY_TOO_LOW"
    elif lead_time > settings.alert_ready_lead_time_seconds:
        reason = "TOO_EARLY_FOR_ALERT"

    if reason is None:
        candidate.status = "ALERT_READY"
        candidate.rejection_reason = None
    elif practical:
        candidate.status = "OBSERVATION_CANDIDATE"
        candidate.rejection_reason = reason
    elif candidate.score >= 0.65 and reason == "LOW_SCORE":
        candidate.status = "CANDIDATE_STORED"
        candidate.rejection_reason = reason
    else:
        candidate.status = "REJECTED"
        candidate.rejection_reason = reason
    return candidate


def airport_rejection_reason(match) -> str:
    phase_name = "APPROACH" if match.phase == "APP" else "DEPARTURE"
    return f"NEAR_{match.airport_code}_{phase_name}"


def suppress_airport_traffic_alert(candidate: TransitCandidate, airport_match) -> TransitCandidate:
    if airport_match is None or airport_match.mode != "strict":
        return candidate
    if candidate.status not in {"ALERT_READY", "OBSERVATION_CANDIDATE"}:
        return candidate
    candidate.status = "CANDIDATE_STORED"
    candidate.rejection_reason = airport_rejection_reason(airport_match)
    return candidate


def build_candidate(raw, settings: Settings, stability: float) -> TransitCandidate:
    aircraft, point, body, separation, offset = raw
    lead_time = int((point.timestamp - aircraft.timestamp).total_seconds())
    max_relocation_km = reachable_relocation_km(settings, lead_time)
    solution = solve_observer_point(
        settings.user_lat,
        settings.user_lon,
        point,
        body,
        max_relocation_km,
    )
    score = final_score(
        offset_body_diameters=solution.offset_body_diameters,
        stability=stability,
        altitude_ft=point.altitude_ft,
        body_elevation_deg=body.elevation_deg,
        aircraft_range_km=point.range_km,
        lead_time_seconds=lead_time,
        observer_distance_km=solution.distance_km,
        solver_confidence=solution.confidence,
    )
    key = dedupe_key(aircraft.icao, body.body, point.timestamp, solution.lat, solution.lon)
    return TransitCandidate(
        aircraft=aircraft,
        body=body.body,
        transit_time_utc=point.timestamp,
        observer_lat=solution.lat,
        observer_lon=solution.lon,
        observer_distance_km=solution.distance_km,
        google_maps_url=google_maps_url(solution.lat, solution.lon),
        google_nav_url=google_nav_url(solution.lat, solution.lon),
        angular_separation_deg=solution.angular_separation_deg,
        body_radius_deg=body.angular_radius_deg,
        offset_body_diameters=solution.offset_body_diameters,
        score=score.score,
        confidence=score.confidence,
        aircraft_range_km=point.range_km,
        aircraft_altitude_ft=point.altitude_ft,
        body_elevation_deg=body.elevation_deg,
        status="CANDIDATE_STORED",
        rejection_reason=solution.reason,
        dedupe_key=key,
        stability_score=score.stability_score,
        alignment_score=score.alignment_score,
        altitude_score=score.altitude_score,
        body_elevation_score=score.body_elevation_score,
        aircraft_range_score=score.aircraft_range_score,
        lead_time_score=score.lead_time_score,
        observer_distance_score=score.observer_distance_score,
        aircraft_track_deg=aircraft.track_deg,
        aircraft_ground_speed_kt=aircraft.ground_speed_kt,
        aircraft_vertical_rate_fpm=aircraft.vertical_rate_fpm,
        body_azimuth_deg=body.azimuth_deg,
        observer_home_offset_body_diameters=solution.home_offset_body_diameters,
        observer_best_grid_offset_body_diameters=solution.best_grid_offset_body_diameters,
        observer_grid_points_checked=solution.grid_points_checked,
        observer_selected_from_home=solution.selected_from_home,
    )


def prune_history(history: deque[AircraftState], now: datetime, keep_seconds: int = 120) -> None:
    while history and (now - history[0].timestamp).total_seconds() > keep_seconds:
        history.popleft()


def remaining_cycle_delay(cycle_started_monotonic: float, interval_seconds: float, now_monotonic: float) -> float:
    """Return only the unused part of the configured start-to-start interval."""
    elapsed = max(0.0, now_monotonic - cycle_started_monotonic)
    return max(0.0, float(interval_seconds) - elapsed)


def _coarse_sampled_path(settings: Settings, path):
    sample_step = max(1, COARSE_GEOMETRY_STEP_SECONDS // max(1, settings.prediction_step_seconds))
    sampled = path[::sample_step]
    if path[-1] not in sampled:
        sampled.append(path[-1])
    return sampled


def populate_ephemeris_cache(settings: Settings, ephemeris_cache: dict, timestamps) -> None:
    """Populate exact requested timestamps in one batch, with scalar fallback."""
    missing = list(dict.fromkeys(timestamp for timestamp in timestamps if timestamp not in ephemeris_cache))
    if not missing:
        return
    ephemeris_cache.update(
        get_body_states_many(settings.user_lat, settings.user_lon, missing)
    )
    # Preserve the previous failure semantics and calculation path if a batch
    # cannot produce one or more timestamps.
    for timestamp in missing:
        if timestamp not in ephemeris_cache:
            ephemeris_cache[timestamp] = get_body_states(
                settings.user_lat,
                settings.user_lon,
                timestamp,
            )


def coarse_geometry_check(settings: Settings, path, ephemeris_cache: dict):
    sampled = _coarse_sampled_path(settings, path)

    bodies_by_time = {}
    populate_ephemeris_cache(settings, ephemeris_cache, (point.timestamp for point in sampled))
    for point in sampled:
        bodies_by_time[point.timestamp] = ephemeris_cache[point.timestamp]

    closest = closest_alignment(sampled, bodies_by_time)
    if not closest:
        return False, None, 0.0

    _body_name, separation, _offset, point, _body = closest
    range_km = point.range_km if point else min(p.range_km for p in path)
    relocation_allowance_deg = math.degrees(math.atan2(settings.max_observer_relocation_km, max(range_km, 1.0)))
    allowed_separation_deg = max(5.0, relocation_allowance_deg + 1.0)
    return separation <= allowed_separation_deg, closest, allowed_separation_deg


def send_validation_notifications(storage: Storage, notifier: TelegramNotifier | None) -> None:
    for validation_id, message in storage.unsent_validations():
        sent = True
        if notifier and notifier.enabled:
            sent = notifier.send_message(message)
        else:
            print(message, flush=True)
        if not sent:
            continue
        notified_at = datetime.now(timezone.utc)
        storage.mark_validation_notified(validation_id, notified_at)
        LOG.info("TRANSIT_VALIDATION_SENT | validation_id=%s", validation_id)


def process_due_transit_validations(
    settings: Settings,
    storage: Storage,
    notifier: TelegramNotifier | None,
    now: datetime,
) -> None:
    if not settings.transit_validation_enabled:
        return
    events = storage.pending_validation_events(
        now=now,
        delay_seconds=settings.transit_validation_delay_seconds,
        event_window_seconds=settings.locked_alert_window_seconds,
    )
    for event in events:
        observations = storage.observations_around(
            icao=event["icao"],
            timestamp=event["transit_time_utc"],
            window_seconds=settings.transit_validation_observation_window_seconds,
        )
        result = validate_actual_transit(
            observations,
            body_name=event["body"],
            observer_lat=event["observer_lat"],
            observer_lon=event["observer_lon"],
            hit_uncertainty_diameters=settings.transit_validation_uncertainty_diameters,
        )
        event["observation_count"] = len(observations)
        event_age_seconds = (now - event["transit_time_utc"]).total_seconds()
        if result is None and event_age_seconds < settings.transit_validation_max_wait_seconds:
            continue
        message = format_validation_message(event, result)
        if not storage.insert_validation(event, result, message, now):
            continue
        LOG.info(
            "TRANSIT_VALIDATED | aircraft=%s callsign=%s body=%s result=%s predicted_time_utc=%s "
            "actual_time_utc=%s actual_offset_diameters=%s vertical_offset_diameters=%s "
            "horizontal_offset_diameters=%s",
            event["icao"],
            event.get("callsign") or "-",
            event["body"],
            result.result if result else "NO_DATA",
            event["transit_time_utc"].isoformat(),
            result.actual_closest_time_utc.isoformat() if result else "-",
            f"{result.actual_offset_body_diameters:.3f}" if result else "-",
            f"{result.vertical_offset_body_diameters:.3f}" if result else "-",
            f"{result.horizontal_offset_body_diameters:.3f}" if result else "-",
        )
    send_validation_notifications(storage, notifier)


def run_cycle(
    settings: Settings,
    client: ADSBClient,
    storage: Storage,
    histories,
    notifier: TelegramNotifier | None = None,
    convergence_tracker: dict[tuple[str, str, int], CandidateConvergence] | None = None,
) -> None:
    cycle_started = datetime.now(timezone.utc)
    cycle_id = int(cycle_started.timestamp())
    if convergence_tracker is not None:
        prune_candidate_convergence(
            convergence_tracker,
            cycle_started,
            max_age_seconds=max(600, settings.prediction_horizon_seconds * 2),
        )
    airport_profiles = parse_airport_filter_profiles(settings.airport_traffic_filters)
    current_bodies = get_body_states(settings.user_lat, settings.user_lon, cycle_started)
    visible_bodies = [
        body for body in current_bodies if body.elevation_deg >= settings.standby_body_elevation_deg
    ]
    if not visible_bodies:
        body_summary = ", ".join(
            f"{body.body}={body.elevation_deg:.1f}deg" for body in current_bodies
        ) or "no_ephemeris"
        cycle_log(
            cycle_id,
            1,
            "STANDBY",
            "reason=ALL_BODIES_BELOW_ELEVATION threshold_deg=%.1f bodies=%s",
            settings.standby_body_elevation_deg,
            body_summary,
        )
        return

    aircraft = client.fetch_aircraft(settings.user_lat, settings.user_lon, settings.search_radius_nm)
    cycle_log(
        cycle_id,
        1,
        "ADSB_FETCH",
        "fetched_aircraft=%s search_radius_nm=%.1f observer_lat=%.6f observer_lon=%.6f",
        len(aircraft),
        settings.search_radius_nm,
        settings.user_lat,
        settings.user_lon,
    )
    storage.insert_observations(aircraft)
    cycle_log(cycle_id, 1, "DB_STORE_OBSERVATIONS", "stored_aircraft_observations=%s", len(aircraft))
    process_due_transit_validations(settings, storage, notifier, datetime.now(timezone.utc))
    for ac in aircraft:
        histories[ac.icao].append(ac)
        prune_history(histories[ac.icao], ac.timestamp)

    run_id = storage.start_prediction_run(cycle_started, settings, len(aircraft))
    analyzed = 0
    alert_count = 0
    saved_count = 0
    all_candidates: list[TransitCandidate] = []
    radar_event_ids_by_candidate: dict[int, int] = {}
    ephemeris_cache = {}
    geometry_inputs = []
    airport_matches = {}
    trajectory_payloads: dict[str, dict] = {}

    try:
        for ac in aircraft:
            history = list(histories[ac.icao])
            stable_vertical_trend = settings.allow_stable_vertical_trend and has_stable_vertical_trend(
                history,
                level_rate_fpm=settings.max_vertical_rate_stable_fpm,
                max_rate_fpm=settings.max_stable_vertical_rate_fpm,
                max_variation_fpm=settings.max_vertical_rate_variation_fpm,
                min_points=settings.stable_vertical_trend_min_points,
            )
            stable_score = stability_score(
                ac,
                history,
                vertical_rate_stable_fpm=settings.max_vertical_rate_stable_fpm,
                stable_vertical_trend=stable_vertical_trend,
            )
            if settings.run_mode == "debug":
                cycle_log(
                    cycle_id,
                    2,
                    "FILTER_INPUT",
                    "aircraft=%s callsign=%s type=%s lat=%.5f lon=%.5f altitude_ft=%s "
                    "gs_kt=%s track_deg=%s vertical_rate_fpm=%s stability=%.2f history_points=%s",
                    ac.icao,
                    ac.callsign or "-",
                    ac.aircraft_type or "-",
                    ac.lat,
                    ac.lon,
                    f"{ac.altitude_ft:.0f}" if ac.altitude_ft is not None else "-",
                    f"{ac.ground_speed_kt:.0f}" if ac.ground_speed_kt is not None else "-",
                    f"{ac.track_deg:.0f}" if ac.track_deg is not None else "-",
                    f"{ac.vertical_rate_fpm:.0f}" if ac.vertical_rate_fpm is not None else "-",
                    stable_score,
                    len(history),
                )
            reject = rejection_reason_for_unstable(
                ac,
                history,
                vertical_rate_stable_fpm=settings.max_vertical_rate_stable_fpm,
                stable_vertical_trend=stable_vertical_trend,
            )
            if stable_vertical_trend and settings.run_mode == "debug":
                cycle_log(
                    cycle_id,
                    2,
                    "VERTICAL_TREND_ACCEPTED",
                    "aircraft=%s callsign=%s vertical_rate_fpm=%s history_points=%s",
                    ac.icao,
                    ac.callsign or "-",
                    f"{ac.vertical_rate_fpm:.0f}" if ac.vertical_rate_fpm is not None else "-",
                    len(history),
                )
            airport_match = classify_airport_traffic(ac, airport_profiles)
            if airport_match:
                airport_reason = airport_rejection_reason(airport_match)
                airport_matches[ac.icao] = airport_match
                if airport_match.mode == "strict":
                    cycle_log(
                        cycle_id,
                        2,
                        "AIRPORT_STRICT_FLAG",
                        "aircraft=%s callsign=%s reason=%s airport=%s phase=%s distance_nm=%.1f track_delta_deg=%.0f mode=%s action=suppress_alert_only",
                        ac.icao,
                        ac.callsign or "-",
                        airport_reason,
                        airport_match.airport_code,
                        airport_match.phase,
                        airport_match.distance_nm,
                        airport_match.track_delta_deg,
                        airport_match.mode,
                    )
                if settings.run_mode == "debug":
                    cycle_log(
                        cycle_id,
                        2,
                        "AIRPORT_SOFT_FLAG",
                        "aircraft=%s callsign=%s reason=%s airport=%s phase=%s distance_nm=%.1f track_delta_deg=%.0f mode=%s",
                        ac.icao,
                        ac.callsign or "-",
                        airport_reason,
                        airport_match.airport_code,
                        airport_match.phase,
                        airport_match.distance_nm,
                        airport_match.track_delta_deg,
                        airport_match.mode,
                    )
            if reject and stable_score < 0.65:
                cycle_log(
                    cycle_id,
                    2,
                    "FILTER_REJECTED",
                    "aircraft=%s callsign=%s reason=%s stability=%.2f",
                    ac.icao,
                    ac.callsign or "-",
                    reject,
                    stable_score,
                )
                continue
            if ac.altitude_ft is not None and ac.altitude_ft < settings.min_altitude_ft:
                cycle_log(
                    cycle_id,
                    2,
                    "FILTER_REJECTED",
                    "aircraft=%s callsign=%s reason=LOW_ALTITUDE altitude_ft=%.0f min_altitude_ft=%.0f stability=%.2f",
                    ac.icao,
                    ac.callsign or "-",
                    ac.altitude_ft,
                    settings.min_altitude_ft,
                    stable_score,
                )
                continue
            if stable_score < settings.min_stability_score_for_geometry:
                reason = reject or "INSUFFICIENT_DATA"
                cycle_log(
                    cycle_id,
                    2,
                    "FILTER_REJECTED",
                    "aircraft=%s callsign=%s reason=%s stability=%.2f min_stability=%.2f",
                    ac.icao,
                    ac.callsign or "-",
                    reason,
                    stable_score,
                    settings.min_stability_score_for_geometry,
                )
                continue
            path = predict_aircraft_path(
                ac,
                settings.user_lat,
                settings.user_lon,
                settings.prediction_horizon_seconds,
                settings.prediction_step_seconds,
                history=history,
                use_history_fit=settings.prediction_use_history_fit,
                fit_window_seconds=settings.prediction_fit_window_seconds,
                fit_min_points=settings.prediction_fit_min_points,
            )
            if not path:
                cycle_log(
                    cycle_id,
                    2,
                    "FILTER_REJECTED",
                    "aircraft=%s callsign=%s reason=NO_PREDICTION_PATH",
                    ac.icao,
                    ac.callsign or "-",
                )
                continue
            trajectory_payloads[ac.icao.lower()] = serialize_prediction_path(path)

            max_aircraft_elevation = max(p.elevation_deg for p in path)
            min_aircraft_range = min(p.range_km for p in path)
            if max_aircraft_elevation < settings.min_aircraft_elevation_deg_for_geometry:
                if settings.run_mode == "debug":
                    cycle_log(
                        cycle_id,
                        2,
                        "VISIBILITY_SKIPPED",
                        "aircraft=%s callsign=%s reason=AIRCRAFT_TOO_LOW_IN_SKY "
                        "max_aircraft_elevation_deg=%.1f threshold_deg=%.1f min_aircraft_range_km=%.1f",
                        ac.icao,
                        ac.callsign or "-",
                        max_aircraft_elevation,
                        settings.min_aircraft_elevation_deg_for_geometry,
                        min_aircraft_range,
                    )
                continue
            if min_aircraft_range > settings.max_aircraft_range_km_for_geometry:
                if settings.run_mode == "debug":
                    cycle_log(
                        cycle_id,
                        2,
                        "VISIBILITY_SKIPPED",
                        "aircraft=%s callsign=%s reason=AIRCRAFT_TOO_FAR "
                        "min_aircraft_range_km=%.1f threshold_km=%.1f max_aircraft_elevation_deg=%.1f",
                        ac.icao,
                        ac.callsign or "-",
                        min_aircraft_range,
                        settings.max_aircraft_range_km_for_geometry,
                        max_aircraft_elevation,
                    )
                continue

            geometry_inputs.append((ac, stable_score, path, min_aircraft_range, max_aircraft_elevation))

        coarse_timestamps = (
            point.timestamp
            for _ac, _stable_score, path, _min_range, _max_elevation in geometry_inputs
            for point in _coarse_sampled_path(settings, path)
        )
        populate_ephemeris_cache(settings, ephemeris_cache, coarse_timestamps)

        selected_geometry_inputs = []
        for ac, stable_score, path, min_aircraft_range, max_aircraft_elevation in geometry_inputs:
            coarse_ok, coarse_closest, allowed_separation_deg = coarse_geometry_check(settings, path, ephemeris_cache)
            if not coarse_ok:
                if settings.run_mode == "debug" and coarse_closest:
                    body_name, separation, offset, point, body = coarse_closest
                    cycle_log(
                        cycle_id,
                        3,
                        "GEOMETRY_SKIPPED",
                        "aircraft=%s callsign=%s reason=BODY_ON_OTHER_SIDE closest_body=%s closest_time_utc=%s "
                        "closest_separation_deg=%.1f allowed_separation_deg=%.1f closest_offset_diameters=%.1f "
                        "body_azimuth_deg=%.1f body_elevation_deg=%.1f aircraft_azimuth_deg=%.1f aircraft_elevation_deg=%.1f",
                        ac.icao,
                        ac.callsign or "-",
                        body_name,
                        point.timestamp.isoformat() if point else "-",
                        separation,
                        allowed_separation_deg,
                        offset,
                        body.azimuth_deg if body else 0.0,
                        body.elevation_deg if body else 0.0,
                        point.azimuth_deg if point else 0.0,
                        point.elevation_deg if point else 0.0,
                    )
                continue

            analyzed += 1
            if settings.run_mode == "debug":
                cycle_log(
                    cycle_id,
                    3,
                    "GEOMETRY_SELECTED",
                    "aircraft=%s callsign=%s type=%s track_deg=%s "
                    "altitude_ft=%s stability=%.2f min_aircraft_range_km=%.1f max_aircraft_elevation_deg=%.1f",
                    ac.icao,
                    ac.callsign or "-",
                    ac.aircraft_type or "-",
                    f"{ac.track_deg:.0f}" if ac.track_deg is not None else "-",
                    f"{ac.altitude_ft:.0f}" if ac.altitude_ft is not None else "-",
                    stable_score,
                    min_aircraft_range,
                    max_aircraft_elevation,
                )
            selected_geometry_inputs.append(
                (ac, stable_score, path, min_aircraft_range, max_aircraft_elevation)
            )

        full_timestamps = (
            point.timestamp
            for _ac, _stable_score, path, _min_range, _max_elevation in selected_geometry_inputs
            for point in path
        )
        populate_ephemeris_cache(settings, ephemeris_cache, full_timestamps)

        for ac, stable_score, path, min_aircraft_range, max_aircraft_elevation in selected_geometry_inputs:
            bodies_by_time = {}
            for p in path:
                bodies_by_time[p.timestamp] = ephemeris_cache[p.timestamp]
            raw_candidates = detect_transit_candidates(
                ac,
                path,
                bodies_by_time,
                max_observer_relocation_km=settings.max_observer_relocation_km,
            )
            if settings.run_mode == "debug" and not raw_candidates:
                closest = closest_alignment(path, bodies_by_time)
                if closest:
                    body_name, separation, offset, point, body = closest
                    cycle_log(
                        cycle_id,
                        3,
                        "GEOMETRY_NO_ALIGNMENT",
                        "aircraft=%s callsign=%s closest_body=%s closest_time_utc=%s "
                        "closest_separation_deg=%.3f closest_offset_diameters=%.2f body_azimuth_deg=%.1f "
                        "body_elevation_deg=%.1f aircraft_azimuth_deg=%.1f aircraft_elevation_deg=%.1f",
                        ac.icao,
                        ac.callsign or "-",
                        body_name,
                        point.timestamp.isoformat() if point else "-",
                        separation,
                        offset,
                        body.azimuth_deg if body else 0.0,
                        body.elevation_deg if body else 0.0,
                        point.azimuth_deg if point else 0.0,
                        point.elevation_deg if point else 0.0,
                    )
                else:
                    cycle_log(
                        cycle_id,
                        3,
                        "GEOMETRY_NO_ALIGNMENT",
                        "aircraft=%s callsign=%s reason=NO_BODY_STATES",
                        ac.icao,
                        ac.callsign or "-",
            )
            for raw in sorted(raw_candidates, key=lambda item: item[3])[:MAX_RAW_CANDIDATES_TO_SOLVE]:
                candidate = build_candidate(raw, settings, stable_score)
                candidate_lead_time = int((candidate.transit_time_utc - cycle_started).total_seconds())
                radar_event_id = storage.insert_radar_event(
                    run_id,
                    candidate,
                    reachable_now=(
                        candidate_lead_time >= 0
                        and candidate.observer_distance_km <= reachable_relocation_km(settings, candidate_lead_time)
                    ),
                )
                candidate = classify_candidate(candidate, settings, stable_score >= 0.65)
                candidate = suppress_airport_traffic_alert(candidate, airport_matches.get(ac.icao))
                if candidate.rejection_reason == "OBSERVER_POINT_TOO_FAR":
                    candidate.status = "REJECTED"
                radar_event_ids_by_candidate[id(candidate)] = radar_event_id
                all_candidates.append(candidate)
                if settings.run_mode == "debug":
                    cycle_log(
                        cycle_id,
                        4,
                        "CANDIDATE_SCORED",
                        "aircraft=%s callsign=%s body=%s status=%s reason=%s score=%.2f "
                        "offset_diameters=%.2f observer_distance_km=%.1f transit_time_utc=%s",
                        candidate.aircraft.icao,
                        candidate.aircraft.callsign or "-",
                        candidate.body,
                        candidate.status,
                        candidate.rejection_reason or "-",
                        candidate.score,
                        candidate.offset_body_diameters,
                        candidate.observer_distance_km,
                        candidate.transit_time_utc.isoformat(),
                    )

        all_candidates.sort(key=notification_sort_key)
        notified_candidates_this_cycle = 0
        notified_events_this_cycle: list[TransitCandidate] = []
        evaluated_events_this_cycle: list[TransitCandidate] = []
        for candidate in all_candidates[:50]:
            alert_type = "CONSOLE"
            is_better_alert = False
            eligible_for_notification = (
                settings.alert_notifications_enabled
                and candidate.status in {"ALERT_READY", "OBSERVATION_CANDIDATE"}
            )
            convergence_enabled = (
                settings.notification_require_convergence and convergence_tracker is not None
            )
            convergence_count = 0
            convergence_reason = "DISABLED"
            if eligible_for_notification and convergence_enabled:
                if notification_event_seen(
                    candidate,
                    evaluated_events_this_cycle,
                    settings.locked_alert_window_seconds,
                ):
                    convergence_reason = "SAME_CYCLE_DUPLICATE"
                else:
                    _confirmed_ready, convergence_count, convergence_reason = update_candidate_convergence(
                        candidate,
                        convergence_tracker,
                        settings,
                        cycle_started,
                    )
                    evaluated_events_this_cycle.append(candidate)
            notification_phase = candidate_notification_phase(
                candidate,
                convergence_count,
                settings,
                convergence_enabled=convergence_enabled,
            )
            notification_ready = notification_phase is not None
            if eligible_for_notification and notification_ready:
                if notification_phase == "EARLY":
                    duplicate_event = storage.alert_event_exists(
                        icao=candidate.aircraft.icao,
                        body=candidate.body,
                        transit_time_utc=candidate.transit_time_utc,
                        event_window_seconds=settings.locked_alert_window_seconds,
                        alert_type="EARLY",
                    )
                    alert_type = "EARLY"
                    if duplicate_event:
                        candidate.status = "REJECTED"
                        candidate.rejection_reason = "DUPLICATE_ALERT"
                else:
                    previous_event = storage.alert_event_summary(
                        icao=candidate.aircraft.icao,
                        body=candidate.body,
                        transit_time_utc=candidate.transit_time_utc,
                        event_window_seconds=settings.locked_alert_window_seconds,
                        confirmed_only=True,
                    )
                    duplicate_event = previous_event is not None
                    is_better_alert = is_better_notification(
                        candidate,
                        previous_event,
                        min_distance_improvement_km=settings.telegram_update_min_distance_improvement_km,
                        min_offset_improvement_ratio=settings.telegram_update_min_offset_improvement_ratio,
                    )
                    alert_type = "CONFIRMED"
                    if duplicate_event and is_better_alert:
                        alert_type = "BETTER"
                    elif duplicate_event or storage.alert_exists(f"confirmed:{candidate.dedupe_key}"):
                        candidate.status = "REJECTED"
                        candidate.rejection_reason = "DUPLICATE_ALERT"
            candidate_id = storage.insert_candidate(run_id, candidate)
            radar_event_id = radar_event_ids_by_candidate.get(id(candidate))
            if radar_event_id is not None:
                storage.link_radar_event_candidate(radar_event_id, candidate_id, candidate)
            saved_count += 1
            trajectory_payload = trajectory_payloads.get(candidate.aircraft.icao.lower())
            if trajectory_payload:
                snapshot_updated = storage.upsert_event_trajectory(
                    candidate_id,
                    candidate,
                    trajectory_payload,
                    event_window_seconds=settings.locked_alert_window_seconds,
                )
                if snapshot_updated and settings.run_mode == "debug":
                    cycle_log(
                        cycle_id,
                        4,
                        "TRAJECTORY_SNAPSHOT_STORED",
                        "candidate_id=%s aircraft=%s body=%s score=%.2f points=%s",
                        candidate_id,
                        candidate.aircraft.icao,
                        candidate.body,
                        candidate.score,
                        len(trajectory_payload["points"]),
                    )
            if settings.run_mode == "debug":
                cycle_log(
                    cycle_id,
                    4,
                    "DB_STORE_CANDIDATE",
                    "candidate_id=%s aircraft=%s body=%s status=%s reason=%s score=%.2f",
                    candidate_id,
                    candidate.aircraft.icao,
                    candidate.body,
                    candidate.status,
                    candidate.rejection_reason or "-",
                    candidate.score,
                )
                if eligible_for_notification and not notification_ready:
                    early_quality = (
                        candidate.score >= settings.alert_min_score
                        and candidate.offset_body_diameters <= settings.max_offset_body_diameters_for_alert
                    )
                    required_cycles = (
                        settings.early_notification_consecutive_cycles
                        if early_quality
                        else settings.notification_consecutive_cycles
                    )
                    cycle_log(
                        cycle_id,
                        5,
                        "NOTIFICATION_DEFERRED",
                        "aircraft=%s callsign=%s body=%s consecutive_cycles=%s required_cycles=%s reason=%s",
                        candidate.aircraft.icao,
                        candidate.aircraft.callsign or "-",
                        candidate.body,
                        convergence_count,
                        required_cycles,
                        convergence_reason,
                    )
            if not eligible_for_notification:
                continue
            if not notification_ready:
                continue
            if candidate.status in {"ALERT_READY", "OBSERVATION_CANDIDATE"}:
                if notification_event_seen(
                    candidate,
                    notified_events_this_cycle,
                    settings.locked_alert_window_seconds,
                ):
                    continue
                if notification_phase == "EARLY" and notified_candidates_this_cycle >= settings.telegram_max_candidates_per_cycle:
                    continue
                message = format_alert(candidate, better=is_better_alert, phase=notification_phase)
                notification_sent = True
                if notifier:
                    notification_sent = notifier.send_candidate(
                        candidate,
                        message,
                        settings.telegram_candidate_cooldown_seconds,
                        settings.telegram_update_cooldown_seconds,
                        settings.telegram_update_min_distance_improvement_km,
                        settings.telegram_update_min_offset_improvement_ratio,
                        settings.locked_alert_window_seconds,
                        notification_phase,
                    )
                if not notification_sent:
                    continue
                print(message, flush=True)
                notification_dedupe_key = f"{notification_phase.lower()}:{candidate.dedupe_key}"
                storage.insert_alert(
                    candidate_id,
                    message,
                    notification_dedupe_key,
                    datetime.now(timezone.utc),
                    alert_type,
                )
                alert_count += 1
                if notification_phase == "EARLY":
                    notified_candidates_this_cycle += 1
                notified_events_this_cycle.append(candidate)
                cycle_log(
                    cycle_id,
                    5,
                    "ALERT_SENT",
                    "candidate_id=%s aircraft=%s callsign=%s body=%s phase=%s score=%.2f observer_lat=%.6f observer_lon=%.6f",
                    candidate_id,
                    candidate.aircraft.icao,
                    candidate.aircraft.callsign or "-",
                    candidate.body,
                    notification_phase,
                    candidate.score,
                    candidate.observer_lat,
                    candidate.observer_lon,
                )

        best = all_candidates[0] if all_candidates else None
        cycle_log(
            cycle_id,
            5,
            "CYCLE_COMPLETE",
            "fetched=%s geometry_analyzed=%s saved_candidates=%s alerts=%s best=%s",
            len(aircraft),
            analyzed,
            saved_count,
            alert_count,
            f"{best.aircraft.icao}/{best.body}/score={best.score:.2f}" if best else "-",
        )
        if settings.run_mode == "debug":
            for candidate in all_candidates[:3]:
                print(
                    f"DEBUG candidate {candidate.aircraft.icao} {candidate.body} "
                    f"score={candidate.score:.2f} status={candidate.status} reason={candidate.rejection_reason}"
                )
    finally:
        storage.finish_prediction_run(run_id, datetime.now(timezone.utc), analyzed, saved_count, alert_count)


def main() -> None:
    settings = load_settings()
    configure_logging(
        settings.log_level,
        settings.log_to_file,
        settings.log_dir,
        retain_full_days=settings.log_retain_full_days,
        retain_compressed_days=settings.log_retain_compressed_days,
        emergency_free_mb=settings.log_emergency_free_mb,
    )
    conn = connect(settings.database_url)
    run_migrations(conn)
    storage = Storage(conn)
    client = ADSBClient(settings.adsbfi_base)
    notifier = TelegramNotifier() if settings.alert_notifications_enabled else None
    histories: dict[str, deque[AircraftState]] = defaultdict(lambda: deque(maxlen=240))
    convergence_tracker: dict[tuple[str, str, int], CandidateConvergence] = {}
    last_data_retention_monotonic: float | None = None
    if settings.ui_enabled:
        start_ui_server(settings)
    LOG.info("Aircraft Transit Hunter started mode=%s", settings.run_mode)
    if notifier and notifier.enabled:
        notifier.send_message("Aircraft Transit Hunter started")
        LOG.info("Telegram notifications enabled")
    elif not settings.alert_notifications_enabled:
        LOG.info("Telegram notifications disabled for this worker")

    while True:
        cycle_started_monotonic = time.monotonic()
        try:
            if retention_due(last_data_retention_monotonic, settings.data_retention_interval_seconds):
                try:
                    run_data_retention(conn, settings)
                except Exception as exc:
                    LOG.exception("Data retention failed error=%s", exc)
                finally:
                    last_data_retention_monotonic = time.monotonic()
            if settings.transit_validation_enabled and notifier:
                send_validation_notifications(storage, notifier)
            run_cycle(settings, client, storage, histories, notifier, convergence_tracker)
        except Exception as exc:
            LOG.exception("Cycle failed error=%s", exc)
        delay = remaining_cycle_delay(
            cycle_started_monotonic,
            settings.poll_interval_seconds,
            time.monotonic(),
        )
        if delay > 0:
            time.sleep(delay)
        else:
            LOG.warning(
                "Cycle exceeded polling interval interval_seconds=%s action=next_cycle_immediately",
                settings.poll_interval_seconds,
            )


if __name__ == "__main__":
    main()
