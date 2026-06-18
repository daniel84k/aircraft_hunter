from datetime import datetime, timedelta, timezone

from models import AircraftState
from stability import stability_score


def ac(ts, altitude=30000, track=90, gs=450, vr=0):
    return AircraftState("abc123", "LOT123", 52.0, 21.0, altitude, gs, track, vr, "B789", None, None, ts, {})


def test_stability_score_good_high_stable_flight() -> None:
    now = datetime.now(timezone.utc)
    history = [ac(now - timedelta(seconds=60), track=88), ac(now, track=90)]
    assert stability_score(history[-1], history) == 1.0


def test_stability_score_rejects_low_altitude() -> None:
    now = datetime.now(timezone.utc)
    aircraft = ac(now, altitude=4000)
    assert stability_score(aircraft, [aircraft]) == 0.0


def test_stability_score_rejects_large_turn() -> None:
    now = datetime.now(timezone.utc)
    history = [ac(now - timedelta(seconds=60), track=10), ac(now, track=50)]
    assert stability_score(history[-1], history) == 0.0
