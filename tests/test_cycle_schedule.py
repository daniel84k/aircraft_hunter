from main import remaining_cycle_delay


def test_cycle_delay_uses_only_remaining_interval() -> None:
    assert remaining_cycle_delay(100.0, 10.0, 103.5) == 6.5


def test_cycle_delay_does_not_add_sleep_after_overrun() -> None:
    assert remaining_cycle_delay(100.0, 10.0, 115.0) == 0.0
