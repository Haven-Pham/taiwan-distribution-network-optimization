"""Builds dashboard/data.json from the experiment results and adds one sample day of routes for the recommended network.

    python scripts/build_dashboard.py
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from network_design import geography as g  # noqa: E402
from network_design import routing as rt  # noqa: E402
from network_design import scenario as sc  # noqa: E402

DC_COUNTS = range(3, 9)  # eight colours can be told apart, which limits the map to eight DCs
TABLES = ROOT / "reports" / "tables"


def main():
    table, km, hours = g.load_geography()
    scenario, fleet = sc.Scenario(), sc.Fleet()
    truck = hours * scenario.truck_time_factor
    stored = json.loads((g.PROCESSED_DIR / "networks.json").read_text(encoding="utf-8"))
    outlines = json.loads((g.PROCESSED_DIR / "township_outlines.json").read_text(encoding="utf-8"))
    frontier = pd.read_csv(TABLES / "network_frontier.csv")
    frontier = frontier[frontier["scenario"] == "Calibrated"].set_index("dcs")

    towns = [{"en": g.english_label(c, e), "zh": f"{c}{t}", "lon": float(lo), "lat": float(la), "pop": int(p)}
             for c, t, e, lo, la, p in zip(table["county"], table["town"], table["town_en"], table["lon"], table["lat"], table["population"])]

    networks, order = {}, []
    for count in DC_COUNTS:
        network = stored["networks"][str(count)]
        assignment = np.array(network["assignment"])
        for site in network["sites"]:
            if site not in order:
                order.append(site)
        row = frontier.loc[count]
        networks[str(count)] = {
            "sites": network["sites"], "assignment": assignment.tolist(), "hours": np.round(truck[assignment, np.arange(len(table))], 2).tolist(),
            "fixed": float(row["fixed_cost"]), "linehaul": float(row["linehaul_cost"]), "delivery": float(row["routed_delivery_cost"]), "handling": float(row["handling_cost"]), "total": float(row["routed_total_cost"]),
            "within2h": float(row["population_within_2h_pct"]), "longest": float(row["longest_trip_hours"]), "trucks": float(row["trucks_per_day"]),
        }

    best = stored["best_dcs"]
    assignment = np.array(stored["networks"][str(best)]["assignment"])
    demand = rt.daily_demand(sc.yearly_parcels(table["population"], scenario), np.random.default_rng(5), 300)
    routes = []
    for dc in stored["networks"][str(best)]["sites"]:
        stops = np.flatnonzero(assignment == dc).tolist()
        plan = rt.solve_routes(dc, stops, demand[stops], km, truck, fleet, time_limit=10)
        problems = rt.check_plan(plan, dc, stops, demand[stops], km, truck, fleet)
        if problems or plan.dropped:
            raise RuntimeError(f"Sample routes are invalid: {problems}, dropped {plan.dropped}")
        routes.extend([int(dc)] + [int(s) for s in route] + [int(dc)] for route in plan.routes)

    payload = {"towns": towns, "outlines": [outlines[code] for code in table["town_code"]], "networks": networks, "best": best, "colourOrder": order, "routes": routes,
               "parcels": scenario.parcels_per_year, "limit": fleet.max_one_way_hours}
    target = ROOT / "dashboard" / "data.json"
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {target.relative_to(ROOT)}: {target.stat().st_size / 1024:.0f} KB, {len(routes)} sample routes")


if __name__ == "__main__":
    main()
