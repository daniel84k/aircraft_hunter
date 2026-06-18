from __future__ import annotations

import requests

from adsb_feeder import ADSBFeeder, _match_adsb_path


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def get(self, url: str, timeout: float) -> FakeResponse:
        self.calls.append(url)
        return self.responses.pop(0)


def make_feeder(session: FakeSession, cache_ttl_seconds: float = 60) -> ADSBFeeder:
    return ADSBFeeder(
        upstream_base="https://example.test/api",
        cache_ttl_seconds=cache_ttl_seconds,
        stale_ttl_seconds=120,
        backoff_seconds=60,
        timeout_seconds=1,
        session=session,
    )


def test_match_adsb_path_accepts_compatible_url() -> None:
    assert _match_adsb_path("/v2/lat/52.19359/lon/20.42513/dist/80.0") == (52.19359, 20.42513, 80.0)
    assert _match_adsb_path("/stats") is None


def test_get_aircraft_uses_fresh_cache_for_same_query() -> None:
    session = FakeSession([FakeResponse(200, {"aircraft": [{"hex": "abc"}]})])
    feeder = make_feeder(session)

    first, first_status = feeder.get_aircraft(52.19359, 20.42513, 80)
    second, second_status = feeder.get_aircraft(52.19359, 20.42513, 80)

    assert first_status == 200
    assert second_status == 200
    assert first["cached"] is False
    assert second["cached"] is True
    assert second["stale"] is False
    assert len(session.calls) == 1


def test_get_aircraft_returns_stale_cache_after_429() -> None:
    session = FakeSession(
        [
            FakeResponse(200, {"aircraft": [{"hex": "abc"}]}),
            FakeResponse(429),
        ]
    )
    feeder = make_feeder(session, cache_ttl_seconds=0)

    first, first_status = feeder.get_aircraft(52.19359, 20.42513, 80)
    second, second_status = feeder.get_aircraft(52.19359, 20.42513, 80)
    third, third_status = feeder.get_aircraft(52.19359, 20.42513, 80)

    assert first_status == 200
    assert second_status == 200
    assert third_status == 200
    assert first["cached"] is False
    assert second["cached"] is True
    assert second["stale"] is True
    assert second["upstream_error_status"] == 429
    assert third["cached"] is True
    assert third["stale"] is True
    assert len(session.calls) == 2
