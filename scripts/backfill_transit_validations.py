#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from config import load_settings
from db import connect
from storage import Storage
from validation import format_validation_message, validate_actual_transit


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill post-transit validation for one Warsaw calendar day.")
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--date", type=date.fromisoformat, help="Day in YYYY-MM-DD format")
    scope.add_argument("--all", action="store_true", help="Process every historical alert")
    parser.add_argument(
        "--notify",
        action="store_true",
        help="Leave generated results pending for Telegram delivery (off by default)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = load_settings()
    warsaw = ZoneInfo("Europe/Warsaw")
    if args.all:
        start = datetime(2000, 1, 1, tzinfo=timezone.utc)
        end = datetime.now(timezone.utc) + timedelta(days=1)
        scope_label = "all"
    else:
        start = datetime.combine(args.date, time.min, tzinfo=warsaw).astimezone(timezone.utc)
        end = (datetime.combine(args.date, time.min, tzinfo=warsaw) + timedelta(days=1)).astimezone(timezone.utc)
        scope_label = args.date.isoformat()
    validated_at = datetime.now(timezone.utc)

    with connect(settings.database_url) as conn:
        storage = Storage(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH ranked AS (
                    SELECT
                        a.id AS alert_id,
                        tc.icao,
                        tc.callsign,
                        tc.body,
                        floor(extract(epoch FROM tc.transit_time_utc) / %s)::bigint AS event_slot,
                        tc.transit_time_utc,
                        tc.observer_lat,
                        tc.observer_lon,
                        tc.offset_body_diameters,
                        row_number() OVER (
                            PARTITION BY lower(tc.icao), lower(tc.body),
                                         floor(extract(epoch FROM tc.transit_time_utc) / %s)
                            ORDER BY a.printed_at DESC, tc.offset_body_diameters ASC
                        ) AS rank
                    FROM alerts a
                    JOIN transit_candidates tc ON tc.id = a.transit_candidate_id
                    WHERE a.printed_at >= %s AND a.printed_at < %s
                )
                SELECT alert_id, icao, callsign, body, event_slot, transit_time_utc,
                       observer_lat, observer_lon, offset_body_diameters
                FROM ranked event
                WHERE rank = 1
                  AND NOT EXISTS (
                      SELECT 1 FROM transit_validations validation
                      WHERE validation.icao = lower(event.icao)
                        AND validation.body = lower(event.body)
                        AND validation.event_slot = event.event_slot
                  )
                ORDER BY transit_time_utc
                """,
                (
                    settings.locked_alert_window_seconds,
                    settings.locked_alert_window_seconds,
                    start,
                    end,
                ),
            )
            rows = cur.fetchall()
        conn.commit()

        counts: Counter[str] = Counter()
        for row in rows:
            event = {
                "alert_id": row[0],
                "icao": row[1],
                "callsign": row[2],
                "body": row[3],
                "event_slot": row[4],
                "transit_time_utc": row[5],
                "observer_lat": row[6],
                "observer_lon": row[7],
                "predicted_offset_body_diameters": row[8],
            }
            observations = storage.observations_around(
                icao=event["icao"],
                timestamp=event["transit_time_utc"],
                window_seconds=settings.transit_validation_observation_window_seconds,
            )
            event["observation_count"] = len(observations)
            result = validate_actual_transit(
                observations,
                body_name=event["body"],
                observer_lat=event["observer_lat"],
                observer_lon=event["observer_lon"],
                hit_uncertainty_diameters=settings.transit_validation_uncertainty_diameters,
            )
            message = format_validation_message(event, result)
            inserted = storage.insert_validation(
                event,
                result,
                message,
                validated_at,
                notified_at=None if args.notify else validated_at,
            )
            if inserted:
                counts[result.result if result else "NO_DATA"] += 1

    total = sum(counts.values())
    summary = " ".join(f"{name}={counts[name]}" for name in ("HIT", "MISS", "UNCERTAIN", "NO_DATA"))
    print(f"scope={scope_label} inserted={total} {summary}")


if __name__ == "__main__":
    main()
