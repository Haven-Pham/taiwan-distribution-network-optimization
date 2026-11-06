import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from network_design import evaluate as ev  # noqa: E402
from network_design import geography as g  # noqa: E402
from network_design import network as n  # noqa: E402
from network_design import parallel  # noqa: E402
from network_design import routing as rt  # noqa: E402
from network_design import scenario as sc  # noqa: E402


def line_world(positions=(0, 10, 20, 100, 110, 120), population=1000):
    """Townships along a straight road. The first is the hub, so linehaul distances are easy to read."""
    positions = np.asarray(positions, dtype=float)
    table = pd.DataFrame({"county": ["桃園市"] + ["甲"] * (len(positions) - 1), "town": ["大園區"] + [f"T{i}" for i in range(1, len(positions))], "population": population})
    km = np.abs(positions[:, None] - positions[None, :])
    return table, km, km / 50


CHEAP = sc.Scenario(parcels_per_year=600_000, dc_fixed_cost=2_000_000, dc_capacity=10_000_000, delivery_per_parcel_km=1.0, truck_time_factor=1.0, flat_rent=True)


def test_two_clusters_get_one_dc_each():
    table, km, hours = line_world()
    solution = n.solve_network(table, km, hours, CHEAP, sites=list(range(6)), time_limit=10)
    assert solution.status == "OPTIMAL"
    assert sorted(solution.open_sites) == [1, 4]
    assert solution.assignment.tolist() == [1, 1, 1, 4, 4, 4]


def test_network_cost_matches_a_hand_calculation():
    table, km, hours = line_world()
    solution = n.solve_network(table, km, hours, CHEAP, sites=[1, 4], n_open=2, time_limit=10)
    # 100,000 parcels a township. Each cluster's outer towns are 10 km from its DC.
    assert solution.delivery_cost == pytest.approx(100_000 * 40 * 1.0)
    assert solution.fixed_cost == pytest.approx(4_000_000)
    assert solution.linehaul_cost == pytest.approx((10 * 3 + 110 * 3) * 100_000 * CHEAP.linehaul_per_parcel_km)
    assert solution.handling_cost == pytest.approx(600_000 * CHEAP.handling_per_parcel)


def test_exact_solution_beats_every_alternative():
    table, km, hours = line_world((0, 7, 19, 33, 58, 90, 131))
    scenario = replace(CHEAP, parcels_per_year=700_000, dc_fixed_cost=2_000_000)
    best = n.solve_network(table, km, hours, scenario, sites=list(range(7)), time_limit=20)
    for first in range(7):
        for second in range(first + 1, 7):
            other = n.solve_network(table, km, hours, scenario, sites=[first, second], n_open=2, time_limit=5)
            assert best.total_cost <= other.total_cost + 1000  # the solver rounds to NT$ thousand


def test_capacity_forces_a_second_dc():
    table, km, hours = line_world((0, 1, 2, 3, 4, 5))
    assert len(n.solve_network(table, km, hours, CHEAP, sites=list(range(6)), time_limit=10).open_sites) == 1
    tight = replace(CHEAP, dc_capacity=400_000)
    solution = n.solve_network(table, km, hours, tight, sites=list(range(6)), time_limit=10)
    assert len(solution.open_sites) == 2
    parcels = sc.yearly_parcels(table["population"], tight)
    for site in solution.open_sites:
        assert parcels[solution.assignment == site].sum() <= tight.dc_capacity


def test_time_limit_is_respected_or_reported_as_infeasible():
    table, km, hours = line_world()
    solution = n.solve_network(table, km, hours, CHEAP, sites=[1, 4], max_hours=0.25, time_limit=10)
    assert (hours[solution.assignment, np.arange(6)] <= 0.25).all()
    assert n.solve_network(table, km, hours, CHEAP, sites=[1], max_hours=0.25, time_limit=10) is None


def test_forced_and_closed_sites():
    table, km, hours = line_world()
    assert 5 in n.solve_network(table, km, hours, CHEAP, sites=list(range(6)), forced=[5], time_limit=10).open_sites
    assert 1 not in n.solve_network(table, km, hours, CHEAP, sites=list(range(6)), closed=[1], time_limit=10).open_sites


def test_service_levels_weight_by_population():
    table, km, hours = line_world()
    table["population"] = [1, 1, 1, 1, 1, 95]
    out = n.service_levels(table, hours, np.array([0, 0, 0, 0, 0, 0]), replace(CHEAP), limits=(1, 3))
    assert out["population_within_1h_pct"] == pytest.approx(3)
    assert out["population_within_3h_pct"] == pytest.approx(100)


def routing_world():
    """A depot at the origin and stops on a grid. Distances are km and trucks run at 50 km/h."""
    xy = np.array([(0, 0), (10, 0), (20, 0), (0, 10), (0, 20), (-10, 0), (-20, 0), (0, -10), (0, -20)], dtype=float)
    km = np.hypot(xy[:, None, 0] - xy[None, :, 0], xy[:, None, 1] - xy[None, :, 1])
    return km, km / 50


def test_routes_are_valid_and_visit_everything():
    km, hours = routing_world()
    fleet = sc.Fleet(capacity=100)
    stops, demand = list(range(1, 9)), [40] * 8
    plan = rt.solve_routes(0, stops, demand, km, hours, fleet, time_limit=1)
    assert rt.check_plan(plan, 0, stops, demand, km, hours, fleet) == []
    assert sorted(s for route in plan.routes for s in route) == stops
    assert len(plan.routes) == 4  # two stops fit on a truck, and each arm of the cross is one route


def test_optimised_routes_are_no_worse_than_the_rule_of_thumb():
    km, hours = routing_world()
    fleet = sc.Fleet(capacity=100)
    stops, demand = list(range(1, 9)), [40] * 8
    assert rt.solve_routes(0, stops, demand, km, hours, fleet, time_limit=1).cost <= rt.nearest_neighbour(0, stops, demand, km, hours, fleet).cost + 1


def test_time_can_bind_before_capacity_without_dropping_stops():
    # Eight far stops in different directions fit easily on one truck by load, but each needs its own by time.
    angles = np.linspace(0, 2 * np.pi, 8, endpoint=False)
    xy = np.vstack([[0.0, 0.0], np.column_stack([np.cos(angles), np.sin(angles)]) * 200])
    km = np.hypot(xy[:, None, 0] - xy[None, :, 0], xy[:, None, 1] - xy[None, :, 1])
    fleet = sc.Fleet(capacity=1000)
    stops, demand = list(range(1, 9)), [10] * 8
    plan = rt.solve_routes(0, stops, demand, km, km / 50, fleet, time_limit=1)
    assert plan.dropped == []
    assert len(plan.routes) == 8
    assert rt.check_plan(plan, 0, stops, demand, km, km / 50, fleet) == []


def test_big_stop_gets_dedicated_trucks_and_the_remainder_is_routed():
    km, hours = routing_world()
    fleet = sc.Fleet(capacity=100)
    plan = rt.solve_routes(0, [1], [250], km, hours, fleet, time_limit=1)
    assert plan.dedicated_trips == 2
    assert plan.routes == [[1]] and plan.loads == [50]


def test_unreachable_stop_is_reported_and_not_silently_lost():
    km = np.array([[0.0, 10, 2000], [10, 0, 2000], [2000, 2000, 0]])
    hours = km / 50
    fleet = sc.Fleet(capacity=100)
    for plan in (rt.solve_routes(0, [1, 2], [10, 10], km, hours, fleet, time_limit=1), rt.nearest_neighbour(0, [1, 2], [10, 10], km, hours, fleet)):
        assert plan.dropped == [2]
        assert rt.check_plan(plan, 0, [1, 2], [10, 10], km, hours, fleet) == []


def test_checker_catches_broken_plans():
    km, hours = routing_world()
    fleet = sc.Fleet(capacity=50)
    overloaded = rt.RoutePlan([[1, 2]], [80], [], 0, 0, 0, 0, "bad")
    assert any("carries" in p for p in rt.check_plan(overloaded, 0, [1, 2], [40, 40], km, hours, fleet))
    twice = rt.RoutePlan([[1], [1]], [40, 40], [], 0, 0, 0, 0, "bad")
    assert any("twice" in p for p in rt.check_plan(twice, 0, [1, 2], [40, 40], km, hours, fleet))
    assert any("neither" in p for p in rt.check_plan(twice, 0, [1, 2], [40, 40], km, hours, fleet))
    slow = sc.Fleet(capacity=100, shift_hours=0.1)
    assert any("lasts" in p for p in rt.check_plan(rt.RoutePlan([[2]], [10], [], 0, 0, 0, 0, "bad"), 0, [2], [10], km, hours, slow))
    late = sc.Fleet(capacity=100, deadline_hours=0.1)
    assert any("after" in p for p in rt.check_plan(rt.RoutePlan([[2]], [10], [], 0, 0, 0, 0, "bad"), 0, [2], [10], km, hours, late))


def test_route_measures_and_cost():
    km = np.array([[0.0, 50, 100], [50, 0, 50], [100, 50, 0]])
    distance, duration, arrivals = rt.route_measures([1, 2], 0, km, km / 50, service_hours=0.5)
    assert distance == 200
    assert arrivals == [1.0, 2.5]
    assert duration == 5.0
    fleet = sc.Fleet(cost_per_km=2, cost_per_hour=10, cost_per_day=100)
    assert rt.route_cost(distance, duration, fleet) == 550


def test_one_way_limit_covers_the_return_trip():
    assert sc.Fleet(deadline_hours=6, shift_hours=10, service_hours=0.5).max_one_way_hours == 4.75
    assert sc.Fleet(deadline_hours=3, shift_hours=10, service_hours=0.5).max_one_way_hours == 3


def test_solver_reaches_a_known_optimum():
    coordinates, demands, capacity = rt.read_cvrplib(ROOT / "tests" / "data" / "A-n32-k5.vrp")
    assert len(coordinates) == 32 and capacity == 100 and demands[0] == 0
    assert rt.solve_benchmark(coordinates, demands, capacity, time_limit=5) <= 784 * 1.02


def test_delivery_rate_fit_recovers_known_values():
    records = pd.DataFrame({"parcel_km": [1000.0, 2000, 3000, 500], "stops": [10, 5, 20, 40]})
    records["route_cost"] = 0.2 * records["parcel_km"] + 7 * records["stops"]
    fit = ev.fit_delivery_rate(records)
    assert fit["rate_per_parcel_km"] == pytest.approx(0.2)
    assert fit["cost_per_stop_day"] == pytest.approx(7)
    assert fit["r2"] == pytest.approx(1)


def test_annual_route_cost_averages_days_and_sums_dcs():
    records = pd.DataFrame({"label": [2, 2, 2, 2], "day": [0, 0, 1, 1], "route_cost": [10.0, 20, 30, 40]})
    assert ev.annual_route_cost(records)[2] == 50 * ev.DAYS_PER_YEAR


def _double(value):
    return value * 2


def _hang_on_first_try(job):
    flag, value = job
    if value == 1 and not Path(flag).exists():
        Path(flag).write_text("tried")
        import time

        time.sleep(600)
    return value * 2


def test_run_jobs_keeps_order():
    assert parallel.run_jobs(_double, [3, 1, 2], workers=2, seconds_per_job=5) == [6, 2, 4]


def test_run_jobs_recovers_from_a_hung_worker(tmp_path, monkeypatch):
    flag = str(tmp_path / "flag")
    jobs = [(flag, 0), (flag, 1), (flag, 2)]
    original = parallel.as_completed
    monkeypatch.setattr(parallel, "as_completed", lambda futures, timeout: original(futures, timeout=20))
    assert parallel.run_jobs(_hang_on_first_try, jobs, workers=2, seconds_per_job=1) == [0, 2, 4]


def test_checkpoint_returns_the_saved_value_without_computing_again(tmp_path):
    calls = []
    assert parallel.checkpointed(tmp_path, "x", lambda: calls.append(1) or 5) == 5
    assert parallel.checkpointed(tmp_path, "x", lambda: calls.append(1) or 6) == 5
    assert len(calls) == 1


def test_latest_resource_uses_the_date_and_not_the_list_order():
    items = [{"resourceDescription": "11509村里"}, {"resourceDescription": "10806村里"}, {"resourceDescription": "無日期"}]
    month, item = g.latest_resource(items)
    assert month == 11509 and item is items[0]


def test_ring_centroid_of_a_square():
    area, x, y = g.ring_area_centroid([(0, 0), (2, 0), (2, 2), (0, 2), (0, 0)])
    assert abs(area) == 4 and (x, y) == (1, 1)


def test_representative_point_is_a_real_village_near_the_people():
    assert g.representative_point([0.0, 1.0, 10.0], [0.0, 0.0, 0.0], [1, 1000, 1]) == 1


def test_representative_point_handles_no_population():
    assert g.representative_point([0.0, 1.0, 2.0], [0.0, 0.0, 0.0], [0, 0, 0]) == 1


def test_accessible_village_prefers_a_central_village_near_a_road():
    lon, lat, population = [0.0, 1.0, 5.0], [0.0, 0.0, 0.0], [10, 10, 10]
    fast = [40, 40, 40]
    # The middle village is the most central, but it is 2 km from a road.
    assert g.accessible_village(lon, lat, population, [100, 2000, 100], fast) == 0
    assert g.accessible_village(lon, lat, population, [100, 100, 100], fast) == 1


def test_accessible_village_rejects_a_slow_track_even_when_near_a_road():
    assert g.accessible_village([0.0, 1.0, 5.0], [0.0, 0.0, 0.0], [10, 10, 10], [100, 100, 100], [40, 11, 40]) == 0


def test_accessible_village_falls_back_on_the_fastest_option():
    assert g.accessible_village([0.0, 1.0], [0.0, 0.0], [5, 5], [100, 100], [11, 15]) == 1
    assert g.accessible_village([0.0, 1.0], [0.0, 0.0], [5, 5], [3000, 900], [30, 25]) == 0


def test_english_label_separates_districts_that_share_a_name():
    assert g.english_label("嘉義市", "West District") == "Chiayi City West District"
    assert g.english_label("彰化縣", "Changhua City") == "Changhua City"


def test_every_county_has_an_english_name():
    table, _, _ = g.load_geography()
    assert set(table["county"]) <= set(g.COUNTY_EN)


def test_offshore_rule():
    assert g.is_offshore("澎湖縣", "馬公市") and g.is_offshore("臺東縣", "蘭嶼鄉")
    assert not g.is_offshore("臺東縣", "臺東市")


def test_simplify_line_keeps_ends_and_removes_collinear_points():
    out = g.simplify_line([(0, 0), (1, 0), (2, 0), (3, 1)], tolerance=0.01)
    assert out[0].tolist() == [0, 0] and out[-1].tolist() == [3, 1]
    assert [1, 0] not in out.tolist()


def test_candidate_rule_is_deterministic_and_includes_the_hub():
    table = pd.DataFrame({"county": ["桃園市", "桃園市", "桃園市", "桃園市", "嘉義市", "嘉義市"], "town": ["大園區", "A", "B", "C", "東區", "西區"], "population": [1, 50, 40, 30, 20, 10]})
    assert sc.candidate_sites(table) == [0, 1, 2, 3, 4]


def test_committed_geography_is_consistent():
    table, km, hours = g.load_geography()
    assert len(table) == 349 and table["town_code"].is_unique
    assert km.shape == (349, 349) and not np.isnan(km).any() and not np.isnan(hours).any()
    assert (np.diag(km) == 0).all()
    assert table["population"].sum() > 22_000_000
    assert table["county"].nunique() == 19
    assert not table["county"].isin(g.OFFSHORE_COUNTIES).any()
