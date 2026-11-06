"""Capacitated facility location with single sourcing, solved exactly with OR-Tools CP-SAT."""
from dataclasses import dataclass

import numpy as np
from ortools.sat.python import cp_model

from .scenario import dc_annual_cost, hub_index, yearly_parcels

MONEY_UNIT = 1000  # the solver works in whole NT$ thousand
PARCEL_UNIT = 100  # and in hundreds of parcels


@dataclass
class NetworkSolution:
    status: str
    open_sites: list
    assignment: np.ndarray  # township -> township index of its DC
    fixed_cost: float
    linehaul_cost: float
    delivery_cost: float
    handling_cost: float
    gap_pct: float

    @property
    def total_cost(self):
        return self.fixed_cost + self.linehaul_cost + self.delivery_cost + self.handling_cost


def cost_matrix(table, km, scenario, sites):
    """Annual linehaul and delivery cost of serving each township from each site. Rows are sites."""
    parcels = yearly_parcels(table["population"], scenario)
    hub = hub_index(table)
    linehaul = np.outer(km[hub, sites] * scenario.linehaul_per_parcel_km, parcels)
    delivery = km[np.ix_(sites, np.arange(len(table)))] * scenario.delivery_per_parcel_km * parcels
    return linehaul, delivery


def solve_network(table, km, hours, scenario, sites, n_open=None, max_hours=None, forced=(), closed=(), time_limit=120, workers=8):
    """Choose DCs and assign each township to exactly one, minimising annual cost.

    max_hours, when given, requires every township to be within that truck time of its DC. Returns None if no network
    can satisfy the constraints.
    """
    parcels = yearly_parcels(table["population"], scenario)
    fixed = dc_annual_cost(table, sites, scenario)
    linehaul, delivery = cost_matrix(table, km, scenario, sites)
    combined = np.rint((linehaul + delivery) / MONEY_UNIT).astype(np.int64)
    load = np.rint(parcels / PARCEL_UNIT).astype(np.int64)
    truck_hours = hours * scenario.truck_time_factor

    model = cp_model.CpModel()
    n_sites, n_towns = len(sites), len(table)
    is_open = [model.NewBoolVar(f"open_{i}") for i in range(n_sites)]
    serve = {}
    for j in range(n_towns):
        # Both directions count, because road times differ slightly each way and the truck must also get back.
        options = [i for i in range(n_sites) if max_hours is None or max(truck_hours[sites[i], j], truck_hours[j, sites[i]]) <= max_hours]
        if not options:
            return None
        for i in options:
            serve[i, j] = model.NewBoolVar(f"serve_{i}_{j}")
            model.AddImplication(serve[i, j], is_open[i])
        model.AddExactlyOne(serve[i, j] for i in options)
    capacity = int(scenario.dc_capacity / PARCEL_UNIT)
    for i in range(n_sites):
        model.Add(sum(load[j] * serve[i, j] for j in range(n_towns) if (i, j) in serve) <= capacity * is_open[i])
        if sites[i] in forced:
            model.Add(is_open[i] == 1)
        if sites[i] in closed:
            model.Add(is_open[i] == 0)
    if n_open is not None:
        model.Add(sum(is_open) == n_open)
    model.Minimize(sum(int(round(fixed[i] / MONEY_UNIT)) * is_open[i] for i in range(n_sites))
                   + sum(int(combined[i, j]) * var for (i, j), var in serve.items()))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_workers = workers
    solver.parameters.random_seed = 42
    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None

    opened = [i for i in range(n_sites) if solver.Value(is_open[i])]
    choice = np.zeros(n_towns, dtype=int)
    for (i, j), var in serve.items():
        if solver.Value(var):
            choice[j] = i
    towns = np.arange(n_towns)
    gap = 0.0 if status == cp_model.OPTIMAL else (solver.ObjectiveValue() - solver.BestObjectiveBound()) / max(solver.ObjectiveValue(), 1) * 100
    return NetworkSolution(solver.StatusName(status), [sites[i] for i in opened], np.array(sites)[choice],
                           float(fixed[opened].sum()), float(linehaul[choice, towns].sum()), float(delivery[choice, towns].sum()),
                           float(parcels.sum() * scenario.handling_per_parcel), float(gap))


def service_levels(table, hours, assignment, scenario, limits=(1, 2, 3, 4)):
    """Share of population within each truck time of its assigned DC, and the longest trip."""
    population = table["population"].to_numpy(dtype=float)
    trip = hours[assignment, np.arange(len(table))] * scenario.truck_time_factor
    out = {f"population_within_{h}h_pct": float(population[trip <= h].sum() / population.sum() * 100) for h in limits}
    out["longest_trip_hours"] = float(trip.max())
    out["population_weighted_hours"] = float(np.average(trip, weights=population))
    return out
