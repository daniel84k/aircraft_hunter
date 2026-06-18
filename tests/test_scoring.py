from scoring import lead_time_score, observer_distance_score


def test_lead_time_score() -> None:
    assert lead_time_score(599) == 0.0
    assert lead_time_score(600) == 1.0
    assert lead_time_score(1800) == 0.8
    assert lead_time_score(3000) == 0.5


def test_observer_distance_score() -> None:
    assert observer_distance_score(2.4) == 1.0
    assert observer_distance_score(4.8) == 0.8
    assert observer_distance_score(6.0) == 0.4
    assert observer_distance_score(6.1) == 0.0
