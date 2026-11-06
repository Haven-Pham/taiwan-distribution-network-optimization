"""Runs every optimisation experiment and saves the results that the notebook reads.

    python scripts/run_experiments.py

It takes about 40 minutes on 8 cores. Results go to reports/tables and data/processed/networks.json. Finished stages are
saved in data/checkpoints, so a stopped run continues where it stopped. Delete that folder to start again.
"""
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from network_design import evaluate as ev  # noqa: E402
from network_design import geography as g  # noqa: E402
from network_design import network as n  # noqa: E402
from network_design import routing as rt  # noqa: E402
from network_design import scenario as sc  # noqa: E402
from network_design.parallel import checkpointed, keep_awake, run_jobs  # noqa: E402

TABLES = ROOT / "reports" / "tables"
CHECKPOINTS = ROOT / "data" / "checkpoints"
BENCHMARKS = {"A-n32-k5": 784, "B-n31-k5": 672, "M-n101-k10": 820, "X-n101-k25": 27591}
K_RANGE = range(2, 10)
EAST_DCS = (("花蓮縣", "花蓮市"), ("臺東縣", "臺東市"))
EAST_COUNTIES = ("花蓮縣", "臺東縣")


def log(message):
    line = f"[{time.strftime('%H:%M:%S')}] {message}"
    print(line, flush=True)
    CHECKPOINTS.mkdir(parents=True, exist_ok=True)
    with open(CHECKPOINTS / "run.log", "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def site_index(table, county, town):
    return int(table.index[(table["county"] == county) & (table["town"] == town)][0])


def names(table, indices):
    return [f"{table['county'].iloc[i]}{table['town'].iloc[i]}" for i in indices]


def network_row(table, solution):
    return {"sites": names(table, solution.open_sites), "fixed_cost": solution.fixed_cost, "linehaul_cost": solution.linehaul_cost,
            "model_delivery_cost": solution.delivery_cost, "handling_cost": solution.handling_cost, "status": solution.status, "gap_pct": solution.gap_pct}


def calibrate(table, km, hours, scenario, sites, fleet, rounds=4):
    """Alternate between choosing networks and routing them until the delivery rate settles."""
    limit = fleet.max_one_way_hours
    rate, history = scenario.delivery_per_parcel_km, []
    for round_number in range(rounds):
        current = scenario.scaled(delivery_per_parcel_km=rate)
        solutions = {k: n.solve_network(table, km, hours, current, sites, n_open=k, max_hours=limit, time_limit=60) for k in K_RANGE}
        solutions = {k: s for k, s in solutions.items() if s is not None}
        if not solutions:
            raise RuntimeError(f"No network meets the {limit:.2f}-hour limit with {K_RANGE.start} to {K_RANGE.stop - 1} DCs")
        records = ev.route_networks({k: s.assignment for k, s in solutions.items()}, table, km, hours, current, fleet, days=2, seconds=4)
        fit = ev.fit_delivery_rate(records)
        history.append({"round": round_number + 1, "rate_used": rate, **fit, "dropped_stops": int(records["dropped"].sum())})
        change = abs(fit["rate_per_parcel_km"] / rate - 1)
        rate = fit["rate_per_parcel_km"]
        if round_number and change < 0.03:
            break
    return rate, history


def frontier(table, km, hours, scenario, sites, fleet, days=5, seconds=6, label=None):
    """The best network for each DC count under the model, then costed by routing trucks on simulated days."""
    limit = fleet.max_one_way_hours
    solutions = {k: n.solve_network(table, km, hours, scenario, sites, n_open=k, max_hours=limit, time_limit=90) for k in K_RANGE}
    solutions = {k: s for k, s in solutions.items() if s is not None}
    records = ev.route_networks({k: s.assignment for k, s in solutions.items()}, table, km, hours, scenario, fleet, days=days, seconds=seconds)
    annual = ev.annual_route_cost(records)
    rows = []
    for k, solution in solutions.items():
        sub = records[records["label"] == k]
        rows.append({"scenario": label, "dcs": k, **network_row(table, solution), "routed_delivery_cost": float(annual[k]),
                     "routed_total_cost": solution.fixed_cost + solution.linehaul_cost + solution.handling_cost + float(annual[k]),
                     "trucks_per_day": float(sub.groupby("day")[["routes", "dedicated"]].sum().sum(axis=1).mean()), "dropped_stops": int(sub["dropped"].sum()), "rule_breaks": int(sub["problems"].sum()),
                     **n.service_levels(table, hours, solution.assignment, scenario)})
    return pd.DataFrame(rows), solutions, records


def east_outsourced(table, km, hours, scenario, fleet):
    """The best network if a carrier takes over Hualien and Taitung counties, as a way to find a break-even carrier price."""
    east = table["county"].isin(EAST_COUNTIES).to_numpy()
    rest = table[~east].reset_index(drop=True)
    keep = np.flatnonzero(~east)
    km_rest, hours_rest = km[np.ix_(keep, keep)], hours[np.ix_(keep, keep)]
    parcels = sc.yearly_parcels(table["population"], scenario)
    variant = scenario.scaled(parcels_per_year=float(parcels[~east].sum()))
    sites = sc.candidate_sites(rest)
    limit = fleet.max_one_way_hours
    solutions = {k: n.solve_network(rest, km_rest, hours_rest, variant, sites, n_open=k, max_hours=limit, time_limit=60) for k in range(2, 6)}
    solutions = {k: s for k, s in solutions.items() if s is not None}
    records = ev.route_networks({k: s.assignment for k, s in solutions.items()}, rest, km_rest, hours_rest, variant, fleet, days=5, seconds=6)
    annual = ev.annual_route_cost(records)
    rows = [{"dcs": k, "sites": " ".join(names(rest, s.open_sites)), "routed_total_cost": s.fixed_cost + s.linehaul_cost + s.handling_cost + float(annual[k]),
             "dropped_stops": int(records[records["label"] == k]["dropped"].sum())} for k, s in solutions.items()]
    return pd.DataFrame(rows), float(parcels[east].sum()), float(table.loc[east, "population"].sum() / table["population"].sum() * 100)


def benchmark_one(job):
    name, seconds = job
    coordinates, demands, capacity = rt.read_cvrplib(ROOT / "tests" / "data" / f"{name}.vrp")
    return {"instance": name, "customers": len(coordinates) - 1, "best_known": BENCHMARKS[name], "seconds": seconds, "found": rt.solve_benchmark(coordinates, demands, capacity, seconds)}


def compare_one(job):
    depot, stops, demand, seconds, day, km, truck_hours, fleet = job
    rows = []
    for method, plan in (("Nearest neighbour", rt.nearest_neighbour(depot, stops, demand, km, truck_hours, fleet)),
                         ("Savings + local search", rt.solve_routes(depot, stops, demand, km, truck_hours, fleet, metaheuristic=False)),
                         ("Savings + guided local search", rt.solve_routes(depot, stops, demand, km, truck_hours, fleet, time_limit=seconds))):
        rows.append({"dc": depot, "day": day, "method": method, "cost": plan.cost, "km": plan.km, "hours": plan.hours, "trucks": len(plan.routes) + plan.dedicated_trips,
                     "dropped": len(plan.dropped), "rule_breaks": len(rt.check_plan(plan, depot, stops, demand, km, truck_hours, fleet))})
    return rows


def convergence_one(job):
    depot, stops, demand, seconds, km, truck_hours, fleet = job
    plan = rt.solve_routes(depot, stops, demand, km, truck_hours, fleet, time_limit=seconds)
    return {"seconds": seconds, "cost": plan.cost, "trucks": len(plan.routes) + plan.dedicated_trips}


def main():
    keep_awake()
    TABLES.mkdir(parents=True, exist_ok=True)
    table, km, hours = g.load_geography()
    base, fleet = sc.Scenario(), sc.Fleet()
    sites = sc.candidate_sites(table)
    truck_hours = hours * base.truck_time_factor
    limit = fleet.max_one_way_hours
    log(f"{len(table)} townships, {len(sites)} candidate sites, one-way limit {limit:.2f} truck hours")

    log("Benchmarks")
    bench = pd.DataFrame(checkpointed(CHECKPOINTS, "benchmarks", lambda: run_jobs(benchmark_one, [(name, s) for name in BENCHMARKS for s in (5, 30)], seconds_per_job=30)))
    bench["gap_pct"] = (bench["found"] / bench["best_known"] - 1) * 100
    bench.round(2).to_csv(TABLES / "solver_benchmarks.csv", index=False)
    log(bench.to_string())

    log("Calibration")
    rate, history = checkpointed(CHECKPOINTS, "calibration", lambda: calibrate(table, km, hours, base, sites, fleet))
    pd.DataFrame(history).round(4).to_csv(TABLES / "calibration_history.csv", index=False)
    log(pd.DataFrame(history).round(4).to_string())
    calibrated = base.scaled(delivery_per_parcel_km=rate)

    log("Frontiers before and after calibration")
    before, before_solutions, _ = checkpointed(CHECKPOINTS, "frontier_before", lambda: frontier(table, km, hours, base, sites, fleet, label="First estimate"))
    after, after_solutions, after_records = checkpointed(CHECKPOINTS, "frontier_after", lambda: frontier(table, km, hours, calibrated, sites, fleet, label="Calibrated"))
    results = pd.concat([before, after], ignore_index=True)
    results.assign(sites=results["sites"].map(" ".join)).round(2).to_csv(TABLES / "network_frontier.csv", index=False)
    log(results.drop(columns=["sites"]).round(1).to_string())
    clean = after[after["dropped_stops"] == 0] if (after["dropped_stops"] == 0).any() else after
    best = clean.loc[clean["routed_total_cost"].idxmin()]
    best_k = int(best["dcs"])
    chosen = after_solutions[best_k]
    log(f"Best network: {best_k} DCs {best['sites']}, NT${best['routed_total_cost'] / 1e6:.1f}M")

    # What the first estimate would have chosen, under the model that used it
    model_pick = min(before_solutions.values(), key=lambda s: s.total_cost)
    pick = {"chosen_by_first_estimate_dcs": len(model_pick.open_sites), "sites": names(table, model_pick.open_sites),
            "routed_total_cost": float(before.loc[before["dcs"] == len(model_pick.open_sites), "routed_total_cost"].iloc[0]),
            "best_dcs": best_k, "best_routed_total_cost": float(best["routed_total_cost"])}
    log(pick)

    log("Alternatives a planner might propose")
    alternatives = {}
    for label, forced in {"North + south": [("桃園市", "桃園區"), ("高雄市", "鳳山區")],
                          "North + centre + south": [("桃園市", "桃園區"), ("臺中市", "西屯區"), ("高雄市", "鳳山區")],
                          "Rule: largest metros": [("新北市", "板橋區"), ("臺中市", "北屯區"), ("高雄市", "三民區")]}.items():
        indices = [site_index(table, *place) for place in forced]
        for extra in range(0, 4):
            solution = n.solve_network(table, km, hours, calibrated, sites, n_open=len(indices) + extra, max_hours=limit, forced=indices, time_limit=60)
            if solution is not None:
                alternatives[label] = solution
                break
    records = checkpointed(CHECKPOINTS, "alternatives", lambda: ev.route_networks({"Optimised": chosen.assignment, **{k: s.assignment for k, s in alternatives.items()}}, table, km, hours, calibrated, fleet, days=5, seconds=6))
    annual = ev.annual_route_cost(records)
    rows = []
    for label, solution in {"Optimised": chosen, **alternatives}.items():
        rows.append({"network": label, "dcs": len(solution.open_sites), "sites": " ".join(names(table, solution.open_sites)), "fixed_cost": solution.fixed_cost, "linehaul_cost": solution.linehaul_cost,
                     "routed_delivery_cost": float(annual[label]), "routed_total_cost": solution.fixed_cost + solution.linehaul_cost + solution.handling_cost + float(annual[label]),
                     "dropped_stops": int(records[records["label"] == label]["dropped"].sum())})
    alternatives_table = pd.DataFrame(rows)
    alternatives_table.round(0).to_csv(TABLES / "alternative_networks.csv", index=False)
    log(alternatives_table.drop(columns="sites").round(0).to_string())

    log("The east coast")
    east = [site_index(table, *place) for place in EAST_DCS]
    rows = []
    for hours_limit in (limit, 5.5, 6.0, 7.0, 8.0):
        solution = n.solve_network(table, km, hours, calibrated, sites, max_hours=hours_limit, closed=east, time_limit=60)
        rows.append({"max_truck_hours": round(hours_limit, 2), "feasible_without_east_dc": solution is not None, "total_model_cost": solution.total_cost if solution else None, "dcs": len(solution.open_sites) if solution else None})
    with_east = n.solve_network(table, km, hours, calibrated, sites, max_hours=limit, time_limit=60)
    pd.DataFrame(rows).to_csv(TABLES / "east_coast_without_dc.csv", index=False)
    log(pd.DataFrame(rows).to_string())
    log(f"Model cost with a free choice of DCs at the limit: {with_east.total_cost / 1e6:.1f}M, sites {names(table, with_east.open_sites)}")

    outsourced, east_parcels, east_share = checkpointed(CHECKPOINTS, "east_outsourced", lambda: east_outsourced(table, km, hours, calibrated, fleet))
    west_best = outsourced.loc[outsourced["routed_total_cost"].idxmin()]
    in_house_total = float(alternatives_table.loc[alternatives_table["network"] == "Optimised", "routed_total_cost"].iloc[0])
    outsourced_row = {"east_population_share_pct": east_share, "east_parcels_per_year": east_parcels, "in_house_total_cost": in_house_total, "west_only_dcs": int(west_best["dcs"]), "west_only_sites": west_best["sites"],
                      "west_only_total_cost": float(west_best["routed_total_cost"]), "west_only_dropped_stops": int(west_best["dropped_stops"]),
                      "saving_before_carrier_fees": in_house_total - float(west_best["routed_total_cost"]), "break_even_carrier_price_per_parcel": (in_house_total - float(west_best["routed_total_cost"])) / east_parcels}
    pd.DataFrame([outsourced_row]).round(2).to_csv(TABLES / "east_coast_outsourcing.csv", index=False)
    log(outsourced_row)

    cost_rows = []
    best_records = after_records[after_records["label"] == best_k]
    daily = best_records.groupby("dc")[["route_cost", "parcels", "stops", "route_km"]].mean()
    population = table["population"].to_numpy(dtype=float)
    fixed = dict(zip(sites, sc.dc_annual_cost(table, sites, calibrated)))
    parcels = sc.yearly_parcels(population, calibrated)
    hub = sc.hub_index(table)
    for dc, row in daily.iterrows():
        served = chosen.assignment == dc
        yearly = parcels[served].sum()
        cost_rows.append({"dc": names(table, [dc])[0], "townships": int(served.sum()), "population_share_pct": population[served].sum() / population.sum() * 100, "parcels_per_year": yearly, "utilisation_pct": yearly / calibrated.dc_capacity * 100,
                          "fixed_per_parcel": fixed[dc] / yearly, "linehaul_per_parcel": km[hub, dc] * calibrated.linehaul_per_parcel_km, "routes_per_parcel": row["route_cost"] * ev.DAYS_PER_YEAR / yearly,
                          "total_per_parcel": fixed[dc] / yearly + km[hub, dc] * calibrated.linehaul_per_parcel_km + row["route_cost"] * ev.DAYS_PER_YEAR / yearly + calibrated.handling_per_parcel})
    pd.DataFrame(cost_rows).round(2).to_csv(TABLES / "cost_to_serve_by_dc.csv", index=False)
    log(pd.DataFrame(cost_rows).round(2).to_string())

    log("Resilience: losing one DC")
    rows = []
    for dc in chosen.open_sites:
        remaining = [s for s in chosen.open_sites if s != dc]
        kept = n.solve_network(table, km, hours, calibrated, remaining, n_open=len(remaining), max_hours=limit, time_limit=30) if len(remaining) else None
        relaxed = kept or n.solve_network(table, km, hours, calibrated, remaining, n_open=len(remaining), time_limit=30)
        row = {"lost_dc": names(table, [dc])[0], "meets_time_limit": kept is not None, "capacity_ok": relaxed is not None}
        if relaxed is not None:
            trip = truck_hours[relaxed.assignment, np.arange(len(table))]
            late = trip > limit
            row.update({"extra_transport_cost_pct": ((relaxed.linehaul_cost + relaxed.delivery_cost) / (chosen.linehaul_cost + chosen.delivery_cost) - 1) * 100,
                        "population_beyond_limit_pct": population[late].sum() / population.sum() * 100, "longest_trip_hours": float(trip.max()),
                        "busiest_dc_utilisation_pct": max(parcels[relaxed.assignment == s].sum() for s in remaining) / calibrated.dc_capacity * 100})
        rows.append(row)
    pd.DataFrame(rows).round(1).to_csv(TABLES / "resilience_one_dc_lost.csv", index=False)
    log(pd.DataFrame(rows).round(1).to_string())

    log("Routing methods compared on the chosen network")
    rng = np.random.default_rng(23)
    jobs = []
    for day in range(5):
        demand = rt.daily_demand(parcels, rng, ev.DAYS_PER_YEAR)
        for dc in chosen.open_sites:
            stops = np.flatnonzero(chosen.assignment == dc).tolist()
            jobs.append((dc, stops, demand[stops], 15, day, km, truck_hours, fleet))
    comparison = pd.DataFrame([row for rows in checkpointed(CHECKPOINTS, "routing_methods", lambda: run_jobs(compare_one, jobs, seconds_per_job=20)) for row in rows])
    comparison["method"] = comparison["method"].replace({"Savings heuristic": "Savings + local search", "OR-Tools guided local search": "Savings + guided local search"})
    comparison.round(1).to_csv(TABLES / "routing_method_days.csv", index=False)
    summary = comparison.groupby(["method", "day"])[["cost", "km", "hours", "trucks", "dropped", "rule_breaks"]].sum().groupby("method").mean()
    reference = summary.loc["Nearest neighbour", "cost"]
    summary["saving_vs_nearest_pct"] = (1 - summary["cost"] / reference) * 100
    summary["annual_saving_vs_nearest"] = (reference - summary["cost"]) * ev.DAYS_PER_YEAR
    summary.round(1).to_csv(TABLES / "routing_methods.csv")
    log(summary.round(1).to_string())

    largest = max(chosen.open_sites, key=lambda s: (chosen.assignment == s).sum())
    stops = np.flatnonzero(chosen.assignment == largest).tolist()
    demand = rt.daily_demand(parcels, np.random.default_rng(5), ev.DAYS_PER_YEAR)[stops]
    curve = pd.DataFrame(checkpointed(CHECKPOINTS, "routing_convergence", lambda: run_jobs(convergence_one, [(largest, stops, demand, s, km, truck_hours, fleet) for s in (1, 2, 5, 10, 30, 60)], workers=6, seconds_per_job=60)))
    curve["dc"] = names(table, [largest])[0]
    curve["stops"] = len(stops)
    curve.round(0).to_csv(TABLES / "routing_convergence.csv", index=False)
    log(curve.round(0).to_string())

    log("Sensitivity")
    cases = {"Demand 10M parcels": (dict(parcels_per_year=10_000_000), {}), "Demand 40M parcels": (dict(parcels_per_year=40_000_000, dc_capacity=24_000_000), {}),
             "DC cost +30%": (dict(dc_fixed_cost=78_000_000), {}), "DC cost -30%": (dict(dc_fixed_cost=42_000_000), {}), "Flat rent": (dict(flat_rent=True), {}),
             "Fuel and transport +30%": (dict(linehaul_per_parcel_km=base.linehaul_per_parcel_km * 1.3), dict(cost_per_km=fleet.cost_per_km * 1.3)),
             "Smaller trucks (1,000 parcels)": ({}, dict(capacity=1000)), "Later deadline (10:00)": ({}, dict(deadline_hours=7.0, shift_hours=12.0))}
    rows = []
    for case_number, (label, (scenario_changes, fleet_changes)) in enumerate(cases.items()):
        variant = base.scaled(**scenario_changes)
        variant_fleet = replace(fleet, **fleet_changes)
        def run_case(variant=variant, variant_fleet=variant_fleet, label=label):
            variant_rate, _ = calibrate(table, km, hours, variant.scaled(delivery_per_parcel_km=rate), sites, variant_fleet, rounds=2)
            frame, _, _ = frontier(table, km, hours, variant.scaled(delivery_per_parcel_km=variant_rate), sites, variant_fleet, days=2, seconds=4, label=label)
            return variant_rate, frame

        variant_rate, frame = checkpointed(CHECKPOINTS, f"sensitivity_{case_number}", run_case)
        variant = variant.scaled(delivery_per_parcel_km=variant_rate)
        usable = frame[frame["dropped_stops"] == 0] if (frame["dropped_stops"] == 0).any() else frame
        pick_row = usable.loc[usable["routed_total_cost"].idxmin()]
        rows.append({"case": label, "rate_per_parcel_km": variant_rate, "best_dcs": int(pick_row["dcs"]), "sites": " ".join(pick_row["sites"]), "routed_total_cost": pick_row["routed_total_cost"],
                     "cost_per_parcel": pick_row["routed_total_cost"] / variant.parcels_per_year, "dropped_stops": int(pick_row["dropped_stops"]),
                     "next_best_gap_pct": (usable["routed_total_cost"].nsmallest(2).iloc[-1] / pick_row["routed_total_cost"] - 1) * 100})
        log(rows[-1])
    pd.DataFrame(rows).round(3).to_csv(TABLES / "sensitivity.csv", index=False)

    payload = {"rate": rate, "best_dcs": best_k, "first_estimate_pick": pick,
               "networks": {str(k): {"sites": [int(i) for i in s.open_sites], "assignment": [int(i) for i in s.assignment]} for k, s in after_solutions.items()}}
    (g.PROCESSED_DIR / "networks.json").write_text(json.dumps(payload), encoding="utf-8")
    # Tables are written with official names during the run, and published with English ones.
    labels = {f"{c}{t}": g.english_label(c, e) for c, t, e in zip(table["county"], table["town"], table["town_en"])}
    for path in TABLES.glob("*.csv"):
        text = path.read_text(encoding="utf-8")
        for official in sorted(labels, key=len, reverse=True):
            text = text.replace(official + " ", labels[official] + "; ").replace(official, labels[official])
        path.write_text(text, encoding="utf-8")
    log("Done")


if __name__ == "__main__":
    main()
