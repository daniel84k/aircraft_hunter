from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv


LOG = logging.getLogger("adsb_feeder")


@dataclass
class CacheEntry:
    payload: dict[str, Any]
    fetched_at: datetime
    upstream_status: int


@dataclass(frozen=True)
class FeederSettings:
    host: str
    port: int
    upstream_base: str
    cache_ttl_seconds: float
    stale_ttl_seconds: float
    backoff_seconds: float
    timeout_seconds: float
    coordinate_precision: int


class ADSBFeeder:
    def __init__(
        self,
        upstream_base: str,
        cache_ttl_seconds: float,
        stale_ttl_seconds: float,
        backoff_seconds: float,
        timeout_seconds: float,
        coordinate_precision: int = 5,
        session: requests.Session | None = None,
    ) -> None:
        self.upstream_base = upstream_base.rstrip("/")
        self.cache_ttl = timedelta(seconds=cache_ttl_seconds)
        self.stale_ttl = timedelta(seconds=stale_ttl_seconds)
        self.backoff = timedelta(seconds=backoff_seconds)
        self.timeout_seconds = timeout_seconds
        self.coordinate_precision = coordinate_precision
        self.session = session or requests.Session()
        self._cache: dict[tuple[float, float, float], CacheEntry] = {}
        self._backoff_until: dict[tuple[float, float, float], datetime] = {}
        self._lock = threading.Lock()
        self.request_count = 0
        self.upstream_fetch_count = 0
        self.upstream_error_count = 0
        self.cache_hit_count = 0
        self.stale_hit_count = 0

    def get_aircraft(self, lat: float, lon: float, radius_nm: float) -> tuple[dict[str, Any], int]:
        now = datetime.now(timezone.utc)
        key = self._cache_key(lat, lon, radius_nm)
        with self._lock:
            self.request_count += 1
            entry = self._cache.get(key)
            backoff_until = self._backoff_until.get(key)
            if entry and now - entry.fetched_at <= self.cache_ttl:
                self.cache_hit_count += 1
                return self._wrap(entry, cached=True, stale=False, now=now), 200
            if entry and backoff_until and now < backoff_until and now - entry.fetched_at <= self.stale_ttl:
                self.stale_hit_count += 1
                return self._wrap(entry, cached=True, stale=True, now=now), 200

        payload, status = self._fetch_upstream(lat, lon, radius_nm)
        now = datetime.now(timezone.utc)
        if status == 200 and payload is not None:
            entry = CacheEntry(payload=payload, fetched_at=now, upstream_status=status)
            with self._lock:
                self._cache[key] = entry
                self._backoff_until.pop(key, None)
            return self._wrap(entry, cached=False, stale=False, now=now), 200

        with self._lock:
            self.upstream_error_count += 1
            if status == 429:
                self._backoff_until[key] = now + self.backoff
            entry = self._cache.get(key)
            if entry and now - entry.fetched_at <= self.stale_ttl:
                self.stale_hit_count += 1
                return self._wrap(entry, cached=True, stale=True, now=now, upstream_error_status=status), 200

        return {
            "aircraft": [],
            "cached": False,
            "stale": False,
            "upstream_status": status,
            "error": "upstream_unavailable",
        }, 503

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "cache_entries": len(self._cache),
                "request_count": self.request_count,
                "upstream_fetch_count": self.upstream_fetch_count,
                "upstream_error_count": self.upstream_error_count,
                "cache_hit_count": self.cache_hit_count,
                "stale_hit_count": self.stale_hit_count,
            }

    def _fetch_upstream(self, lat: float, lon: float, radius_nm: float) -> tuple[dict[str, Any] | None, int]:
        url = f"{self.upstream_base}/v2/lat/{lat}/lon/{lon}/dist/{radius_nm}"
        try:
            with self._lock:
                self.upstream_fetch_count += 1
            response = self.session.get(url, timeout=self.timeout_seconds)
            if response.status_code == 429:
                LOG.warning("ADSB upstream rate limited status=429 url=%s", url)
                return None, 429
            response.raise_for_status()
            return response.json(), response.status_code
        except (requests.RequestException, ValueError) as exc:
            status = exc.response.status_code if isinstance(exc, requests.HTTPError) and exc.response else 502
            LOG.warning("ADSB upstream fetch failed status=%s error=%s", status, exc)
            return None, status

    def _cache_key(self, lat: float, lon: float, radius_nm: float) -> tuple[float, float, float]:
        return (
            round(lat, self.coordinate_precision),
            round(lon, self.coordinate_precision),
            round(radius_nm, 2),
        )

    def _wrap(
        self,
        entry: CacheEntry,
        cached: bool,
        stale: bool,
        now: datetime,
        upstream_error_status: int | None = None,
    ) -> dict[str, Any]:
        payload = dict(entry.payload)
        payload["cached"] = cached
        payload["stale"] = stale
        payload["fetched_at"] = entry.fetched_at.isoformat()
        payload["age_seconds"] = round((now - entry.fetched_at).total_seconds(), 3)
        payload["upstream_status"] = entry.upstream_status
        if upstream_error_status is not None:
            payload["upstream_error_status"] = upstream_error_status
        return payload


def load_settings() -> FeederSettings:
    load_dotenv()
    return FeederSettings(
        host=os.getenv("ADSB_FEEDER_HOST", "0.0.0.0"),
        port=int(os.getenv("ADSB_FEEDER_PORT", "9988")),
        upstream_base=os.getenv("ADSB_FEEDER_UPSTREAM_BASE", "https://opendata.adsb.fi/api"),
        cache_ttl_seconds=float(os.getenv("ADSB_FEEDER_CACHE_TTL_SECONDS", "10")),
        stale_ttl_seconds=float(os.getenv("ADSB_FEEDER_STALE_TTL_SECONDS", "120")),
        backoff_seconds=float(os.getenv("ADSB_FEEDER_429_BACKOFF_SECONDS", "60")),
        timeout_seconds=float(os.getenv("ADSB_FEEDER_TIMEOUT_SECONDS", "8")),
        coordinate_precision=int(os.getenv("ADSB_FEEDER_COORDINATE_PRECISION", "5")),
    )


def make_handler(feeder: ADSBFeeder) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/health":
                self._send_json({"ok": True}, 200)
                return
            if parsed.path == "/stats":
                self._send_json(feeder.stats(), 200)
                return

            match = _match_adsb_path(parsed.path)
            if match is None:
                self._send_json({"error": "not_found"}, 404)
                return
            lat, lon, radius_nm = match
            payload, status = feeder.get_aircraft(lat, lon, radius_nm)
            self._send_json(payload, status)

        def log_message(self, fmt: str, *args: Any) -> None:
            LOG.info("%s - %s", self.address_string(), fmt % args)

        def _send_json(self, payload: dict[str, Any], status: int) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def _match_adsb_path(path: str) -> tuple[float, float, float] | None:
    parts = path.strip("/").split("/")
    if len(parts) != 7 or parts[0] != "v2" or parts[1] != "lat" or parts[3] != "lon" or parts[5] != "dist":
        return None
    try:
        return float(parts[2]), float(parts[4]), float(parts[6])
    except ValueError:
        return None


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s | %(levelname)s | %(message)s")
    settings = load_settings()
    feeder = ADSBFeeder(
        upstream_base=settings.upstream_base,
        cache_ttl_seconds=settings.cache_ttl_seconds,
        stale_ttl_seconds=settings.stale_ttl_seconds,
        backoff_seconds=settings.backoff_seconds,
        timeout_seconds=settings.timeout_seconds,
        coordinate_precision=settings.coordinate_precision,
    )
    server = ThreadingHTTPServer((settings.host, settings.port), make_handler(feeder))
    LOG.info(
        "ADSB feeder started host=%s port=%s upstream=%s cache_ttl=%ss stale_ttl=%ss",
        settings.host,
        settings.port,
        settings.upstream_base,
        settings.cache_ttl_seconds,
        settings.stale_ttl_seconds,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
