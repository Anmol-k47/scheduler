"""
cost.py — Scoring / cost functions for the scheduler.

Design principle
----------------
Each *rule* is an independent function with signature:

    rule(candidate: CandidateState, context: ScoringContext) -> float

The engine sums weighted rule scores.  Adding a new rule = adding one function
here + one entry in RULES (or passing it in at runtime).  Nothing else changes.

Weights live in a single Weights object (models.py) — one obvious place.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, List, Dict, Tuple

from scheduler.models import Weights


# ---------------------------------------------------------------------------
# Context passed to every rule
# ---------------------------------------------------------------------------

@dataclass
class ScoringContext:
    """
    Everything a rule might need to score a candidate charging stop.
    Extend freely — rules that don't need a new field just ignore it.
    """
    weights: Weights
    # current wait at the candidate station (minutes)
    wait_min: float
    # accumulated wait for THIS bus so far in the trip
    bus_total_wait_so_far: float
    # accumulated wait for all buses of THIS operator so far
    operator_total_wait: float
    # total wait across the whole network so far
    network_total_wait: float
    # number of chargers at this station (useful for multi-charger rules)
    station_chargers: int = 1
    # placeholder for future rules (e.g. electricity cost, driver shift remaining)
    extra: Dict = None


# ---------------------------------------------------------------------------
# Built-in rules
# ---------------------------------------------------------------------------

def rule_individual_wait(wait_min: float, context: ScoringContext) -> float:
    """
    Penalise how long THIS bus has to wait right now.
    Weight: individual
    """
    return context.weights.individual * wait_min


def rule_operator_wait(wait_min: float, context: ScoringContext) -> float:
    """
    Penalise the operator's cumulative delay across its fleet.
    Uses the additional wait this bus incurs on the operator total.
    Weight: operator
    """
    return context.weights.operator * (context.operator_total_wait + wait_min)


def rule_overall_network(wait_min: float, context: ScoringContext) -> float:
    """
    Penalise total network delay.
    Weight: overall
    """
    return context.weights.overall * (context.network_total_wait + wait_min)


# ---------------------------------------------------------------------------
# Future rules (examples — uncomment + add to RULES to activate)
# ---------------------------------------------------------------------------

# def rule_electricity_cost(wait_min: float, context: ScoringContext) -> float:
#     """Penalise charging during peak electricity hours."""
#     peak_multiplier = context.extra.get("peak_multiplier", 1.0)
#     return context.weights.get("electricity", 0.0) * wait_min * peak_multiplier

# def rule_driver_shift(wait_min: float, context: ScoringContext) -> float:
#     """Penalise pushing a bus near its driver's shift limit."""
#     shift_pressure = context.extra.get("shift_pressure", 0.0)
#     return context.weights.get("driver_shift", 0.0) * shift_pressure * wait_min

# def rule_priority_bus(wait_min: float, context: ScoringContext) -> float:
#     """Reduce cost for priority (VIP / emergency) buses."""
#     is_priority = context.extra.get("is_priority", False)
#     return -context.weights.get("priority", 0.0) * wait_min if is_priority else 0.0


# ---------------------------------------------------------------------------
# Rule registry — the only place that lists which rules are active
# ---------------------------------------------------------------------------

# Each entry: (rule_function, label_for_docs)
RULES: List[Tuple[Callable, str]] = [
    (rule_individual_wait, "individual_wait"),
    (rule_operator_wait,   "operator_fairness"),
    (rule_overall_network, "network_efficiency"),
    # add new rules here — engine picks them up automatically
]


def total_cost(wait_min: float, context: ScoringContext) -> float:
    """
    Weighted sum of all active rules.
    Lower is better (used to compare candidate scheduling orderings).
    """
    return sum(fn(wait_min, context) for fn, _ in RULES)
