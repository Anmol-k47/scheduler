# Architecture

## Scheduler framework: discrete-event simulation with cost-based ordering

### Why this approach?

The problem is fundamentally about **time-ordered resource contention**: buses arrive at stations at specific times and compete for a shared charger. That's the textbook definition of a discrete-event simulation (DES).

Alternatives considered:

| Approach | Why rejected |
|---|---|
| Greedy round-robin | Can't handle arbitrary weights or new rules cleanly |
| LP/ILP solver | Overkill; adds a heavy solver dependency; hard to explain in interviews |
| Rule-based heuristics | Brittle; each new rule requires careful case-by-case tuning |
| DES + cost scoring | ✅ Clean separation of simulation (engine) vs. policy (cost.py); easy to extend |

The simulation runs in O(E log E) time where E = total charge events across all buses. For the current scale (20 buses × 2 stops = 40 events) this is instantaneous. At 10,000 buses it would still finish in well under a second on any modern machine.

### How the algorithm works

```
1. Plan assignment
   For each bus, enumerate all valid charging plans (subsets of scheduling
   stations where no consecutive leg exceeds battery_range_km).
   Pick the plan with fewest stops (minimises baseline trip time).

2. Seed the event heap
   Push each bus's first arrival event onto a global min-heap:
       (expected_arrive_time, bus_id, target_station)

3. Event loop (while heap is non-empty):
   a. Pop the earliest event.
   b. If multiple buses arrive at the same station at the same time,
      sort them by total_cost() — lowest cost charges first.
   c. Book the charger (ChargerQueue handles ≥1 charger slots via its own heap).
   d. Record ChargeEvent with arrive / wait / charge_start / charge_end / range.
   e. Push the bus's next station event (charge_end + travel_to_next).

4. Final arrivals
   After the heap empties, compute each bus's arrival at its destination
   from its last charge stop.
```

### Key design decision: explicit target_station in every heap event

Every heap item carries `(time, bus_id, target_station)`. The engine never infers where a bus is headed from its current position — it always reads the explicit field.

This eliminates a whole class of bugs that appear when buses can be re-routed mid-trip, when stations are skipped due to range-based plan changes, or when bidirectional traffic means two buses at the same station are heading in opposite directions. Explicit state is always safer than inferred state.

---

## Data structure design

### Scenario JSON schema

```jsonc
{
  "id":          "scenario_1",
  "name":        "...",
  "description": "...",

  "route": {
    "segments": [                    // ordered list of physical segments
      {"from": "Bengaluru", "to": "A", "distance_km": 100},
      ...
    ],
    "scheduling_stations": ["A", "B", "C", "D"],   // stations with chargers
    "origins": ["Bengaluru", "Kochi"]               // endpoints (no scheduling)
  },

  "physics": {
    "battery_range_km":   240,
    "charge_duration_min": 25,
    "speed_kmh":           60
  },

  "stations": {                     // per-station config
    "A": {"chargers": 1},           // just set chargers > 1 for multi-charger
    ...
  },

  "weights": {                      // tunable soft-rule weights
    "individual": 1.0,
    "operator":   1.0,
    "overall":    1.0
  },

  "buses": [
    {"id": "bus-BK-01", "operator": "kpn", "direction": "BK", "departure": "19:00"},
    ...
  ]
}
```

### Why this structure handles future changes without code changes

Below is the full set of changes anticipated at design time, and how the data model handles each:

| Change | How the data model handles it |
|---|---|
| **Add a new station** (e.g. station E between D and Kochi) | Add a segment to `route.segments`, add `"E"` to `scheduling_stations`, add `"E": {"chargers": 1}` to `stations`. Zero code changes. |
| **Change a segment distance** | Edit `distance_km` in the relevant segment. The engine recomputes all travel times from the route. |
| **Add chargers to a station** | Change `"chargers": 1` to `"chargers": 2`. `ChargerQueue` supports N chargers via its internal heap. |
| **Add a new bus** | Add one JSON object to the `buses` array. The engine loops over `buses` generically. |
| **Change a weight** | Edit one value in `weights`. No other file needs to change. |
| **Add a new weight / soft rule** | Add one function to `cost.py` + one field to `Weights` + one field to the JSON. The engine calls all rules via `RULES` registry — it doesn't need touching. |
| **Change battery range** | Edit `physics.battery_range_km`. Plan enumeration uses this value; all valid plans recompute automatically. |
| **Change charge duration** | Edit `physics.charge_duration_min`. Used only in the engine's `cq.book()` call. |
| **Change travel speed** | Edit `physics.speed_kmh`. `route.travel_time_between()` uses it. |
| **Add a new route** (e.g. Bengaluru–Chennai) | Create a new scenario JSON with different segments and stations. No code changes. |
| **Add a new operator** | Add buses with a new operator string. Operator-level logic in `cost.py` uses operator name as a string key — no hardcoding. |
| **Extend to multiple routes sharing a station** | Station configs are a dict keyed by name. If two routes reference the same station name, they share the same `ChargerQueue` automatically once the engine is extended to handle multi-route scenarios. |
| **Add hard rules** (e.g. no charging between 02:00–04:00) | Add a constraint check in `valid_charging_plans()` or a pre-filter in the event loop. Engine structure doesn't change. |
| **Per-bus attributes** (priority flag, vehicle type, driver shift end) | Add fields to the bus JSON object and to `BusInput`. Rules in `cost.py` can read them via `ScoringContext.extra`. |
| **Time-of-day electricity cost** | Add `peak_hours` to station config or `physics`. Add a rule in `cost.py` that reads the charge start time from context. |
| **Different charge durations per bus or station** | Move `charge_duration_min` from `physics` into per-station config or per-bus config. Loader passes it through; engine reads from the right place. |

### What the data structure intentionally does NOT embed

- **Pre-computed charging plans** — these are computed at runtime from `route` + `physics`. If either changes, plans recompute automatically without touching any stored data.
- **Hardcoded direction counts** — the engine doesn't assume 10+10. It loops over the `buses` array generically.
- **Station order in bus objects** — bus objects only carry direction (`"BK"` / `"KB"`). The engine derives station visit order from the route at runtime. This means a route change automatically updates every bus's stop sequence.

---

## Module responsibilities

```
models.py   Pure data classes. No logic, no imports from other scheduler modules.
            The "shape of the world" — everything downstream reads from this.

loader.py   Parses JSON → typed objects. The only file that knows the JSON schema.
            Adding a new JSON field = touching only this file.

cost.py     All soft-rule scoring. One function per rule, one RULES registry.
            The engine calls total_cost(); it doesn't care what's inside.

engine.py   Discrete-event simulation. Enforces hard rules (range, one-bus-per-charger).
            Delegates soft-rule ordering to cost.py. Never touches JSON.

app.py      Streamlit UI. Reads scenario JSONs via loader, runs engine, renders results.
            Contains no business logic — just presentation.
```

---

## Assumptions made

- **Speed is uniform** for all buses and all segments (no traffic, no variation). The spec says "consistent speed" — 60 km/h chosen for round numbers.
- **Charging always fills to 240 km**. Partial charging is not modelled.
- **Buses depart from origin with full charge**. The spec states this explicitly.
- **The scheduler picks charging plans before running the simulation**, using a minimum-stop heuristic. Plans are not re-optimised mid-trip in response to contention. This is a deliberate simplification; re-planning mid-trip would require a more complex feedback loop.
- **Contention resolution is greedy at the point of arrival**. The scheduler does not look ahead to predict future queues at downstream stations. This is consistent with a real-time dispatch system.
- **Scenario 3 has 14 buses** (10 BK + 4 KB) as specified. The engine handles any count.
- **Negative wait is clamped to zero**. If a charger is free when a bus arrives, wait = 0.
- **Station logs are sorted by charge_start_min** (service order), not arrival order. This is what matters operationally — it's the order in which the charger was actually used.
