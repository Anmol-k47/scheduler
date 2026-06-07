# Bus Charging Scheduler

A Streamlit app that schedules electric bus charging stops on the Bengaluru–Kochi corridor.

## Quick start (local)

```bash
git clone <your-repo-url>
cd bus_scheduler
pip install -r requirements.txt
streamlit run app.py
```

Open http://localhost:8501. The scenario dropdown appears immediately in the sidebar.

## What you'll see

| Tab | Contents |
|-----|----------|
| **Scenario Overview** | Route diagram, raw departure table, summary metrics, operator breakdown |
| **Per-Bus Timetable** | Every bus's full timeline: depart → charge (with wait) → charge → arrive |
| **Per-Station View** | Service order at each of the four charging stations (A, B, C, D) |

Use the **direction** and **operator** filters on the Per-Bus tab to narrow down to the buses you care about.

## Changing a weight

Weights live in two places — the sidebar sliders (live, per session) and the scenario JSON files (persisted defaults).

**In the UI (no code change needed):**  
Move any of the three sliders in the sidebar. The scheduler reruns instantly.

**In a scenario file (to change the default):**
```json
// scenarios/scenario_4.json
"weights": {
  "individual": 1.0,   // ← per-bus wait penalty
  "operator":   2.0,   // ← operator fleet fairness (doubled here)
  "overall":    1.0    // ← total network delay
}
```
One field, one file, no code changes.

## Adding a new rule

All soft-rule logic lives in `scheduler/cost.py`. Adding a rule takes three steps:

**Step 1 — Write the function:**
```python
# scheduler/cost.py
def rule_electricity_cost(wait_min: float, context: ScoringContext) -> float:
    """Penalise charging during peak electricity hours."""
    peak_multiplier = context.extra.get("peak_multiplier", 1.0) if context.extra else 1.0
    electricity_weight = getattr(context.weights, "electricity", 0.0)
    return electricity_weight * wait_min * peak_multiplier
```

**Step 2 — Add it to the registry:**
```python
# scheduler/cost.py → RULES list
RULES = [
    (rule_individual_wait, "individual_wait"),
    (rule_operator_wait,   "operator_fairness"),
    (rule_overall_network, "network_efficiency"),
    (rule_electricity_cost, "electricity_cost"),   # ← add here
]
```

**Step 3 — Add the weight to the model and scenario JSON:**
```python
# scheduler/models.py → Weights dataclass
@dataclass
class Weights:
    individual:  float = 1.0
    operator:    float = 1.0
    overall:     float = 1.0
    electricity: float = 0.5   # ← add here
```
```json
// scenarios/scenario_X.json
"weights": { "individual": 1.0, "operator": 1.0, "overall": 1.0, "electricity": 0.5 }
```

The engine calls every function in `RULES` automatically — no engine changes needed.

## Adding a new scenario

Create a new file `scenarios/scenario_6.json` following the same schema. The app picks it up automatically (it globs `scenario_*.json`).

## Project structure

```
bus_scheduler/
├── app.py                   # Streamlit UI — 3 tabs
├── requirements.txt
├── README.md
├── ARCHITECTURE.md
├── scenarios/
│   ├── scenario_1.json      # Even spacing (baseline)
│   ├── scenario_2.json      # Bunched departures
│   ├── scenario_3.json      # Asymmetric load
│   ├── scenario_4.json      # Operator-heavy (KPN, w_operator=2.0)
│   └── scenario_5.json      # Worst-case convergence
└── scheduler/
    ├── __init__.py
    ├── models.py            # Pure data classes (no logic)
    ├── loader.py            # JSON → typed objects
    ├── cost.py              # Pluggable scoring rules
    └── engine.py            # Discrete-event simulation
```

## Hosting on Streamlit Community Cloud

1. Push the repo to GitHub (public).
2. Go to share.streamlit.io → New app → paste your repo URL.
3. Set `app.py` as the main file.
4. Click Deploy. Streamlit reads `requirements.txt` and installs everything automatically.

No Dockerfile, no server config.
