"""
loader.py — Reads a scenario JSON file and returns typed model objects.

This is the only place that knows about the JSON schema.
Add a new JSON field?  Touch only this file.
"""

from __future__ import annotations
import json
from pathlib import Path
from typing import Dict

from scheduler.models import (
    Segment, StationConfig, Physics, Weights, Route, BusInput
)


def parse_time(t: str) -> float:
    """Convert 'HH:MM' to minutes since midnight."""
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def load_scenario(path: str | Path):
    """
    Load a scenario JSON and return (route, physics, weights, stations, buses, meta).
    Returns a plain dict for maximum flexibility — callers unpack what they need.
    """
    with open(path, "r") as f:
        raw = json.load(f)

    # --- route ---
    segments = [
        Segment(
            from_stop=s["from"],
            to_stop=s["to"],
            distance_km=s["distance_km"],
        )
        for s in raw["route"]["segments"]
    ]
    route = Route(
        segments=segments,
        scheduling_stations=raw["route"]["scheduling_stations"],
        origins=raw["route"]["origins"],
    )

    # --- physics ---
    p = raw["physics"]
    physics = Physics(
        battery_range_km=p["battery_range_km"],
        charge_duration_min=p["charge_duration_min"],
        speed_kmh=p["speed_kmh"],
    )

    # --- weights ---
    w = raw.get("weights", {})
    weights = Weights(
        individual=w.get("individual", 1.0),
        operator=w.get("operator", 1.0),
        overall=w.get("overall", 1.0),
    )

    # --- stations ---
    stations: Dict[str, StationConfig] = {
        name: StationConfig(name=name, chargers=cfg.get("chargers", 1))
        for name, cfg in raw["stations"].items()
    }

    # --- buses ---
    buses = [
        BusInput(
            id=b["id"],
            operator=b["operator"],
            direction=b["direction"],
            departure_time_min=parse_time(b["departure"]),
        )
        for b in raw["buses"]
    ]

    # --- metadata ---
    meta = {
        "id": raw.get("id", ""),
        "name": raw.get("name", ""),
        "description": raw.get("description", ""),
    }

    return dict(
        route=route,
        physics=physics,
        weights=weights,
        stations=stations,
        buses=buses,
        meta=meta,
        raw=raw,
    )


def load_all_scenarios(scenarios_dir: str | Path) -> list[dict]:
    """Load every scenario_*.json in a directory, sorted by filename."""
    p = Path(scenarios_dir)
    files = sorted(p.glob("scenario_*.json"))
    return [load_scenario(f) for f in files]
