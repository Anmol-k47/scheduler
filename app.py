"""
app.py — Streamlit UI for the Bus Charging Scheduler.

Layout
------
1. Sidebar: scenario picker + weight sliders
2. Main: Scenario Overview → Per-Bus Timetable → Per-Station View
"""

import streamlit as st
from pathlib import Path
import pandas as pd

from scheduler.loader import load_all_scenarios
from scheduler.engine import run

# ── Page config ────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Bus Charging Scheduler",
    page_icon="⚡",
    layout="wide",
)

# ── Load scenarios once ────────────────────────────────────────────────────
SCENARIOS_DIR = Path(__file__).parent / "scenarios"

@st.cache_data
def load_scenarios():
    return load_all_scenarios(SCENARIOS_DIR)

ALL_SCENARIOS = load_scenarios()
SCENARIO_NAMES = [sc["meta"]["name"] for sc in ALL_SCENARIOS]

# ── Helpers ────────────────────────────────────────────────────────────────
def fmt_min(minutes: float) -> str:
    """Convert absolute minutes-since-midnight to HH:MM string."""
    minutes = int(round(minutes))
    h, m = divmod(minutes, 60)
    return f"{h:02d}:{m:02d}"

def fmt_dur(minutes: float) -> str:
    """Format a duration in minutes as Xh Ym or Ym."""
    minutes = int(round(minutes))
    if minutes >= 60:
        return f"{minutes // 60}h {minutes % 60}m"
    return f"{minutes}m"

OPERATOR_COLORS = {
    "kpn":      "#3b82f6",   # blue
    "freshbus": "#10b981",   # green
    "flixbus":  "#f59e0b",   # amber
}

def operator_badge(op: str) -> str:
    color = OPERATOR_COLORS.get(op, "#6b7280")
    return f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:4px;font-size:0.8em;font-weight:600">{op.upper()}</span>'

# ── Sidebar ────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("⚡ Bus Charging Scheduler")
    st.markdown("---")

    selected_idx = st.selectbox(
        "Scenario",
        range(len(ALL_SCENARIOS)),
        format_func=lambda i: ALL_SCENARIOS[i]["meta"]["name"],
    )
    sc = ALL_SCENARIOS[selected_idx]
    meta     = sc["meta"]
    route    = sc["route"]
    physics  = sc["physics"]
    stations = sc["stations"]
    buses    = sc["buses"]

    st.markdown("---")
    st.subheader("⚖️ Objective Weights")
    st.caption("Tune priorities — changes rerun the scheduler live.")

    from scheduler.models import Weights
    w_individual = st.slider("Individual (per-bus wait)", 0.0, 5.0,
                             float(sc["weights"].individual), 0.5)
    w_operator   = st.slider("Operator (fleet fairness)", 0.0, 5.0,
                             float(sc["weights"].operator),   0.5)
    w_overall    = st.slider("Overall (network total)",   0.0, 5.0,
                             float(sc["weights"].overall),    0.5)
    weights = Weights(individual=w_individual, operator=w_operator, overall=w_overall)

    st.markdown("---")
    st.caption(f"**Physics:** range {physics.battery_range_km:.0f} km · "
               f"charge {physics.charge_duration_min:.0f} min · "
               f"speed {physics.speed_kmh:.0f} km/h")

# ── Run scheduler ──────────────────────────────────────────────────────────
result = run(route, physics, weights, stations, buses)

# ── Header ─────────────────────────────────────────────────────────────────
st.title(meta["name"])
st.caption(meta["description"])

tab_overview, tab_buses, tab_stations = st.tabs(
    ["📋 Scenario Overview", "🚌 Per-Bus Timetable", "🏭 Per-Station View"]
)

# ═══════════════════════════════════════════════════════════════════════════
# TAB 1 — Scenario Overview
# ═══════════════════════════════════════════════════════════════════════════
with tab_overview:
    # ── Route diagram ──────────────────────────────────────────────────────
    st.subheader("Route")
    stops = route.stop_names()
    cols = st.columns(len(stops) * 2 - 1)
    for i, stop in enumerate(stops):
        col = cols[i * 2]
        is_sched = stop in route.scheduling_stations
        col.markdown(
            f"**{'🔌 ' if is_sched else '🏙️ '}{stop}**",
            help="Scheduling station" if is_sched else "Origin / Destination"
        )
        if i < len(stops) - 1:
            dist = route.segments[i].distance_km
            cols[i * 2 + 1].markdown(f"<div style='text-align:center;color:#888;padding-top:4px'>──{dist:.0f}km──▶</div>", unsafe_allow_html=True)

    st.caption("🔌 = scheduling station (has charger)  ·  🏙️ = origin/destination (no scheduling)")

    # ── Input bus table ────────────────────────────────────────────────────
    st.subheader("Input — Bus Departures")
    rows = []
    for b in buses:
        h = int(b.departure_time_min) // 60
        m = int(b.departure_time_min) % 60
        rows.append({
            "Bus ID":    b.id,
            "Operator":  b.operator.upper(),
            "Direction": "Bengaluru → Kochi" if b.direction == "BK" else "Kochi → Bengaluru",
            "Departure": f"{h:02d}:{m:02d}",
        })
    df_input = pd.DataFrame(rows)
    bk = df_input[df_input["Direction"] == "Bengaluru → Kochi"]
    kb = df_input[df_input["Direction"] == "Kochi → Bengaluru"]
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Bengaluru → Kochi**")
        st.dataframe(bk.drop(columns="Direction"), use_container_width=True, hide_index=True)
    with c2:
        st.markdown("**Kochi → Bengaluru**")
        st.dataframe(kb.drop(columns="Direction"), use_container_width=True, hide_index=True)

    # ── Summary metrics ────────────────────────────────────────────────────
    st.subheader("Schedule Summary")
    mc1, mc2, mc3, mc4 = st.columns(4)
    mc1.metric("Total buses", len(result.bus_results))
    mc2.metric("Total wait (all buses)", fmt_dur(result.total_wait_min()))
    mc3.metric("Max individual wait", fmt_dur(result.max_individual_wait_min()))
    avg_w = result.total_wait_min() / max(1, len(result.bus_results))
    mc4.metric("Avg wait per bus", fmt_dur(avg_w))

    # Operator breakdown
    st.subheader("Operator Summary")
    op_data = {}
    for r in result.bus_results:
        op = r.bus.operator
        if op not in op_data:
            op_data[op] = {"buses": 0, "total_wait": 0.0, "max_wait": 0.0}
        op_data[op]["buses"]      += 1
        op_data[op]["total_wait"] += r.total_wait_min
        op_data[op]["max_wait"]    = max(op_data[op]["max_wait"], r.total_wait_min)

    op_rows = []
    for op, d in sorted(op_data.items()):
        op_rows.append({
            "Operator":       op.upper(),
            "Buses":          d["buses"],
            "Total Wait":     fmt_dur(d["total_wait"]),
            "Max Wait (any bus)": fmt_dur(d["max_wait"]),
            "Avg Wait":       fmt_dur(d["total_wait"] / d["buses"]),
        })
    st.dataframe(pd.DataFrame(op_rows), use_container_width=True, hide_index=True)

# ═══════════════════════════════════════════════════════════════════════════
# TAB 2 — Per-Bus Timetable
# ═══════════════════════════════════════════════════════════════════════════
with tab_buses:
    st.subheader("Per-Bus Timetable")

    # Optional filters
    fc1, fc2 = st.columns(2)
    with fc1:
        dir_filter = st.selectbox(
            "Direction",
            ["All", "Bengaluru → Kochi", "Kochi → Bengaluru"],
            key="dir_filter",
        )
    with fc2:
        all_ops = sorted({b.operator for b in buses})
        op_filter = st.multiselect("Operator", all_ops, default=all_ops, key="op_filter")

    for r in result.bus_results:
        # Apply filters
        dir_label = "Bengaluru → Kochi" if r.bus.direction == "BK" else "Kochi → Bengaluru"
        if dir_filter != "All" and dir_filter != dir_label:
            continue
        if r.bus.operator not in op_filter:
            continue

        with st.expander(
            f"**{r.bus.id}** — {dir_label} · {r.bus.operator.upper()} · "
            f"depart {fmt_min(r.bus.departure_time_min)} · "
            f"arrive {fmt_min(r.arrival_time_min)} · "
            f"{'⚠️ ' if r.total_wait_min > 60 else ''}wait {fmt_dur(r.total_wait_min)}",
            expanded=False,
        ):
            # Timeline table
            rows = []
            prev_stop = r.bus.origin
            prev_time = r.bus.departure_time_min

            # Departure row
            rows.append({
                "Stop":             r.bus.origin,
                "Event":            "Depart",
                "Arrive":           fmt_min(r.bus.departure_time_min),
                "Wait":             "—",
                "Charge Start":     "—",
                "Charge End":       "—",
                "Range on Arrival": f"{physics.battery_range_km:.0f} km (full)",
            })

            for ev in r.charge_events:
                rows.append({
                    "Stop":             ev.station,
                    "Event":            "Charge",
                    "Arrive":           fmt_min(ev.arrive_time_min),
                    "Wait":             fmt_dur(ev.wait_time_min) if ev.wait_time_min > 0 else "none",
                    "Charge Start":     fmt_min(ev.charge_start_min),
                    "Charge End":       fmt_min(ev.charge_end_min),
                    "Range on Arrival": f"{ev.range_on_arrival_km:.0f} km",
                })

            rows.append({
                "Stop":             r.bus.destination,
                "Event":            "Arrive",
                "Arrive":           fmt_min(r.arrival_time_min),
                "Wait":             "—",
                "Charge Start":     "—",
                "Charge End":       "—",
                "Range on Arrival": "—",
            })

            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            st.caption(
                f"Charging plan: **{' → '.join(r.charging_plan)}** · "
                f"Trip time: **{fmt_dur(r.total_trip_min)}** · "
                f"Total wait: **{fmt_dur(r.total_wait_min)}**"
            )

# ═══════════════════════════════════════════════════════════════════════════
# TAB 3 — Per-Station View
# ═══════════════════════════════════════════════════════════════════════════
with tab_stations:
    st.subheader("Per-Station Charging Order")
    st.caption("Shows buses in the order they used each charger, with wait times.")

    sched_stations = route.scheduling_stations
    cols = st.columns(len(sched_stations))

    for col, station_name in zip(cols, sched_stations):
        log = result.station_logs.get(station_name)
        col.markdown(f"### 🔌 Station {station_name}")
        cfg = stations[station_name]
        col.caption(f"{cfg.chargers} charger{'s' if cfg.chargers > 1 else ''}")

        if not log or not log.events:
            col.info("No buses charged here.")
            continue

        for i, ev in enumerate(log.events, 1):
            # Find which bus this event belongs to
            bus_res = result.bus_by_id(ev.bus_id)
            op = bus_res.bus.operator if bus_res else "?"
            direction = bus_res.bus.direction if bus_res else "?"
            color = OPERATOR_COLORS.get(op, "#6b7280")

            wait_str = f"⏳ {fmt_dur(ev.wait_time_min)} wait" if ev.wait_time_min > 0 else "✅ no wait"
            col.markdown(
                f"<div style='border-left: 4px solid {color}; padding: 6px 10px; "
                f"margin-bottom: 6px; border-radius: 0 4px 4px 0; background: #f8f9fa'>"
                f"<b>#{i} {ev.bus_id}</b><br>"
                f"<span style='font-size:0.8em;color:#555'>{op.upper()} · "
                f"{'BK' if direction == 'BK' else 'KB'}</span><br>"
                f"<span style='font-size:0.8em'>Arrive {fmt_min(ev.arrive_time_min)} · "
                f"{wait_str}<br>"
                f"Charge {fmt_min(ev.charge_start_min)}–{fmt_min(ev.charge_end_min)}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )
