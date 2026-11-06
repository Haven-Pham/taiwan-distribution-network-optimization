"""Daily truck routes from a DC to township delivery stations, solved with the OR-Tools routing library.

A station receiving more than a truckload gets dedicated full trucks, and the remainder joins a multi-stop route.
"""
import math
from dataclasses import dataclass

import numpy as np
from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from .scenario import Fleet

MINUTE = 60  # the solver works in whole seconds
FIRST_SOLUTIONS = {"cheapest_arc": routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC,
                   "savings": routing_enums_pb2.FirstSolutionStrategy.SAVINGS,
                   "christofides": routing_enums_pb2.FirstSolutionStrategy.CHRISTOFIDES}


@dataclass
class RoutePlan:
    routes: list  # each route is a list of matrix indices, depot excluded
    loads: list
    dropped: list
    dedicated_trips: int
    km: float
    hours: float
    cost: float
    method: str


def split_full_truckloads(demand, capacity):
    """Parcels left for shared routes, and the number of dedicated full trucks each stop needs."""
    demand = np.asarray(demand, dtype=int)
    full = demand // capacity
    return demand - full * capacity, full


def route_measures(route, depot, km, hours, service_hours):
    """Distance, hours and the arrival time at each stop for a route that starts and ends at the depot."""
    path = [depot] + list(route) + [depot]
    distance = sum(km[a, b] for a, b in zip(path[:-1], path[1:]))
    clock, arrivals = 0.0, []
    for a, b in zip(path[:-2], path[1:-1]):
        clock += hours[a, b]
        arrivals.append(clock)
        clock += service_hours
    return float(distance), float(clock + hours[path[-2], depot]), arrivals


def route_cost(distance, duration, fleet):
    return distance * fleet.cost_per_km + duration * fleet.cost_per_hour + fleet.cost_per_day


def check_plan(plan, depot, stops, demand, km, hours, fleet):
    """Independent check of a plan: returns a list of broken rules. An empty list means the plan is valid."""
    problems = []
    remainder, _ = split_full_truckloads(demand, fleet.capacity)
    need = {s: int(r) for s, r in zip(stops, remainder) if r > 0}
    visited = [s for route in plan.routes for s in route]
    if len(visited) != len(set(visited)):
        problems.append("a stop is visited twice")
    missing = set(need) - set(visited) - set(plan.dropped)
    if missing:
        problems.append(f"{len(missing)} stops are neither served nor reported as dropped")
    for route in plan.routes:
        load = sum(need.get(s, 0) for s in route)
        if load > fleet.capacity:
            problems.append(f"a route carries {load} parcels")
        _, duration, arrivals = route_measures(route, depot, km, hours, fleet.service_hours)
        if duration > fleet.shift_hours + 1e-6:
            problems.append(f"a route lasts {duration:.2f} hours")
        if arrivals and max(arrivals) > fleet.deadline_hours + 1e-6:
            problems.append(f"a stop is reached after {max(arrivals):.2f} hours")
    _, full = split_full_truckloads(demand, fleet.capacity)
    for stop, trucks in zip(stops, full):
        if trucks and hours[depot, stop] > fleet.deadline_hours + 1e-6:
            problems.append("a dedicated truck arrives after the deadline")
    return problems


def _plan(routes, dropped, depot, stops, demand, full, km, hours, fleet, method):
    """Measure a set of routes, adding the dedicated full-truck round trips."""
    need = dict(zip(stops, demand))
    total_km, total_hours, cost = 0.0, 0.0, 0.0
    for route in routes:
        distance, duration, _ = route_measures(route, depot, km, hours, fleet.service_hours)
        total_km += distance
        total_hours += duration
        cost += route_cost(distance, duration, fleet)
    dedicated = 0
    for stop, trucks in zip(stops, full):
        for _ in range(int(trucks)):
            distance, duration, _ = route_measures([stop], depot, km, hours, fleet.service_hours)
            total_km += distance
            total_hours += duration
            cost += route_cost(distance, duration, fleet)
            dedicated += 1
    loads = [sum(int(need[s]) % fleet.capacity for s in route) for route in routes]
    return RoutePlan(routes, loads, dropped, dedicated, total_km, total_hours, cost, method)


def solve_routes(depot, stops, demand, km, hours, fleet=Fleet(), time_limit=10, first_solution="savings", metaheuristic=True, drop_penalty=10_000_000):
    """Route trucks from the depot to the stops. Stops that no feasible route can reach are reported as dropped.

    Starting with few trucks keeps the search fast, but on long thin routes time binds before capacity, so a low limit
    would force stops to be dropped. Whenever a stop is dropped the limit doubles, until every stop could have its own
    truck. A stop dropped at that point truly cannot be served.
    """
    stops = list(stops)
    remainder, full = split_full_truckloads(demand, fleet.capacity)
    active = [i for i, r in enumerate(remainder) if r > 0]
    if not active:
        return _plan([], [], depot, stops, demand, full, km, hours, fleet, "no shared routes")
    nodes = [depot] + [stops[i] for i in active]
    loads = [0] + [int(remainder[i]) for i in active]
    vehicles = min(len(active), math.ceil(sum(loads) / fleet.capacity) * 2 + 3)
    while True:
        routes, dropped = _search(nodes, loads, vehicles, km, hours, fleet, time_limit, first_solution, metaheuristic, drop_penalty)
        if not dropped or vehicles >= len(active):
            break
        vehicles = min(len(active), vehicles * 2)
    method = f"{first_solution}{' + guided local search' if metaheuristic else ''}"
    return _plan(routes, dropped, depot, stops, demand, full, km, hours, fleet, method)


def _search(nodes, loads, vehicles, km, hours, fleet, time_limit, first_solution, metaheuristic, drop_penalty):
    """One run of the routing solver with a given limit on trucks. nodes[0] is the depot."""
    manager = pywrapcp.RoutingIndexManager(len(nodes), vehicles, 0)
    routing = pywrapcp.RoutingModel(manager)
    node_of = manager.IndexToNode

    def arc_cost(a, b):
        i, j = nodes[node_of(a)], nodes[node_of(b)]
        return int(round(km[i, j] * fleet.cost_per_km + hours[i, j] * fleet.cost_per_hour))

    def arc_time(a, b):
        i, j = node_of(a), node_of(b)
        service = fleet.service_hours if i != 0 else 0.0
        return int(round((hours[nodes[i], nodes[j]] + service) * 3600))

    cost_index = routing.RegisterTransitCallback(arc_cost)
    routing.SetArcCostEvaluatorOfAllVehicles(cost_index)
    routing.SetFixedCostOfAllVehicles(int(fleet.cost_per_day))
    load_index = routing.RegisterUnaryTransitCallback(lambda a: loads[node_of(a)])
    routing.AddDimensionWithVehicleCapacity(load_index, 0, [fleet.capacity] * vehicles, True, "Load")
    time_index = routing.RegisterTransitCallback(arc_time)
    routing.AddDimension(time_index, 0, int(fleet.shift_hours * 3600), True, "Time")
    clock = routing.GetDimensionOrDie("Time")
    for node in range(1, len(nodes)):
        index = manager.NodeToIndex(node)
        clock.CumulVar(index).SetMax(int(fleet.deadline_hours * 3600))
        routing.AddDisjunction([index], drop_penalty)

    parameters = pywrapcp.DefaultRoutingSearchParameters()
    parameters.first_solution_strategy = FIRST_SOLUTIONS[first_solution]
    if metaheuristic:
        parameters.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
        parameters.time_limit.FromSeconds(int(time_limit))
    solution = routing.SolveWithParameters(parameters)
    if solution is None:
        raise RuntimeError("The routing solver found no solution")

    routes, served = [], set()
    for vehicle in range(vehicles):
        index, route = routing.Start(vehicle), []
        while not routing.IsEnd(index):
            if node_of(index) != 0:
                route.append(nodes[node_of(index)])
            index = solution.Value(routing.NextVar(index))
        if route:
            routes.append(route)
            served.update(route)
    return routes, [nodes[n] for n in range(1, len(nodes)) if nodes[n] not in served]


def nearest_neighbour(depot, stops, demand, km, hours, fleet=Fleet()):
    """A planner's rule of thumb: keep driving to the nearest unserved stop that still fits, then start a new truck."""
    stops = list(stops)
    remainder, full = split_full_truckloads(demand, fleet.capacity)
    waiting = {stops[i]: int(remainder[i]) for i in range(len(stops)) if remainder[i] > 0}
    routes, dropped = [], []
    while waiting:
        route, load, here, clock = [], 0, depot, 0.0
        while True:
            options = []
            for stop, parcels in waiting.items():
                arrive = clock + hours[here, stop]
                back = arrive + fleet.service_hours + hours[stop, depot]
                if load + parcels <= fleet.capacity and arrive <= fleet.deadline_hours and back <= fleet.shift_hours:
                    options.append((km[here, stop], stop))
            if not options:
                break
            _, stop = min(options)
            clock += hours[here, stop] + fleet.service_hours
            load += waiting.pop(stop)
            route.append(stop)
            here = stop
        if route:
            routes.append(route)
        else:
            dropped.extend(waiting)
            break
    return _plan(routes, dropped, depot, stops, demand, full, km, hours, fleet, "nearest neighbour")


def daily_demand(yearly, rng, days_per_year=300):
    """One day's parcels by stop: Poisson around the average day."""
    return rng.poisson(np.asarray(yearly, dtype=float) / days_per_year)


def read_cvrplib(path):
    """Read a CVRPLIB instance: coordinates, demands and vehicle capacity."""
    text = open(path, encoding="utf-8").read().replace("\r", "")
    capacity = int(text.split("CAPACITY")[1].split(":")[1].split()[0])
    coordinates, demands = [], []
    section = None
    for line in text.splitlines():
        token = line.strip()
        if token.startswith("NODE_COORD_SECTION"):
            section = "coordinates"
        elif token.startswith("DEMAND_SECTION"):
            section = "demands"
        elif token.startswith("DEPOT_SECTION"):
            section = None
        elif section == "coordinates":
            parts = token.split()
            coordinates.append((float(parts[1]), float(parts[2])))
        elif section == "demands":
            demands.append(int(token.split()[1]))
    return np.array(coordinates), np.array(demands), capacity


def solve_benchmark(coordinates, demands, capacity, time_limit=30):
    """Solve a pure capacitated routing benchmark and return its rounded total distance, as the benchmark defines it."""
    xy = np.asarray(coordinates, dtype=float)
    distance = np.rint(np.hypot(xy[:, None, 0] - xy[None, :, 0], xy[:, None, 1] - xy[None, :, 1])).astype(int)
    n = len(xy)
    vehicles = n - 1
    manager = pywrapcp.RoutingIndexManager(n, vehicles, 0)
    routing = pywrapcp.RoutingModel(manager)
    arc = routing.RegisterTransitCallback(lambda a, b: int(distance[manager.IndexToNode(a), manager.IndexToNode(b)]))
    routing.SetArcCostEvaluatorOfAllVehicles(arc)
    load = routing.RegisterUnaryTransitCallback(lambda a: int(demands[manager.IndexToNode(a)]))
    routing.AddDimensionWithVehicleCapacity(load, 0, [int(capacity)] * vehicles, True, "Load")
    parameters = pywrapcp.DefaultRoutingSearchParameters()
    parameters.first_solution_strategy = FIRST_SOLUTIONS["savings"]
    parameters.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    parameters.time_limit.FromSeconds(int(time_limit))
    solution = routing.SolveWithParameters(parameters)
    return int(solution.ObjectiveValue())
