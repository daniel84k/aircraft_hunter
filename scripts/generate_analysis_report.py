#!/usr/bin/env python3
import csv
import os
import re
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
START = datetime(2026, 6, 17, 15, 35, 0, tzinfo=timezone.utc)
END = datetime(2026, 6, 17, 18, 35, 0, tzinfo=timezone.utc)
LOG_PATH = ROOT / "logs" / "aircraft-transit-2026-06-17.log"
OUT_PATH = ROOT / "analysis_2026-06-17_1535-1835UTC.txt"


def parse_env(path):
    data = {}
    if not path.exists():
        return data
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data


def dt_from_log_line(line):
    try:
        return datetime.strptime(line[:28], "%Y-%m-%d %H:%M:%S.%f%z").astimezone(timezone.utc)
    except ValueError:
        return None


def fields_from_pipe_payload(line):
    payload = line.split("|", 2)[-1]
    result = {}
    for match in re.finditer(r"([a-zA-Z_][a-zA-Z0-9_]*)=([^ |]+)", payload):
        result[match.group(1)] = match.group(2)
    return result


def parse_float(value):
    if value in (None, "", "None"):
        return ""
    try:
        return float(value)
    except ValueError:
        return ""


def psql_copy(query):
    copy = f"COPY ({query}) TO STDOUT WITH CSV HEADER DELIMITER E'\\t'"
    cmd = [
        "docker",
        "compose",
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        "aircraft",
        "-d",
        "aircraft_transit",
        "-v",
        "ON_ERROR_STOP=1",
        "-c",
        copy,
    ]
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())
    return proc.stdout.strip("\n")


def write_section(lines, title, body):
    lines.append(f"[{title}]")
    if isinstance(body, str):
        lines.extend(body.splitlines() if body else [""])
    else:
        lines.extend(body)
    lines.append("")


def tsv_from_rows(headers, rows):
    out = ["\t".join(headers)]
    for row in rows:
        out.append("\t".join(str(row.get(h, "")) for h in headers))
    return out


def main():
    env = parse_env(ROOT / ".env")
    generated_at = datetime.now(timezone.utc).isoformat()

    step_counts = Counter()
    rejection_counts = Counter()
    visibility_skip_counts = Counter()
    geometry_skip_counts = Counter()
    candidate_reason_counts = Counter()
    geometry_selected = []
    geometry_no_alignment = []
    geometry_skipped = []
    candidate_scored = []
    filter_rejected = []

    step_re = re.compile(r"cycle=(\d+) step=([0-9]/5) ([A-Z_]+) \|")

    if LOG_PATH.exists():
        with LOG_PATH.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                dt = dt_from_log_line(line)
                if dt is None or dt < START or dt > END:
                    continue
                step_match = step_re.search(line)
                if step_match:
                    label = step_match.group(3)
                    step_counts[f"{step_match.group(2)} {label}"] += 1
                fields = fields_from_pipe_payload(line)
                cycle = fields.get("cycle", "")

                if "FILTER_REJECTED" in line:
                    reason = fields.get("reason", "")
                    rejection_counts[reason] += 1
                    filter_rejected.append(
                        {
                            "log_time_utc": dt.isoformat(),
                            "cycle": cycle,
                            "icao": fields.get("aircraft", ""),
                            "callsign": fields.get("callsign", ""),
                            "reason": reason,
                            "stability": fields.get("stability", ""),
                            "history_points": fields.get("history_points", ""),
                        }
                    )
                elif "VISIBILITY_SKIPPED" in line:
                    visibility_skip_counts[fields.get("reason", "")] += 1
                elif "GEOMETRY_SKIPPED" in line:
                    reason = fields.get("reason", "")
                    geometry_skip_counts[reason] += 1
                    row = {
                        "log_time_utc": dt.isoformat(),
                        "cycle": cycle,
                        "icao": fields.get("aircraft", ""),
                        "callsign": fields.get("callsign", ""),
                        "reason": reason,
                        "closest_body": fields.get("closest_body", ""),
                        "closest_time_utc": fields.get("closest_time_utc", ""),
                        "closest_separation_deg": fields.get("closest_separation_deg", ""),
                        "allowed_separation_deg": fields.get("allowed_separation_deg", ""),
                        "closest_offset_diameters": fields.get("closest_offset_diameters", ""),
                        "body_azimuth_deg": fields.get("body_azimuth_deg", ""),
                        "body_elevation_deg": fields.get("body_elevation_deg", ""),
                        "aircraft_azimuth_deg": fields.get("aircraft_azimuth_deg", ""),
                        "aircraft_elevation_deg": fields.get("aircraft_elevation_deg", ""),
                    }
                    geometry_skipped.append(row)
                elif "GEOMETRY_SELECTED" in line:
                    geometry_selected.append(
                        {
                            "log_time_utc": dt.isoformat(),
                            "cycle": cycle,
                            "icao": fields.get("aircraft", ""),
                            "callsign": fields.get("callsign", ""),
                            "aircraft_type": fields.get("type", ""),
                            "track_deg": fields.get("track_deg", ""),
                            "altitude_ft": fields.get("altitude_ft", ""),
                            "stability": fields.get("stability", ""),
                            "min_aircraft_range_km": fields.get("min_aircraft_range_km", ""),
                            "max_aircraft_elevation_deg": fields.get("max_aircraft_elevation_deg", ""),
                        }
                    )
                elif "GEOMETRY_NO_ALIGNMENT" in line:
                    geometry_no_alignment.append(
                        {
                            "log_time_utc": dt.isoformat(),
                            "cycle": cycle,
                            "icao": fields.get("aircraft", ""),
                            "callsign": fields.get("callsign", ""),
                            "closest_body": fields.get("closest_body", ""),
                            "closest_time_utc": fields.get("closest_time_utc", ""),
                            "closest_separation_deg": fields.get("closest_separation_deg", ""),
                            "allowed_separation_deg": fields.get("allowed_separation_deg", ""),
                            "closest_offset_diameters": fields.get("closest_offset_diameters", ""),
                            "body_azimuth_deg": fields.get("body_azimuth_deg", ""),
                            "body_elevation_deg": fields.get("body_elevation_deg", ""),
                            "aircraft_azimuth_deg": fields.get("aircraft_azimuth_deg", ""),
                            "aircraft_elevation_deg": fields.get("aircraft_elevation_deg", ""),
                        }
                    )
                elif "CANDIDATE_SCORED" in line:
                    reason = fields.get("reason", "")
                    candidate_reason_counts[reason] += 1
                    candidate_scored.append(
                        {
                            "log_time_utc": dt.isoformat(),
                            "cycle": cycle,
                            "icao": fields.get("aircraft", ""),
                            "callsign": fields.get("callsign", ""),
                            "body": fields.get("body", ""),
                            "status": fields.get("status", ""),
                            "reason": reason,
                            "score": fields.get("score", ""),
                            "offset_diameters": fields.get("offset_diameters", ""),
                            "observer_distance_km": fields.get("observer_distance_km", ""),
                            "transit_time_utc": fields.get("transit_time_utc", ""),
                        }
                    )

    geometry_no_alignment.sort(key=lambda r: parse_float(r["closest_offset_diameters"]) if parse_float(r["closest_offset_diameters"]) != "" else 999999)
    geometry_skipped.sort(key=lambda r: parse_float(r["closest_offset_diameters"]) if parse_float(r["closest_offset_diameters"]) != "" else 999999)

    start_s = START.isoformat().replace("+00:00", "+00")
    end_s = END.isoformat().replace("+00:00", "+00")
    where_created = f"created_at >= '{start_s}'::timestamptz AND created_at <= '{end_s}'::timestamptz"
    where_observed = f"observed_at >= '{start_s}'::timestamptz AND observed_at <= '{end_s}'::timestamptz"
    where_runs = f"started_at >= '{start_s}'::timestamptz AND started_at <= '{end_s}'::timestamptz"

    config_keys = [
        "USER_LAT",
        "USER_LON",
        "SEARCH_RADIUS_NM",
        "POLL_INTERVAL_SECONDS",
        "PREDICTION_HORIZON_SECONDS",
        "PREDICTION_STEP_SECONDS",
        "MAX_OBSERVER_RELOCATION_KM",
        "MIN_LEAD_TIME_SECONDS",
        "PREFERRED_LEAD_TIME_SECONDS",
        "MIN_ALTITUDE_FT",
        "SOFT_GOOD_ALTITUDE_FT",
        "MAX_VERTICAL_RATE_STABLE_FPM",
        "MAX_TRACK_CHANGE_60S_DEG",
        "MAX_GS_CHANGE_60S_KT",
        "MIN_STABILITY_SCORE_FOR_GEOMETRY",
        "MIN_AIRCRAFT_ELEVATION_DEG_FOR_GEOMETRY",
        "MAX_AIRCRAFT_RANGE_KM_FOR_GEOMETRY",
        "ALERT_MIN_SCORE",
        "MAX_OFFSET_BODY_DIAMETERS_FOR_ALERT",
        "MIN_BODY_ELEVATION_DEG",
        "STANDBY_BODY_ELEVATION_DEG",
        "RUN_MODE",
    ]
    config_rows = [f"{key}\t{env.get(key, '')}" for key in config_keys]

    db_sections = {
        "DB_OBSERVATION_AGGREGATE": psql_copy(
            f"""
            SELECT
              count(*) AS observations,
              count(DISTINCT icao) AS distinct_aircraft,
              min(observed_at) AS first_observed_at,
              max(observed_at) AS last_observed_at
            FROM aircraft_observations
            WHERE {where_observed}
            """
        ),
        "DB_PREDICTION_RUN_AGGREGATE": psql_copy(
            f"""
            SELECT
              count(*) AS runs,
              coalesce(sum(aircraft_count_total),0) AS aircraft_count_total_sum,
              coalesce(sum(aircraft_count_analyzed),0) AS aircraft_count_analyzed_sum,
              coalesce(sum(candidate_count),0) AS candidate_count_sum,
              coalesce(sum(alert_count),0) AS alert_count_sum,
              min(started_at) AS first_run_started_at,
              max(started_at) AS last_run_started_at
            FROM prediction_runs
            WHERE {where_runs}
            """
        ),
        "DB_CANDIDATE_STATUS_SUMMARY": psql_copy(
            f"""
            SELECT status, rejection_reason, body, count(*) AS count, max(score) AS max_score, min(offset_body_diameters) AS min_offset
            FROM transit_candidates
            WHERE {where_created}
            GROUP BY status, rejection_reason, body
            ORDER BY status, rejection_reason, body
            """
        ),
        "DB_CANDIDATES_FULL_SCORING": psql_copy(
            f"""
            SELECT
              id, prediction_run_id, created_at, transit_time_utc, icao, callsign, aircraft_type, body,
              status, rejection_reason, alert_sent, alerted_at, time_to_transit_seconds,
              observer_lat, observer_lon, observer_distance_km, angular_separation_deg, body_radius_deg,
              offset_body_diameters, score, confidence, stability_score, alignment_score, altitude_score,
              body_elevation_score, aircraft_range_score, lead_time_score, observer_distance_score,
              aircraft_altitude_ft, aircraft_range_km, aircraft_track_deg, aircraft_ground_speed_kt,
              aircraft_vertical_rate_fpm, body_azimuth_deg, body_elevation_deg, dedupe_key,
              google_maps_url, google_nav_url
            FROM transit_candidates
            WHERE {where_created}
            ORDER BY transit_time_utc, score DESC, offset_body_diameters ASC
            """
        ),
        "DB_ALERTS": psql_copy(
            f"""
            SELECT id, transit_candidate_id, alert_type, printed_at, dedupe_key, message
            FROM alerts
            WHERE printed_at >= '{start_s}'::timestamptz AND printed_at <= '{end_s}'::timestamptz
            ORDER BY printed_at
            """
        ),
        "DB_OBSERVATION_SUMMARY_FOR_GEOMETRY_AND_CANDIDATE_AIRCRAFT": psql_copy(
            f"""
            WITH ids AS (
              SELECT DISTINCT icao FROM transit_candidates WHERE {where_created}
              UNION
              SELECT DISTINCT regexp_replace(message, '.*aircraft=([^ ]+).*', '\\1') AS icao
              FROM alerts
              WHERE printed_at >= '{start_s}'::timestamptz AND printed_at <= '{end_s}'::timestamptz
            )
            SELECT
              o.icao,
              max(o.callsign) AS callsign,
              max(o.aircraft_type) AS aircraft_type,
              count(*) AS observation_count,
              min(o.observed_at) AS first_observed_at,
              max(o.observed_at) AS last_observed_at,
              min(o.altitude_ft) AS min_altitude_ft,
              max(o.altitude_ft) AS max_altitude_ft,
              min(o.ground_speed_kt) AS min_ground_speed_kt,
              max(o.ground_speed_kt) AS max_ground_speed_kt,
              min(o.track_deg) AS min_track_deg,
              max(o.track_deg) AS max_track_deg,
              min(o.vertical_rate_fpm) AS min_vertical_rate_fpm,
              max(o.vertical_rate_fpm) AS max_vertical_rate_fpm
            FROM aircraft_observations o
            JOIN ids ON ids.icao = o.icao
            WHERE {where_observed}
            GROUP BY o.icao
            ORDER BY o.icao
            """
        ),
        "DB_PREDICTION_RUNS_ALL": psql_copy(
            f"""
            SELECT id, started_at, finished_at, user_lat, user_lon, search_radius_nm,
                   prediction_horizon_seconds, aircraft_count_total, aircraft_count_analyzed,
                   candidate_count, alert_count
            FROM prediction_runs
            WHERE {where_runs}
            ORDER BY started_at
            """
        ),
    }

    lines = []
    lines.append("AIRCRAFT_TRANSIT_HUNTER_ANALYSIS_REPORT")
    lines.append(f"window_start_utc\t{START.isoformat()}")
    lines.append(f"window_end_utc\t{END.isoformat()}")
    lines.append(f"generated_at_utc\t{generated_at}")
    lines.append(f"log_file\t{LOG_PATH}")
    lines.append("format\tPlain TXT with bracketed TSV sections; first row in each table is header")
    lines.append("")

    write_section(lines, "CONFIG", ["key\tvalue"] + config_rows)
    write_section(lines, "DB_OBSERVATION_AGGREGATE", db_sections["DB_OBSERVATION_AGGREGATE"])
    write_section(lines, "DB_PREDICTION_RUN_AGGREGATE", db_sections["DB_PREDICTION_RUN_AGGREGATE"])
    write_section(lines, "LOG_STEP_COUNTS", ["step_label\tcount"] + [f"{k}\t{v}" for k, v in sorted(step_counts.items())])
    write_section(lines, "LOG_FILTER_REJECTION_COUNTS", ["reason\tcount"] + [f"{k}\t{v}" for k, v in sorted(rejection_counts.items())])
    write_section(lines, "LOG_VISIBILITY_SKIP_COUNTS", ["reason\tcount"] + [f"{k}\t{v}" for k, v in sorted(visibility_skip_counts.items())])
    write_section(lines, "LOG_GEOMETRY_SKIP_COUNTS", ["reason\tcount"] + [f"{k}\t{v}" for k, v in sorted(geometry_skip_counts.items())])
    write_section(lines, "LOG_CANDIDATE_REASON_COUNTS", ["reason\tcount"] + [f"{k}\t{v}" for k, v in sorted(candidate_reason_counts.items())])
    write_section(lines, "DB_CANDIDATE_STATUS_SUMMARY", db_sections["DB_CANDIDATE_STATUS_SUMMARY"])
    write_section(lines, "DB_CANDIDATES_FULL_SCORING", db_sections["DB_CANDIDATES_FULL_SCORING"])
    write_section(lines, "DB_ALERTS", db_sections["DB_ALERTS"])

    write_section(
        lines,
        "LOG_CANDIDATE_SCORED",
        tsv_from_rows(
            [
                "log_time_utc",
                "cycle",
                "icao",
                "callsign",
                "body",
                "status",
                "reason",
                "score",
                "offset_diameters",
                "observer_distance_km",
                "transit_time_utc",
            ],
            candidate_scored,
        ),
    )
    write_section(
        lines,
        "LOG_GEOMETRY_SELECTED_ALL",
        tsv_from_rows(
            [
                "log_time_utc",
                "cycle",
                "icao",
                "callsign",
                "aircraft_type",
                "track_deg",
                "altitude_ft",
                "stability",
                "min_aircraft_range_km",
                "max_aircraft_elevation_deg",
            ],
            geometry_selected,
        ),
    )
    write_section(
        lines,
        "LOG_GEOMETRY_NO_ALIGNMENT_SORTED_BY_OFFSET_TOP_100",
        tsv_from_rows(
            [
                "log_time_utc",
                "cycle",
                "icao",
                "callsign",
                "closest_body",
                "closest_time_utc",
                "closest_separation_deg",
                "allowed_separation_deg",
                "closest_offset_diameters",
                "body_azimuth_deg",
                "body_elevation_deg",
                "aircraft_azimuth_deg",
                "aircraft_elevation_deg",
            ],
            geometry_no_alignment[:100],
        ),
    )
    write_section(
        lines,
        "LOG_GEOMETRY_SKIPPED_SORTED_BY_OFFSET_TOP_100",
        tsv_from_rows(
            [
                "log_time_utc",
                "cycle",
                "icao",
                "callsign",
                "reason",
                "closest_body",
                "closest_time_utc",
                "closest_separation_deg",
                "allowed_separation_deg",
                "closest_offset_diameters",
                "body_azimuth_deg",
                "body_elevation_deg",
                "aircraft_azimuth_deg",
                "aircraft_elevation_deg",
            ],
            geometry_skipped[:100],
        ),
    )
    write_section(lines, "DB_OBSERVATION_SUMMARY_FOR_GEOMETRY_AND_CANDIDATE_AIRCRAFT", db_sections["DB_OBSERVATION_SUMMARY_FOR_GEOMETRY_AND_CANDIDATE_AIRCRAFT"])
    write_section(lines, "DB_PREDICTION_RUNS_ALL", db_sections["DB_PREDICTION_RUNS_ALL"])
    write_section(
        lines,
        "LOG_FILTER_REJECTED_SAMPLE_FIRST_300",
        tsv_from_rows(
            ["log_time_utc", "cycle", "icao", "callsign", "reason", "stability", "history_points"],
            filter_rejected[:300],
        ),
    )

    OUT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(OUT_PATH)


if __name__ == "__main__":
    main()
