from geo import angular_separation_deg, destination_point, haversine_distance_km
import observer_solver
from observer_solver import ObserverSolution, observer_search_grid, solve_observer_point


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


def test_observer_search_grid_matches_solver_rings() -> None:
    points = observer_search_grid(52.19359, 20.42513, 1.0)

    assert len(points) == 97
    assert points[0] == (52.19359, 20.42513, 0.0, None)
    assert {point[2] for point in points[1:]} == {0.25, 0.5, 0.75, 1.0}


def test_solver_checks_grid_even_when_home_is_good(monkeypatch) -> None:
    def fake_grid(_user_lat, _user_lon, _max_relocation_km):
        return [(52.0, 21.0, 0.0, None), (52.001, 21.0, 0.1, 0.0)]

    def fake_solution(user_lat, user_lon, lat, lon, _aircraft_point, _body, confidence):
        offset = 0.20 if lat == user_lat and lon == user_lon else 0.05
        return ObserverSolution(
            lat=lat,
            lon=lon,
            distance_km=haversine_distance_km(user_lat, user_lon, lat, lon),
            confidence=confidence,
            angular_separation_deg=offset,
            offset_body_diameters=offset,
        )

    monkeypatch.setattr(observer_solver, "observer_search_grid", fake_grid)
    monkeypatch.setattr(observer_solver, "_solution_at", fake_solution)

    solution = solve_observer_point(
        52.0,
        21.0,
        aircraft_point=object(),
        body=object(),
        max_relocation_km=1.0,
    )

    assert solution.offset_body_diameters == 0.05
    assert solution.home_offset_body_diameters == 0.20
    assert solution.best_grid_offset_body_diameters == 0.05
    assert solution.grid_points_checked == 2
    assert solution.selected_from_home is False


def test_solver_does_not_use_unreachable_grid_offset_for_home(monkeypatch) -> None:
    def fake_grid(_user_lat, _user_lon, _max_relocation_km):
        return [(52.0, 21.0, 0.0, None), (52.01, 21.0, 2.0, 0.0)]

    def fake_solution(user_lat, user_lon, lat, lon, _aircraft_point, _body, confidence):
        home = lat == user_lat and lon == user_lon
        offset = 1.20 if home else 0.02
        return ObserverSolution(
            lat=lat,
            lon=lon,
            distance_km=0.0 if home else 2.0,
            confidence=confidence,
            angular_separation_deg=offset,
            offset_body_diameters=offset,
        )

    monkeypatch.setattr(observer_solver, "observer_search_grid", fake_grid)
    monkeypatch.setattr(observer_solver, "_solution_at", fake_solution)

    solution = solve_observer_point(
        52.0,
        21.0,
        aircraft_point=object(),
        body=object(),
        max_relocation_km=0.5,
    )

    assert solution.lat == 52.0
    assert solution.lon == 21.0
    assert solution.offset_body_diameters == 1.20
    assert solution.home_offset_body_diameters == 1.20
    assert solution.best_grid_offset_body_diameters == 0.02
    assert solution.selected_from_home is True
    assert solution.reason == "OBSERVER_POINT_TOO_FAR"
