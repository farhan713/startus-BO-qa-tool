"""Scenario store — a persistent library of named, reusable test scenarios.

A scenario captures a known testable situation (a bug, a feature, a smoke
flow) so a tester can later say "test STRAT-28795 with u2" and the tool
knows what to run.

Backed by a single JSON file at `knowledge_base/scenarios.json`. Atomic
writes via tempfile + replace so the file is always either fully-old or
fully-new — never half-written.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path


# Default location — overridable for tests via STRATUS_SCENARIOS_PATH
def _store_path() -> Path:
    if env := os.environ.get("STRATUS_SCENARIOS_PATH"):
        return Path(env)
    here = Path(__file__).resolve().parent.parent
    return here / "knowledge_base" / "scenarios.json"


# ============================================================ Data model

@dataclass
class Scenario:
    id: str                          # human-readable, used by recall ("STRAT-28795")
    title: str                       # short description
    description: str = ""            # full notes
    screen: str = ""                 # default catalog screen
    tags: list = field(default_factory=list)
    variables: dict = field(default_factory=dict)  # {name: default-value}
    steps: list = field(default_factory=list)      # step dicts (same shape as YAML)
    author: str = ""
    created_ts: float = 0.0
    last_run_ts: float | None = None
    run_count: int = 0

    @classmethod
    def from_dict(cls, d: dict) -> "Scenario":
        # Forward-compatible: ignore unknown keys, fill missing with defaults
        f = {k.name for k in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in f})


# ============================================================ I/O

def _read_all() -> dict:
    p = _store_path()
    if not p.exists():
        return {"scenarios": {}, "version": 1}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"scenarios": {}, "version": 1}


def _write_all(data: dict) -> None:
    p = _store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


# ============================================================ Public API

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,80}$")


def list_scenarios() -> list[dict]:
    """Return every scenario as a dict, newest first."""
    data = _read_all()
    out = list((data.get("scenarios") or {}).values())
    out.sort(key=lambda s: s.get("created_ts", 0), reverse=True)
    return out


def get_scenario(scenario_id: str) -> dict | None:
    data = _read_all()
    return (data.get("scenarios") or {}).get(scenario_id)


def save_scenario(scenario: Scenario, overwrite: bool = False) -> Scenario:
    """Persist a scenario. Raises ValueError if the id collides and
    overwrite=False, or if the id has invalid characters."""
    if not _SAFE_ID.match(scenario.id):
        raise ValueError(
            f"invalid scenario id {scenario.id!r}: use letters, digits, '-', '_'")
    if not scenario.title.strip():
        raise ValueError("scenario.title is required")
    if not scenario.steps:
        raise ValueError("scenario.steps is empty — nothing to run")

    data = _read_all()
    bucket = data.setdefault("scenarios", {})
    if scenario.id in bucket and not overwrite:
        raise ValueError(f"scenario {scenario.id!r} already exists; pass overwrite=True to replace")
    # Stamp created_ts on first save; preserve on edit so the library sort is stable
    existing = bucket.get(scenario.id) or {}
    if not scenario.created_ts:
        scenario.created_ts = existing.get("created_ts") or time.time()
    if "run_count" in existing and not scenario.run_count:
        scenario.run_count = existing["run_count"]
    if "last_run_ts" in existing and not scenario.last_run_ts:
        scenario.last_run_ts = existing["last_run_ts"]

    bucket[scenario.id] = asdict(scenario)
    _write_all(data)
    return scenario


def delete_scenario(scenario_id: str) -> bool:
    data = _read_all()
    bucket = data.get("scenarios") or {}
    if scenario_id not in bucket:
        return False
    del bucket[scenario_id]
    _write_all(data)
    return True


def record_run(scenario_id: str) -> None:
    """Bump run-count + last-run timestamp on a scenario."""
    data = _read_all()
    bucket = data.get("scenarios") or {}
    if scenario_id not in bucket:
        return
    bucket[scenario_id]["run_count"] = (bucket[scenario_id].get("run_count") or 0) + 1
    bucket[scenario_id]["last_run_ts"] = time.time()
    _write_all(data)


# ============================================================ Scenario-from-YAML

def from_yaml_text(scenario_id: str, title: str, yaml_text: str,
                   description: str = "", tags: list | None = None,
                   author: str = "") -> Scenario:
    """Parse a YAML doc (the kind the converter produces) into a Scenario.

    Handles both shapes:
      • {tests: [{screen, name, steps}]}  — converter output
      • {steps: [...]}                     — raw step list
    For multi-test YAML, all tests' steps are flattened in order; the screen
    comes from the first test that has one.
    """
    import yaml
    doc = yaml.safe_load(yaml_text) or {}
    steps: list = []
    screen = ""
    if isinstance(doc, dict):
        if "tests" in doc and isinstance(doc["tests"], list):
            for t in doc["tests"]:
                if not isinstance(t, dict): continue
                if not screen and t.get("screen"):
                    screen = t["screen"]
                for st in (t.get("steps") or []):
                    if isinstance(st, dict): steps.append(st)
        elif isinstance(doc.get("steps"), list):
            steps = [s for s in doc["steps"] if isinstance(s, dict)]
            screen = doc.get("screen", "")
    elif isinstance(doc, list):
        steps = [s for s in doc if isinstance(s, dict)]

    return Scenario(
        id=scenario_id,
        title=title,
        description=description,
        screen=screen,
        tags=list(tags or []),
        steps=steps,
        author=author,
    )


def to_yaml_text(scenario: dict, applied_variables: dict | None = None) -> str:
    """Render a scenario as runnable YAML, substituting any {{var}} tokens.

    `applied_variables` overrides the scenario's defaults — that's how
    "test STRAT-28795 with order WEB-99999" works."""
    import yaml
    vars_resolved = dict(scenario.get("variables") or {})
    if applied_variables:
        vars_resolved.update({k: v for k, v in applied_variables.items() if v is not None})

    def sub(value):
        if not isinstance(value, str): return value
        out = value
        for k, v in vars_resolved.items():
            out = re.sub(r"\{\{\s*" + re.escape(k) + r"\s*\}\}",
                         str(v), out)
        return out

    steps_out = []
    for st in scenario.get("steps") or []:
        s = {k: sub(v) for k, v in st.items()}
        steps_out.append(s)

    doc = {"tests": [{
        "screen": scenario.get("screen") or "yourscreen",
        "name": scenario.get("title") or scenario.get("id") or "scenario",
        "steps": steps_out,
    }]}
    header = (
        f"# Stratus QA — Scenario: {scenario.get('id', '?')}\n"
        f"# Title : {scenario.get('title', '')}\n"
        f"# Vars  : {vars_resolved or '(none)'}\n"
        f"# Run # : {scenario.get('run_count', 0)}\n"
        f"# ============================================================\n\n"
    )
    return header + yaml.safe_dump(doc, sort_keys=False, default_flow_style=False,
                                   allow_unicode=True, width=100)


# ============================================================ Directory (users + named data)

def _directory_path() -> Path:
    if env := os.environ.get("STRATUS_DIRECTORY_PATH"):
        return Path(env)
    return Path(__file__).resolve().parent.parent / "knowledge_base" / "directory.json"


_DEFAULT_DIRECTORY = {
    "users": {
        # Examples — replace with real Stratus QA accounts
        "u1":   {"username": "user",  "password": "", "role": "admin"},
        "demo": {"username": "demo",  "password": "", "role": "viewer"},
    },
    "envs": {
        "local":   {"url": "http://localhost:8080/backoffice/login.jsp", "machine_id": "100"},
        "staging": {"url": "https://YOUR-STAGING-HOST/backoffice/?mid=100", "machine_id": "100"},
        "prod":    {"url": "https://YOUR-PROD-HOST/backoffice/?mid=100",    "machine_id": "100"},
    },
    "data": {
        # Named data values — referenced from scenarios via {{name}}
        "customer.smith":     "Smith",
        "customer.oconnor":   "O'Connor-Smith",
        "order.web1":         "WEB-12345",
    },
}


def load_directory() -> dict:
    p = _directory_path()
    if not p.exists():
        # Seed with defaults so the UI has something to show
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(_DEFAULT_DIRECTORY, indent=2), encoding="utf-8")
        return dict(_DEFAULT_DIRECTORY)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return dict(_DEFAULT_DIRECTORY)


def save_directory(data: dict) -> None:
    p = _directory_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)
