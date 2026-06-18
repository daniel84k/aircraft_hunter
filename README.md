# Aircraft Transit Hunter

Aircraft Transit Hunter v1 is a Python CLI prototype for finding strong aircraft transit candidates across the Moon or Sun. It polls ADS-B data from ADSB.fi, keeps recent flight history in memory, stores observations and prediction results in PostgreSQL, and prints only high-confidence alerts to the console.

The application searches for observation points up to 5 km from the configured observer location. The database stores much more than the console prints: weak, late, uncertain, or rejected candidates are saved for later analysis, while quiet mode prints only events worth acting on.

## Configuration

Copy `.env.example` to `.env` and adjust at least:

```bash
USER_LAT=52.000000
USER_LON=21.000000
DATABASE_URL=postgresql://aircraft:aircraft@postgres:5432/aircraft_transit
RUN_MODE=quiet
```

`RUN_MODE=quiet` prints only transit alerts. `RUN_MODE=debug` also prints cycle candidate summaries. Detailed diagnostics always go to logs and PostgreSQL.

## Run Locally

Start PostgreSQL yourself, then:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=src python src/main.py
```

## Run With Docker Compose

```bash
cp .env.example .env
docker compose up --build
```

The compose file starts `app`, `ui`, `adsb-feeder`, and `postgres`. `adsb-feeder` is the only container that calls ADSB.fi; `app` reads the compatible local endpoint at `http://adsb-feeder:9988`. The web dashboard runs in the separate `ui` container so slow dashboard queries do not share the prediction worker process. Logs are written under `./logs`.

The feeder exposes:

```text
http://localhost:9988/health
http://localhost:9988/stats
http://localhost:9988/v2/lat/{lat}/lon/{lon}/dist/{radius_nm}
```

It has a small in-memory cache and a `429` backoff path. When ADSB.fi is temporarily rate-limited, the feeder returns the last cached payload as stale instead of making every app hit the upstream API again.

## Web UI

A small operational dashboard is exposed on:

```text
http://localhost:9999
```

It shows observation/run/candidate/alert counts, recent prediction runs, rejection summaries, latest candidates, latest alerts, and a live tail of the application log. It is intended for analysis of the running prototype, not as the final user-facing product.

## Project Structure

`src/adsb_client.py` fetches and validates ADS-B records. `src/stability.py` scores flight stability using altitude, vertical rate, track change, and speed change. `src/prediction.py` performs linear aircraft prediction. `src/ephemeris.py` calculates Moon and Sun positions with Skyfield. `src/transit_detector.py` finds angular alignments. `src/observer_solver.py` searches for a bounded observation point. `src/scoring.py` ranks candidates. `src/storage.py` writes observations, prediction runs, candidates, and alerts to PostgreSQL.

## Scoring

Final score is:

```text
alignment * stability * altitude * body_elevation * aircraft_range * lead_time * observer_distance
```

Alerts require `score >= ALERT_MIN_SCORE`, at least `MIN_LEAD_TIME_SECONDS`, offset no worse than `MAX_OFFSET_BODY_DIAMETERS_FOR_ALERT`, body elevation above `MIN_BODY_ELEVATION_DEG`, relocation within `MAX_OBSERVER_RELOCATION_KM`, and no previous alert with the same dedupe key.

Candidates scoring roughly `0.65-0.79` are stored but not printed. Rejected candidates include a `rejection_reason` such as `LOW_SCORE`, `TOO_LATE`, `BODY_TOO_LOW`, `OFFSET_TOO_LARGE`, or `OBSERVER_POINT_TOO_FAR`.

In debug mode, stable flights can still be skipped before ephemeris comparison when they are not photographically sensible from the observer location. `MIN_AIRCRAFT_ELEVATION_DEG_FOR_GEOMETRY` and `MAX_AIRCRAFT_RANGE_KM_FOR_GEOMETRY` limit this early geometry check.

`SEARCH_RADIUS_NM` is intentionally wider than the geometry range: it controls how early aircraft are fetched from ADS-B. `MAX_AIRCRAFT_RANGE_KM_FOR_GEOMETRY` controls when the aircraft is close enough to be photographically useful, and `PREDICTION_HORIZON_SECONDS` must be long enough to cover the time from first fetch to possible transit.

The practical notification path is intentionally looser than the final alert path. `OBSERVATION_CANDIDATE_MAX_SEPARATION_DEG` controls the maximum angular error for an observation candidate, while `TRAVEL_SPEED_KMH`, `REACH_SAFETY`, and `MAX_OBSERVER_RELOCATION_KM` determine how far the observer can reasonably relocate before the predicted transit.

## Database Analysis

The migration in `migrations/001_init.sql` creates:

```text
aircraft_observations
prediction_runs
transit_candidates
alerts
```

Useful starting queries:

```sql
SELECT body, status, rejection_reason, count(*)
FROM transit_candidates
GROUP BY body, status, rejection_reason
ORDER BY count(*) DESC;

SELECT transit_time_utc, icao, body, score, observer_distance_km, google_maps_url
FROM transit_candidates
WHERE score >= 0.65
ORDER BY transit_time_utc DESC
LIMIT 50;
```

## Logging

Logs include timestamps and rotate daily through `TimedRotatingFileHandler`. The active file is `logs/aircraft-transit.log`; rotated files receive date suffixes. Logs include ADS-B fetch timing, fetched and analyzed counts, saved candidates, alert counts, best candidate summaries, API errors, DB errors, and rejection reasons.

## v1 Limitations

Prediction is linear: constant track, groundspeed, and optional vertical-rate altitude correction. ADS-B may be delayed or unavailable. The observer solver is approximate and bounded; it avoids brute-forcing a 5 km grid. Skyfield ephemerides depend on local availability or download of `de421.bsp`. Weather, cloud cover, Telegram notifications, GUI, full FMS trajectory modeling, and a full geodetic solver are intentionally out of scope.

Airport exclusion is configured, but v1 does not ship an airport coordinate database, so origin/destination proximity is not reliably computed. When origin/destination are missing or unresolved, the app relies on stability, altitude, groundspeed, and vertical-rate scoring instead.

No alert does not mean no geometric candidate. It means the candidate failed the aggressive console-alert filters or was stored only for later database analysis.
