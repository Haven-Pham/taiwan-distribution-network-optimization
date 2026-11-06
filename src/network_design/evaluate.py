"""Costs a network by routing real trucks, and feeds that cost back into the network model."""
import numpy as np
import pandas as pd

from . import routing as rt
from .parallel import run_jobs
from .scenario import Fleet, yearly_parcels

DAYS_PER_YEAR = 300
_shared = {}


def _start_worker(km, truck_hours, fleet):
    _shared.update(km=km, hours=truck_hours, fleet=fleet)


def _route_one(job):
    label, depot, stops, demand, seconds, day = job
    plan = rt.solve_routes(depot, stops, demand, _shared["km"], _shared["hours"], _shared["fleet"], time_limit=seconds)
    problems = rt.check_plan(plan, depot, stops, demand, _shared["km"], _shared["hours"], _shared["fleet"])
    parcel_km = float((np.asarray(demand) * _shared["km"][depot, stops]).sum())
    return {"label": label, "dc": depot, "day": day, "stops": len(stops), "parcels": int(np.sum(demand)), "parcel_km": parcel_km, "routes": len(plan.routes),
            "dedicated": plan.dedicated_trips, "dropped": len(plan.dropped), "route_km": plan.km, "route_hours": plan.hours, "route_cost": plan.cost, "problems": len(problems)}


def route_networks(networks, table, km, hours, scenario, fleet=Fleet(), days=3, seconds=5, seed=11, workers=8):
    """Route every DC in each named network on the same simulated days. `networks` maps a label to an assignment array.

    Each sampled day is the same for every network, so differences between networks are not caused by demand noise.
    """
    parcels = yearly_parcels(table["population"], scenario)
    rng = np.random.default_rng(seed)
    day_demand = [rt.daily_demand(parcels, rng, DAYS_PER_YEAR) for _ in range(days)]
    jobs = []
    for label, assignment in networks.items():
        for dc in np.unique(assignment):
            stops = np.flatnonzero(assignment == dc).tolist()
            for day, demand in enumerate(day_demand):
                jobs.append((label, int(dc), stops, demand[stops], seconds, day))
    truck_hours = hours * scenario.truck_time_factor
    return pd.DataFrame(run_jobs(_route_one, jobs, workers, seconds, initializer=_start_worker, initargs=(km, truck_hours, fleet)))


def fit_delivery_rate(records):
    """Least-squares fit of a DC's daily route cost on its parcel-kilometres and its stops, with no intercept.

    The parcel-kilometre rate is what the network model needs, because it is the part of delivery cost that
    depends on which DC serves a township.
    """
    design = records[["parcel_km", "stops"]].to_numpy(dtype=float)
    target = records["route_cost"].to_numpy(dtype=float)
    (rate, per_stop), *_ = np.linalg.lstsq(design, target, rcond=None)
    fitted = design @ np.array([rate, per_stop])
    return {"rate_per_parcel_km": float(rate), "cost_per_stop_day": float(per_stop),
            "r2": float(1 - ((target - fitted) ** 2).sum() / ((target - target.mean()) ** 2).sum()),
            "mape_pct": float(np.mean(np.abs(fitted - target) / target) * 100)}


def annual_route_cost(records):
    """Average daily route cost across sampled days for each network, scaled to a year."""
    daily = records.groupby(["label", "day"])["route_cost"].sum().groupby("label").mean()
    return daily * DAYS_PER_YEAR
