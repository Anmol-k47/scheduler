"""
engine.py — Priority-queue event-driven bus charging scheduler.

Algorithm
---------
1. For every bus enumerate all valid charging plans (subsets of scheduling
   stations where no consecutive leg exceeds battery range).  Pick the
   minimum-stop plan that keeps baseline trip time low.

2. Seed a global min-heap with each bus's first charging event (time it
   would arrive at its first planned station assuming no waits).

3. Process events in time order.  When a bus arrives at a station:
     a. The charger may already be busy.  Compute how long the bus waits.
     b. Book the charger (ChargerQueue handles ≥1 chargers per station).
     c. Record the ChargeEvent.
     d. Push the bus's next station event onto the heap (arrival time =
        charge_end + travel_time_to_next_station).

4. When the heap is empty every bus has completed its charging plan.
   Compute final arrival at destination from the last charge stop.

Contention resolution (soft rules / weights)
--------------------------------------------
When multiple buses arrive at the same station at the same time, the one
with the LOWEST cost score goes first.  cost.py owns all scoring logic;
the engine just calls total_cost().  Adding a new soft rule = one function
in cost.py, nothing here changes.

Hard rules always hold regardless of weights:
- One bus per charger slot (enforced by ChargerQueue).
- Range constraint enforced during plan enumeration; invalid plans are never
  generated.
- Buses visit stations in their own travel order (enforced by event chaining).
"""

from __future__ import annotations
import heapq
from collections import defaultdict
from itertools import combinations
from typing import Dict, List, Tuple

from scheduler.models import (
    BusInput, BusResult, ChargeEvent, Physics,
    Route, ScheduleResult, StationConfig, StationLog, Weights,
)
from scheduler.cost import ScoringContext, total_cost


# ---------------------------------------------------------------------------
# Charging plan enumeration
# ---------------------------------------------------------------------------

def valid_charging_plans(
    bus: BusInput,
    route: Route,
    physics: Physics,
) -> List[List[str]]:
    """
    Return every valid ordered subset of scheduling stations for this bus.

    A plan is valid iff every consecutive leg (origin → first stop,
    stop → stop, last stop → destination) ≤ battery_range_km.
    Stations are always visited in the bus's natural travel order.
    """
    stations = bus.scheduling_stations_in_order(route)
    stops = bus.stops_in_order(route)
    origin = stops[0]
    destination = stops[-1]
    R = physics.battery_range_km

    def is_valid(plan: List[str]) -> bool:
        checkpoints = [origin] + list(plan) + [destination]
        for i in range(len(checkpoints) - 1):
            if route.distance_between(checkpoints[i], checkpoints[i + 1]) > R:
                return False
        return True

    valid: List[List[str]] = []
    for size in range(len(stations) + 1):
        for combo in combinations(stations, size):
            plan = list(combo)  # combinations preserves order from `stations`
            if is_valid(plan):
                valid.append(plan)
    return valid


def best_charging_plan(bus: BusInput, route: Route, physics: Physics) -> List[str]:
    """
    From all valid plans pick the one with fewest stops (minimum charging =
    minimum baseline travel time).  Ties resolved by preferring earlier stops
    to spread load toward route edges and away from busy inner stations.
    """
    plans = valid_charging_plans(bus, route, physics)
    if not plans:
        raise ValueError(
            f"No valid charging plan for bus {bus.id}. "
            f"Check route distances vs battery range ({physics.battery_range_km} km)."
        )

    sched = route.scheduling_stations          # forward order always
    def plan_key(p: List[str]) -> tuple:
        # fewest stops first; ties: prefer plan whose first stop comes earliest
        first_idx = sched.index(p[0]) if p else 999
        return (len(p), first_idx)

    return min(plans, key=plan_key)


# ---------------------------------------------------------------------------
# Charger queue — supports ≥ 1 charger per station
# ---------------------------------------------------------------------------

class ChargerQueue:
    """Min-heap of charger free-times; one entry per charger slot."""

    def __init__(self, station: str, chargers: int):
        self.station = station
        self._free: List[float] = [0.0] * chargers   # heap of next-free times
        heapq.heapify(self._free)

    def earliest_free(self) -> float:
        return self._free[0]

    def book(self, arrive: float, duration: float) -> Tuple[float, float]:
        """
        Reserve the earliest available charger.
        Returns (charge_start, charge_end).
        """
        start = max(self._free[0], arrive)
        end = start + duration
        heapq.heapreplace(self._free, end)
        return start, end


# ---------------------------------------------------------------------------
# Main simulation
# ---------------------------------------------------------------------------

def run(
    route: Route,
    physics: Physics,
    weights: Weights,
    stations: Dict[str, StationConfig],
    buses: List[BusInput],
) -> ScheduleResult:
    """Run the full simulation and return a ScheduleResult."""

    # ── 1. Assign charging plans ──────────────────────────────────────────
    bus_plans: Dict[str, List[str]] = {
        b.id: best_charging_plan(b, route, physics) for b in buses
    }

    # ── 2. Initialise state ───────────────────────────────────────────────
    bus_map: Dict[str, BusInput] = {b.id: b for b in buses}

    # Remaining planned stops per bus (mutable copy)
    remaining_plan: Dict[str, List[str]] = {
        b.id: list(bus_plans[b.id]) for b in buses
    }

    # Track each bus's current position (stop name) and clock (minutes)
    bus_clock: Dict[str, float] = {b.id: b.departure_time_min for b in buses}
    bus_stop:  Dict[str, str]   = {b.id: b.stops_in_order(route)[0] for b in buses}

    # Accumulated waits for cost scoring
    bus_wait_total:      Dict[str, float] = defaultdict(float)
    operator_wait_total: Dict[str, float] = defaultdict(float)
    network_wait_total:  float = 0.0

    # Charger queues per station
    charger_queues: Dict[str, ChargerQueue] = {
        name: ChargerQueue(name, cfg.chargers)
        for name, cfg in stations.items()
    }

    # Output accumulators
    charge_events_by_bus: Dict[str, List[ChargeEvent]] = defaultdict(list)
    station_logs: Dict[str, StationLog] = {
        name: StationLog(station=name) for name in stations
    }

    # ── 3. Seed the event heap ────────────────────────────────────────────
    # Heap items: (arrive_time, bus_id, target_station)
    # target_station is carried explicitly — never inferred from context.
    heap: List[Tuple[float, str, str]] = []

    for bus in buses:
        plan = remaining_plan[bus.id]
        if plan:
            first_station = plan[0]
            travel = route.travel_time_between(bus.origin, first_station, physics.speed_kmh)
            arrive = bus.departure_time_min + travel
            heapq.heappush(heap, (arrive, bus.id, first_station))

    # Track which (bus_id, station) pairs have been processed
    processed: set = set()

    # ── 4. Process events ─────────────────────────────────────────────────
    while heap:
        arrive_time, bus_id, target_station = heapq.heappop(heap)

        key = (bus_id, target_station)
        if key in processed:
            continue
        processed.add(key)

        bus = bus_map[bus_id]
        cq  = charger_queues[target_station]

        # ── Contention: collect all buses arriving at this station within
        #    the same "tick" (same arrive_time) and sort by cost so the
        #    cheapest (lowest cost = most deserving) goes first.
        # We've already popped the first; gather ties from heap.
        simultaneous = [(arrive_time, bus_id, target_station)]
        while heap and heap[0][0] == arrive_time and heap[0][2] == target_station:
            _, bid2, _ = heapq.heappop(heap)
            if (bid2, target_station) not in processed:
                simultaneous.append((arrive_time, bid2, target_station))

        if len(simultaneous) > 1:
            # Sort by cost ascending; bus with lowest cost charges first
            def score(item):
                _, bid, st = item
                b2 = bus_map[bid]
                wait_if_first = max(0.0, cq.earliest_free() - item[0])
                ctx = ScoringContext(
                    weights=weights,
                    wait_min=wait_if_first,
                    bus_total_wait_so_far=bus_wait_total[bid],
                    operator_total_wait=operator_wait_total[b2.operator],
                    network_total_wait=network_wait_total,
                    station_chargers=cq.chargers,
                )
                return total_cost(wait_if_first, ctx)
            simultaneous.sort(key=score)
            # Re-push all but the first back onto the heap
            for item in simultaneous[1:]:
                heapq.heappush(heap, item)
            arrive_time, bus_id, target_station = simultaneous[0]
            bus = bus_map[bus_id]

        # ── Book the charger ──────────────────────────────────────────────
        charge_start, charge_end = cq.book(arrive_time, physics.charge_duration_min)
        wait = charge_start - arrive_time

        # ── Range validation ──────────────────────────────────────────────
        prev_stop = bus_stop[bus_id]
        dist = route.distance_between(prev_stop, target_station)
        range_on_arrival = physics.battery_range_km - dist
        # (Should always be >= 0 if plan_enumeration is correct)

        # ── Record event ──────────────────────────────────────────────────
        event = ChargeEvent(
            bus_id=bus_id,
            station=target_station,
            arrive_time_min=arrive_time,
            wait_time_min=wait,
            charge_start_min=charge_start,
            charge_end_min=charge_end,
            range_on_arrival_km=range_on_arrival,
        )
        charge_events_by_bus[bus_id].append(event)
        station_logs[target_station].events.append(event)

        # ── Update accumulators ───────────────────────────────────────────
        bus_wait_total[bus_id]           += wait
        operator_wait_total[bus.operator] += wait
        network_wait_total               += wait
        bus_clock[bus_id]  = charge_end
        bus_stop[bus_id]   = target_station

        # Advance remaining plan
        plan = remaining_plan[bus_id]
        if plan and plan[0] == target_station:
            plan.pop(0)

        # ── Schedule next station ─────────────────────────────────────────
        if plan:
            next_station = plan[0]
            travel = route.travel_time_between(target_station, next_station, physics.speed_kmh)
            next_arrive = charge_end + travel
            heapq.heappush(heap, (next_arrive, bus_id, next_station))
        # else: bus heads straight to destination — handled in step 5

    # ── 5. Compute final arrivals ─────────────────────────────────────────
    bus_results: List[BusResult] = []
    for bus in buses:
        last_stop = bus_stop[bus.id]
        last_time = bus_clock[bus.id]
        travel_to_dest = route.travel_time_between(last_stop, bus.destination, physics.speed_kmh)
        arrival = last_time + travel_to_dest

        bus_results.append(BusResult(
            bus=bus,
            charging_plan=bus_plans[bus.id],
            charge_events=charge_events_by_bus[bus.id],
            arrival_time_min=arrival,
            total_wait_min=bus_wait_total[bus.id],
        ))

    # Sort each station log by charge start time
    for log in station_logs.values():
        log.events.sort(key=lambda e: e.charge_start_min)

    return ScheduleResult(bus_results=bus_results, station_logs=station_logs)
