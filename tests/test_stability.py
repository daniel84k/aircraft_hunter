from datetime import datetime, timedelta, timezone

from models import AircraftState
from stability import has_stable_vertical_trend, rejection_reason_for_unstable, stability_score


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


def test_consistent_climb_can_be_treated_as_stable() -> None:
    now = datetime.now(timezone.utc)
    history = [
        ac(now - timedelta(seconds=75), vr=1800),
        ac(now - timedelta(seconds=50), vr=1920),
        ac(now - timedelta(seconds=25), vr=1856),
        ac(now, vr=1792),
    ]

    assert has_stable_vertical_trend(history)
    assert stability_score(history[-1], history, stable_vertical_trend=True) == 1.0
    assert rejection_reason_for_unstable(history[-1], history, stable_vertical_trend=True) is None


def test_erratic_climb_is_not_treated_as_stable() -> None:
    now = datetime.now(timezone.utc)
    history = [
        ac(now - timedelta(seconds=75), vr=800),
        ac(now - timedelta(seconds=50), vr=2200),
        ac(now - timedelta(seconds=25), vr=900),
        ac(now, vr=2600),
    ]

    assert not has_stable_vertical_trend(history)
    assert stability_score(history[-1], history) < 0.65
    assert rejection_reason_for_unstable(history[-1], history) == "HIGH_VERTICAL_RATE"


def test_vertical_trend_requires_one_direction() -> None:
    now = datetime.now(timezone.utc)
    history = [
        ac(now - timedelta(seconds=75), vr=1500),
        ac(now - timedelta(seconds=50), vr=1450),
        ac(now - timedelta(seconds=25), vr=-1400),
        ac(now, vr=-1500),
    ]

    assert not has_stable_vertical_trend(history)
