#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter, defaultdict, deque
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
import sys
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from airport_filter import classify_airport_traffic, parse_airport_filter_profiles
from config import Settings, load_settings
from db import connect
from ephemeris import get_body_states, get_body_states_many
from main import (
    MAX_RAW_CANDIDATES_TO_SOLVE,
    build_candidate,
    candidate_notification_phase,
    coarse_geometry_check,
    notification_event_seen,
    notification_sort_key,
    reachable_relocation_km,
    update_candidate_convergence,
)
from models import AircraftState, TransitCandidate
from prediction import predict_aircraft_path
from stability import has_stable_vertical_trend, rejection_reason_for_unstable, stability_score
from transit_detector import detect_transit_candidates


WARSAW = ZoneInfo("Europe/Warsaw")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay historical ADS-B observations through the current alert pipeline without writing to DB.",
    )
    parser.add_argument("--date", type=date.fromisoformat, required=True, help="Warsaw day, YYYY-MM-DD")
    parser.add_argument("--from", dest="from_time", help="Warsaw local time HH:MM, optional")
    parser.add_argument("--to", dest="to_time", help="Warsaw local time HH:MM, optional")
    parser.add_argument(
        "--cycle-step",
        type=int,
        default=1,
        help="Process every Nth reconstructed cycle. Use >1 for fast approximate runs.",
    )
    parser.add_argument("--max-cycles", type=int, default=0, help="Stop after N processed cycles, 0 = no limit")
    parser.add_argument("--top", type=int, default=20, help="Number of top simulated events to print")
    return parser.parse_args()


def local_window(args: argparse.Namespace) -> tuple[datetime, datetime]:
    start_local = datetime.combine(args.date, time.min, tzinfo=WARSAW)
    end_local = start_local + timedelta(days=1)
    if args.from_time:
        hh, mm = [int(part) for part in args.from_time.split(":", 1)]
        start_local = datetime.combine(args.date, time(hh, mm), tzinfo=WARSAW)
    if args.to_time:
        hh, mm = [int(part) for part in args.to_time.split(":", 1)]
        end_local = datetime.combine(args.date, time(hh, mm), tzinfo=WARSAW)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def load_observations(settings: Settings, start: datetime, end: datetime) -> list[AircraftState]:
    with connect(settings.database_url) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT observed_at, icao, callsign, aircraft_type, lat, lon, altitude_ft,
                   ground_speed_kt, track_deg, vertical_rate_fpm, origin, destination, raw_json
            FROM aircraft_observations
            WHERE created_at >= %s AND created_at < %s
            ORDER BY created_at, id
            """,
            (start, end),
        )
        rows = cur.fetchall()
    return [
        AircraftState(
            icao=row[1],
            callsign=row[2],
            aircraft_type=row[3],
            lat=float(row[4]),
            lon=float(row[5]),
            altitude_ft=float(row[6]) if row[6] is not None else None,
            ground_speed_kt=float(row[7]) if row[7] is not None else None,
            track_deg=float(row[8]) if row[8] is not None else None,
            vertical_rate_fpm=float(row[9]) if row[9] is not None else None,
            origin=row[10],
            destination=row[11],
            raw_json=row[12] or {},
            timestamp=row[0],
        )
        for row in rows
    ]


def load_existing_alerts(settings: Settings, start: datetime, end: datetime) -> Counter:
    with connect(settings.database_url) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT COALESCE(tc.callsign, tc.icao), tc.body, count(*)
            FROM alerts a
            JOIN transit_candidates tc ON tc.id = a.transit_candidate_id
            WHERE a.printed_at >= %s AND a.printed_at < %s
            GROUP BY COALESCE(tc.callsign, tc.icao), tc.body
            """,
            (start, end),
        )
        return Counter({(row[0], row[1]): int(row[2]) for row in cur.fetchall()})


def group_cycles(observations: list[AircraftState], poll_interval_seconds: int) -> list[list[AircraftState]]:
    buckets: dict[int, dict[str, AircraftState]] = defaultdict(dict)
    interval = max(1, int(poll_interval_seconds))
    for aircraft in observations:
        bucket = int(aircraft.timestamp.timestamp()) // interval
        current = buckets[bucket].get(aircraft.icao.lower())
        if current is None or aircraft.timestamp >= current.timestamp:
            buckets[bucket][aircraft.icao.lower()] = aircraft
    return [list(buckets[key].values()) for key in sorted(buckets)]


def prune_history(history: deque[AircraftState], now: datetime, keep_seconds: int = 120) -> None:
    while history and (now - history[0].timestamp).total_seconds() > keep_seconds:
        history.popleft()


def classify_at(
    candidate: TransitCandidate,
    settings: Settings,
    *,
    stable: bool,
    now: datetime,
) -> TransitCandidate:
    lead_time = int((candidate.transit_time_utc - now).total_seconds())
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
    elif lead_time < 0 or lead_time > settings.prediction_horizon_seconds:
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
    else:
        candidate.status = "REJECTED"
        candidate.rejection_reason = reason
    return candidate


def simulate(settings: Settings, cycles: list[list[AircraftState]], *, cycle_step: int, max_cycles: int) -> dict:
    histories: dict[str, deque[AircraftState]] = defaultdict(lambda: deque(maxlen=240))
    convergence_tracker = {}
    notified_events: list[TransitCandidate] = []
    airport_profiles = parse_airport_filter_profiles(settings.airport_traffic_filters)
    stats = Counter()
    status_counts = Counter()
    reason_counts = Counter()
    top_events: dict[tuple[str, str, int], TransitCandidate] = {}
    simulated_alerts: list[tuple[str, TransitCandidate]] = []

    processed_cycles = 0
    for index, aircraft in enumerate(cycles):
        if index % max(1, cycle_step) != 0:
            continue
        if max_cycles and processed_cycles >= max_cycles:
            break
        processed_cycles += 1
        if not aircraft:
            continue
        cycle_started = max(ac.timestamp for ac in aircraft)
        stats["cycles"] += 1
        stats["fetched"] += len(aircraft)

        for ac in aircraft:
            histories[ac.icao].append(ac)
            prune_history(histories[ac.icao], ac.timestamp)

        geometry_inputs = []
        airport_matches = {}
        ephemeris_cache = {}
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
            reject = rejection_reason_for_unstable(
                ac,
                history,
                vertical_rate_stable_fpm=settings.max_vertical_rate_stable_fpm,
                stable_vertical_trend=stable_vertical_trend,
            )
            airport_match = classify_airport_traffic(ac, airport_profiles)
            if airport_match:
                airport_matches[ac.icao] = airport_match
            if reject and stable_score < 0.65:
                stats[f"filter_{reject}"] += 1
                continue
            if ac.altitude_ft is not None and ac.altitude_ft < settings.min_altitude_ft:
                stats["filter_LOW_ALTITUDE"] += 1
                continue
            if stable_score < settings.min_stability_score_for_geometry:
                stats[f"filter_{reject or 'INSUFFICIENT_DATA'}"] += 1
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
                stats["filter_NO_PREDICTION_PATH"] += 1
                continue
            max_aircraft_elevation = max(p.elevation_deg for p in path)
            min_aircraft_range = min(p.range_km for p in path)
            if max_aircraft_elevation < settings.min_aircraft_elevation_deg_for_geometry:
                stats["filter_AIRCRAFT_TOO_LOW_IN_SKY"] += 1
                continue
            if min_aircraft_range > settings.max_aircraft_range_km_for_geometry:
                stats["filter_AIRCRAFT_TOO_FAR"] += 1
                continue
            geometry_inputs.append((ac, stable_score, path))

        coarse_timestamps = [
            point.timestamp
            for _ac, _stable_score, path in geometry_inputs
            for point in path[:: max(1, 60 // max(1, settings.prediction_step_seconds))]
        ]
        if coarse_timestamps:
            ephemeris_cache.update(get_body_states_many(settings.user_lat, settings.user_lon, coarse_timestamps))

        selected = []
        for ac, stable_score, path in geometry_inputs:
            coarse_ok, _closest, _allowed = coarse_geometry_check(settings, path, ephemeris_cache)
            if not coarse_ok:
                stats["geometry_coarse_rejected"] += 1
                continue
            selected.append((ac, stable_score, path))
            stats["geometry_selected"] += 1

        full_timestamps = [point.timestamp for _ac, _stable_score, path in selected for point in path]
        if full_timestamps:
            ephemeris_cache.update(get_body_states_many(settings.user_lat, settings.user_lon, full_timestamps))

        candidates: list[TransitCandidate] = []
        for ac, stable_score, path in selected:
            bodies_by_time = {point.timestamp: ephemeris_cache.get(point.timestamp) or get_body_states(settings.user_lat, settings.user_lon, point.timestamp) for point in path}
            raw_candidates = detect_transit_candidates(
                ac,
                path,
                bodies_by_time,
                max_observer_relocation_km=settings.max_observer_relocation_km,
            )
            for raw in sorted(raw_candidates, key=lambda item: item[3])[:MAX_RAW_CANDIDATES_TO_SOLVE]:
                candidate = build_candidate(raw, settings, stable_score)
                candidate = classify_at(candidate, settings, stable=stable_score >= 0.65, now=cycle_started)
                if airport_matches.get(ac.icao) is not None and airport_matches[ac.icao].mode == "strict":
                    if candidate.status in {"ALERT_READY", "OBSERVATION_CANDIDATE"}:
                        candidate.status = "CANDIDATE_STORED"
                        candidate.rejection_reason = f"NEAR_{airport_matches[ac.icao].airport_code}_{'APPROACH' if airport_matches[ac.icao].phase == 'APP' else 'DEPARTURE'}"
                candidates.append(candidate)
                status_counts[candidate.status] += 1
                reason_counts[candidate.rejection_reason or "-"] += 1
                stats["candidates"] += 1

                slot = int(candidate.transit_time_utc.timestamp()) // max(60, settings.locked_alert_window_seconds)
                key = (candidate.aircraft.icao.lower(), candidate.body.lower(), slot)
                previous = top_events.get(key)
                if previous is None or candidate.score > previous.score or (
                    candidate.score == previous.score and candidate.offset_body_diameters < previous.offset_body_diameters
                ):
                    top_events[key] = candidate

        candidates.sort(key=notification_sort_key)
        evaluated_this_cycle: list[TransitCandidate] = []
        notified_this_cycle: list[TransitCandidate] = []
        early_count = 0
        for candidate in candidates[:50]:
            if candidate.status not in {"ALERT_READY", "OBSERVATION_CANDIDATE"}:
                continue
            if notification_event_seen(candidate, evaluated_this_cycle, settings.locked_alert_window_seconds):
                continue
            _confirmed, convergence_count, _reason = update_candidate_convergence(
                candidate,
                convergence_tracker,
                settings,
                cycle_started,
            )
            evaluated_this_cycle.append(candidate)
            phase = candidate_notification_phase(candidate, convergence_count, settings)
            if phase is None:
                stats["notification_deferred"] += 1
                continue
            if notification_event_seen(candidate, notified_this_cycle, settings.locked_alert_window_seconds):
                continue
            if notification_event_seen(candidate, notified_events, settings.locked_alert_window_seconds):
                stats["notification_duplicate"] += 1
                continue
            if phase == "EARLY" and early_count >= settings.telegram_max_candidates_per_cycle:
                continue
            simulated_alerts.append((phase, candidate))
            notified_events.append(candidate)
            notified_this_cycle.append(candidate)
            if phase == "EARLY":
                early_count += 1

    return {
        "stats": stats,
        "status_counts": status_counts,
        "reason_counts": reason_counts,
        "top_events": top_events,
        "simulated_alerts": simulated_alerts,
    }


def print_report(args: argparse.Namespace, settings: Settings, start: datetime, end: datetime, observations: list[AircraftState], cycles: list[list[AircraftState]], result: dict) -> None:
    existing_alerts = load_existing_alerts(settings, start, end)
    stats: Counter = result["stats"]
    simulated_alerts: list[tuple[str, TransitCandidate]] = result["simulated_alerts"]
    top_events = sorted(
        result["top_events"].values(),
        key=lambda c: (-c.score, c.offset_body_diameters, c.observer_distance_km),
    )

    print(f"BACKTEST date={args.date} window={start.astimezone(WARSAW):%Y-%m-%d %H:%M}..{end.astimezone(WARSAW):%Y-%m-%d %H:%M} cycle_step={args.cycle_step}")
    print(f"INPUT observations={len(observations)} reconstructed_cycles={len(cycles)} processed_cycles={stats['cycles']}")
    print(f"OLD_ALERTS count={sum(existing_alerts.values())} events={len(existing_alerts)}")
    print(f"SIM_ALERTS count={len(simulated_alerts)}")
    print("PIPELINE")
    for key in sorted(stats):
        print(f"  {key}: {stats[key]}")
    print("STATUSES")
    for key, value in result["status_counts"].most_common():
        print(f"  {key}: {value}")
    print("REASONS")
    for key, value in result["reason_counts"].most_common(12):
        print(f"  {key}: {value}")
    print("SIMULATED_ALERTS")
    for phase, candidate in simulated_alerts[: args.top]:
        print(
            "  phase=%s callsign=%s body=%s score=%.3f offset=%.3f distance_km=%.2f transit_pl=%s home_offset=%s grid_points=%s"
            % (
                phase,
                candidate.aircraft.callsign or candidate.aircraft.icao,
                candidate.body,
                candidate.score,
                candidate.offset_body_diameters,
                candidate.observer_distance_km,
                candidate.transit_time_utc.astimezone(WARSAW).strftime("%H:%M:%S"),
                f"{candidate.observer_home_offset_body_diameters:.3f}" if candidate.observer_home_offset_body_diameters is not None else "-",
                candidate.observer_grid_points_checked,
            )
        )
    print("TOP_EVENTS")
    for candidate in top_events[: args.top]:
        print(
            "  callsign=%s body=%s status=%s reason=%s score=%.3f offset=%.3f distance_km=%.2f transit_pl=%s home_offset=%s grid_points=%s"
            % (
                candidate.aircraft.callsign or candidate.aircraft.icao,
                candidate.body,
                candidate.status,
                candidate.rejection_reason or "-",
                candidate.score,
                candidate.offset_body_diameters,
                candidate.observer_distance_km,
                candidate.transit_time_utc.astimezone(WARSAW).strftime("%H:%M:%S"),
                f"{candidate.observer_home_offset_body_diameters:.3f}" if candidate.observer_home_offset_body_diameters is not None else "-",
                candidate.observer_grid_points_checked,
            )
        )


def main() -> None:
    args = parse_args()
    settings = load_settings()
    start, end = local_window(args)
    observations = load_observations(settings, start, end)
    cycles = group_cycles(observations, settings.poll_interval_seconds)
    result = simulate(
        settings,
        cycles,
        cycle_step=args.cycle_step,
        max_cycles=args.max_cycles,
    )
    print_report(args, settings, start, end, observations, cycles, result)


if __name__ == "__main__":
    main()
