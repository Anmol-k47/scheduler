"""
models.py — Pure data classes for the bus charging scheduler.

Nothing here knows about scheduling logic; it only describes the world.
This keeps the data model easy to extend without touching the engine.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Dict, Optional


# ---------------------------------------------------------------------------
# Route / world description (loaded from scenario JSON)
# ---------------------------------------------------------------------------

@dataclass
class Segment:
    """One road segment between two named stops."""
    from_stop: str
    to_stop: str
    distance_km: float


@dataclass
class StationConfig:
    """Physical configuration of one charging station."""
    name: str
    chargers: int = 1           # easy to extend: just set chargers > 1


@dataclass
class Physics:
    battery_range_km: float     # max km on a full charge
    charge_duration_min: float  # always charges to full, takes this long
    speed_kmh: float            # uniform speed for all buses


@dataclass
class Weights:
    """
    Tunable objective weights.  One obvious place to change a value.
    Add a new weight here + one term in cost.py — nothing else changes.
    """
    individual: float = 1.0   # penalise a single bus waiting too long
    operator: float = 1.0     # penalise an operator's fleet running unevenly
    overall: float = 1.0      # penalise total network delay


@dataclass
class Route:
    segments: List[Segment]
    scheduling_stations: List[str]   # ordered Bengaluru→Kochi; subset of segment stops
    origins: List[str]               # endpoints (Bengaluru, Kochi) — no scheduling here

    def stop_names(self) -> List[str]:
        """All stops in Bengaluru→Kochi order."""
        return [self.segments[0].from_stop] + [s.to_stop for s in self.segments]

    def distance_between(self, a: str, b: str) -> float:
        """
        Cumulative physical distance from stop a to stop b.
        Works in BOTH directions — always returns a positive value.
        """
        stops = self.stop_names()
        ia, ib = stops.index(a), stops.index(b)
        if ia <= ib:
            return sum(self.segments[i].distance_km for i in range(ia, ib))
        else:
            # reverse direction: sum segments from ib to ia
            return sum(self.segments[i].distance_km for i in range(ib, ia))

    def travel_time_between(self, a: str, b: str, speed_kmh: float) -> float:
        """Travel time in minutes from a to b at given speed."""
        return self.distance_between(a, b) / speed_kmh * 60


# ---------------------------------------------------------------------------
# Bus input (from scenario file)
# ---------------------------------------------------------------------------

@dataclass
class BusInput:
    """Raw bus data from scenario — what the operator provides."""
    id: str
    operator: str
    direction: str              # "BK" = Bengaluru→Kochi, "KB" = Kochi→Bengaluru
    departure_time_min: float   # minutes since midnight

    @property
    def origin(self) -> str:
        return "Bengaluru" if self.direction == "BK" else "Kochi"

    @property
    def destination(self) -> str:
        return "Kochi" if self.direction == "BK" else "Bengaluru"

    def stops_in_order(self, route: Route) -> List[str]:
        """All stops this bus will pass through, in travel order."""
        stops = route.stop_names()
        if self.direction == "KB":
            stops = list(reversed(stops))
        return stops

    def scheduling_stations_in_order(self, route: Route) -> List[str]:
        """Scheduling stations in this bus's travel order."""
        all_stops = self.stops_in_order(route)
        return [s for s in all_stops if s in route.scheduling_stations]


# ---------------------------------------------------------------------------
# Scheduler outputs
# ---------------------------------------------------------------------------

@dataclass
class ChargeEvent:
    """One charging stop for a bus."""
    bus_id: str
    station: str
    arrive_time_min: float      # when bus physically arrives at station
    wait_time_min: float        # time spent waiting for charger to free up
    charge_start_min: float     # = arrive + wait
    charge_end_min: float       # = charge_start + charge_duration
    range_on_arrival_km: float  # remaining range when arriving (for validation)


@dataclass
class BusResult:
    """Full output for one bus after scheduling."""
    bus: BusInput
    charging_plan: List[str]            # ordered list of stations this bus will use
    charge_events: List[ChargeEvent]    # detailed timeline
    arrival_time_min: float             # when bus reaches its destination
    total_wait_min: float               # sum of all waits at chargers

    @property
    def total_trip_min(self) -> float:
        return self.arrival_time_min - self.bus.departure_time_min

    @property
    def delay_vs_no_wait_min(self) -> float:
        """Extra time added purely by waiting (vs zero-contention baseline)."""
        return self.total_wait_min


@dataclass
class StationLog:
    """Ordered service log for one station."""
    station: str
    events: List[ChargeEvent] = field(default_factory=list)   # in service order


@dataclass
class ScheduleResult:
    """Top-level output from the scheduler for one scenario run."""
    bus_results: List[BusResult]
    station_logs: Dict[str, StationLog]   # station_name → log

    def bus_by_id(self, bus_id: str) -> Optional[BusResult]:
        return next((r for r in self.bus_results if r.bus.id == bus_id), None)

    def total_wait_min(self) -> float:
        return sum(r.total_wait_min for r in self.bus_results)

    def max_individual_wait_min(self) -> float:
        return max((r.total_wait_min for r in self.bus_results), default=0)

    def operator_max_wait_min(self) -> Dict[str, float]:
        from collections import defaultdict
        op: Dict[str, float] = defaultdict(float)
        for r in self.bus_results:
            op[r.bus.operator] = max(op[r.bus.operator], r.total_wait_min)
        return dict(op)
