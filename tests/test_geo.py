from geo import angular_separation_deg, destination_point, haversine_distance_km


def test_haversine_distance_warsaw_lodz() -> None:
    distance = haversine_distance_km(52.2297, 21.0122, 51.7592, 19.4560)
    assert 115 < distance < 125


def test_destination_point_east() -> None:
    lat, lon = destination_point(0.0, 0.0, 90.0, 111.195)
    assert abs(lat) < 0.01
    assert 0.99 < lon < 1.01


def test_angular_separation_zero_and_quarter() -> None:
    assert angular_separation_deg(10, 20, 10, 20) < 1e-6
    assert 89.9 < angular_separation_deg(0, 0, 90, 0) < 90.1
