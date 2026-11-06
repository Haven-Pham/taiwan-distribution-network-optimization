"""The fictional company and its cost assumptions, in one place so that they can be seen and varied.

Geography, population and road distances are real. Everything here is an assumption: there are no quotes or company data
behind these values, so results should be read as comparisons and not as budgets.
"""
from dataclasses import dataclass, replace

import numpy as np

# Rent differs by area. These multipliers on a DC's annual cost are assumed, and tested against a flat rent.
RENT_TIERS = {"臺北市": 1.6, "新北市": 1.6, "基隆市": 1.2, "桃園市": 1.2, "新竹市": 1.2, "新竹縣": 1.2, "臺中市": 1.0, "臺南市": 1.0, "高雄市": 1.0}
OTHER_RENT = 0.8
HUB = ("桃園市", "大園區")  # imports and suppliers reach the network here, near the international airport


@dataclass(frozen=True)
class Scenario:
    parcels_per_year: float = 20_000_000
    dc_fixed_cost: float = 60_000_000  # NT$ a year for a DC at rent multiplier 1.0
    dc_capacity: float = 12_000_000  # parcels a year
    handling_per_parcel: float = 10.0  # NT$, the same at every DC
    linehaul_per_parcel_km: float = 0.0133  # NT$: semi-trailer at NT$40/km, 6,000 parcels, empty return
    delivery_per_parcel_km: float = 0.05  # NT$: starting estimate, later calibrated from solved routes
    truck_time_factor: float = 1.25  # routing server times are for cars
    flat_rent: bool = False

    def scaled(self, **changes):
        return replace(self, **changes)


@dataclass(frozen=True)
class Fleet:
    capacity: int = 1500  # parcels a truck
    cost_per_km: float = 30.0  # NT$
    cost_per_hour: float = 350.0  # NT$, driver
    cost_per_day: float = 2500.0  # NT$ for each truck used
    service_hours: float = 0.33  # unloading at each station
    shift_hours: float = 10.0
    deadline_hours: float = 6.0  # trucks leave at 03:00 and must reach each station by 09:00

    @property
    def max_one_way_hours(self):
        """The farthest a station can be: the truck must arrive by the deadline and also return within its shift."""
        return min(self.deadline_hours, (self.shift_hours - self.service_hours) / 2)


def yearly_parcels(population, scenario):
    """Parcels a year by township, in proportion to population."""
    population = np.asarray(population, dtype=float)
    return population / population.sum() * scenario.parcels_per_year


def candidate_sites(table):
    """Candidate DC townships, by a fixed rule so the choice cannot favour any answer.

    The most populous township in every county or city, the next two in each of the six special municipalities, and the hub.
    """
    specials = {"臺北市", "新北市", "桃園市", "臺中市", "臺南市", "高雄市"}
    chosen = set()
    for county, group in table.groupby("county"):
        take = 3 if county in specials else 1
        chosen.update(group.nlargest(take, "population").index.tolist())
    chosen.add(hub_index(table))
    return sorted(chosen)


def hub_index(table):
    match = table.index[(table["county"] == HUB[0]) & (table["town"] == HUB[1])]
    if len(match) != 1:
        raise ValueError("Hub township not found")
    return int(match[0])


def dc_annual_cost(table, sites, scenario):
    """Annual fixed cost of a DC at each candidate site."""
    if scenario.flat_rent:
        return np.full(len(sites), scenario.dc_fixed_cost)
    return np.array([scenario.dc_fixed_cost * RENT_TIERS.get(table["county"].iloc[s], OTHER_RENT) for s in sites])
