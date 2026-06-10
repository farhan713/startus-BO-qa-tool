"""Catalog-aware natural-language editor for stored test cases.

The prompt engine (framework/prompt_engine.py) handles *generic*, catalog-blind
transforms — screenshots, waits, skips, row asserts, variable substitution.

This module adds the edits a tester actually asks for when revisiting a saved
test the next day, which NEED to know the screen's real fields:

  • "search by first name instead of last name"   → retarget the fill field
  • "search by first name"                          → retarget to first name
  • "also search by email"                          → add a search test
  • "search for Smith" / "use Smith"                → change the value typed

Field names are resolved against the catalog entry, so the tester types the
human label ("first name") and we map it to the real input id ("FirstName").

Public entry point: `edit_testcases(yaml_text, entry, prompt, use_llm)`.
It runs the generic prompt engine first, then applies these field/value edits
to whatever instruction lines the rule engine could not handle.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import yaml as _yaml

from framework.prompt_engine import apply_prompt, _split_instructions


@dataclass
class EditResult:
    yaml_text: str
    applied: list = field(default_factory=list)
    ignored: list = field(default_factory=list)
    llm_used: bool = False
    llm_error: str = ""


# ----------------------------------------------------------- field resolution

def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def build_field_index(entry: dict) -> list[dict]:
    """Return [{id,label,type,norms:set}] for every catalog field of a screen."""
    out = []
    for f in entry.get("fields") or []:
        fid = f.get("id") or f.get("name") or ""
        label = f.get("label") or f.get("name") or fid
        if not fid:
            continue
        norms = {_norm(fid), _norm(label)}
        # also index the label without a trailing "name"/"id" noise word removed?
        norms.discard("")
        out.append({"id": fid, "label": label, "type": (f.get("type") or "text").lower(),
                    "norms": norms})
    return out


def resolve_field(query: str, index: list[dict]) -> dict | None:
    """Map a human field reference ('first name') to a catalog field.
    Exact normalized match first, then substring either direction."""
    q = _norm(query)
    if not q:
        return None
    for f in index:                       # exact
        if q in f["norms"]:
            return f
    best = None
    for f in index:                       # substring / prefix
        for n in f["norms"]:
            if n and (n.startswith(q) or q.startswith(n) or q in n or n in q):
                # prefer the shortest id (most specific) on ties
                if best is None or len(f["id"]) < len(best["id"]):
                    best = f
    return best


# ----------------------------------------------------------- step helpers

def _is_search_test(test: dict) -> bool:
    for st in test.get("steps") or []:
        if st.get("action") == "click" and "search" in str(st.get("target") or "").lower():
            return True
        if st.get("action") == "open_search":
            return True
    return False


def _fill_steps(test: dict) -> list[dict]:
    return [s for s in (test.get("steps") or []) if s.get("action") == "fill"]


def _sample_value(ftype: str) -> str:
    return {"email": "qa@example.com", "number": "1", "date": "01/01/2025"}.get(ftype, "QA")


# ----------------------------------------------------------- the edits

# "search/filter/look by|with|using <field> [instead of <field>]"
_RX_RETARGET = re.compile(
    r"\b(?:search|filter|look|query|find|fill)\b[^.;\n]*?\b(?:by|with|using|on|in)\s+"
    r"(?P<new>[a-z][\w '\-]{1,40}?)"
    r"(?:\s+(?:instead of|rather than|not|in place of)\s+(?P<old>[a-z][\w '\-]{1,40}?))?"
    r"\s*$", re.I)

# "also/add search by <field>"
_RX_ADD = re.compile(
    r"\b(?:also|add|include|and)\b[^.;\n]*?\b(?:search|filter)\b[^.;\n]*?\b(?:by|with|using|for)\s+"
    r"(?P<new>[a-z][\w '\-]{1,40}?)\s*$", re.I)

# "search for <value>" / "use <value>" / "value <value>" — a literal value to type
_RX_VALUE = re.compile(
    r"\b(?:search|look|query|find)\s+for\s+(?P<val>[\"']?[\w .\-@]{1,40}?[\"']?)\s*$"
    r"|\bvalue\s+(?:should be|is|=)\s*(?P<val2>[\"']?[\w .\-@]{1,40}?[\"']?)\s*$",
    re.I)


def _strip_quotes(s: str) -> str:
    return (s or "").strip().strip("'\"").strip()


def _clean_field_phrase(s: str) -> str:
    # drop trailing filler like "field", "column", "name box"
    s = re.sub(r"\b(field|column|box|textbox|input)\b", "", s, flags=re.I)
    return s.strip()


def _retarget(doc: dict, entry: dict, index: list[dict], new_q: str, old_q: str | None):
    new_f = resolve_field(_clean_field_phrase(new_q), index)
    if not new_f:
        return None, f"no field matching '{new_q.strip()}' on this screen"
    old_f = resolve_field(_clean_field_phrase(old_q), index) if old_q else None

    n = 0
    for t in doc.get("tests", []) or []:
        if not _is_search_test(t):
            continue
        for st in _fill_steps(t):
            tgt = str(st.get("target") or "")
            if old_f is None or tgt == old_f["id"] or _norm(tgt) in old_f["norms"]:
                if tgt != new_f["id"]:
                    st["target"] = new_f["id"]
                    if st.get("value"):
                        # keep a value but make it type-appropriate if it was the
                        # old sample placeholder
                        if str(st["value"]).upper() == "QA":
                            st["value"] = _sample_value(new_f["type"])
                    # refresh the test name if it referenced the old field
                    if old_f and old_f["label"].lower() in (t.get("name") or "").lower():
                        t["name"] = re.sub(re.escape(old_f["label"]), new_f["label"],
                                           t.get("name", ""), flags=re.I)
                    elif "Search by" in (t.get("name") or ""):
                        t["name"] = re.sub(r"Search by .*?(=|$)",
                                           f"Search by {new_f['label']} ", t["name"])
                    n += 1

    if n == 0:
        # nothing matched — synthesize a fresh search test for the new field
        steps = [
            {"action": "open_search", "optional": True},
            {"action": "fill", "target": new_f["id"], "value": _sample_value(new_f["type"]), "optional": True},
            {"action": "click", "target": "Search"},
            {"action": "wait", "target": 2},
            {"action": "assert_no_errors"},
        ]
        scr = (doc.get("tests") or [{}])[0].get("screen") or entry.get("screenname")
        doc.setdefault("tests", []).append(
            {"screen": scr, "name": f"Search by {new_f['label']} = '{_sample_value(new_f['type'])}'",
             "steps": steps})
        return new_f, f"added a search test using {new_f['label']} ({new_f['id']})"

    where = f" (was {old_f['label']})" if old_f else ""
    return new_f, f"retargeted {n} search step{'s' if n != 1 else ''} to {new_f['label']} ({new_f['id']}){where}"


def _add_search(doc: dict, entry: dict, index: list[dict], new_q: str):
    new_f = resolve_field(_clean_field_phrase(new_q), index)
    if not new_f:
        return f"no field matching '{new_q.strip()}' on this screen"
    scr = (doc.get("tests") or [{}])[0].get("screen") or entry.get("screenname")
    steps = [
        {"action": "open_search", "optional": True},
        {"action": "fill", "target": new_f["id"], "value": _sample_value(new_f["type"]), "optional": True},
        {"action": "click", "target": "Search"},
        {"action": "wait", "target": 2},
        {"action": "assert_no_errors"},
    ]
    doc.setdefault("tests", []).append(
        {"screen": scr, "name": f"Search by {new_f['label']} = '{_sample_value(new_f['type'])}'",
         "steps": steps})
    return f"added a search test using {new_f['label']} ({new_f['id']})"


def _set_value(doc: dict, value: str):
    value = _strip_quotes(value)
    if not value:
        return None
    n = 0
    for t in doc.get("tests", []) or []:
        if not _is_search_test(t):
            continue
        for st in _fill_steps(t):
            st["value"] = value
            n += 1
    if n == 0:
        return None
    return f"set search value to '{value}' on {n} fill step{'s' if n != 1 else ''}"


def _apply_catalog_edits(yaml_text: str, entry: dict, lines: list[str]) -> tuple[str, list, list]:
    doc = _yaml.safe_load(yaml_text) or {"tests": []}
    if not isinstance(doc, dict):
        doc = {"tests": []}
    index = build_field_index(entry)
    applied, ignored = [], []
    for line in lines:
        desc = None
        m = _RX_ADD.search(line)
        if m:
            desc = _add_search(doc, entry, index, m.group("new"))
        if desc is None:
            m = _RX_RETARGET.search(line)
            if m:
                _f, desc = _retarget(doc, entry, index, m.group("new"), m.group("old"))
        if desc is None:
            m = _RX_VALUE.search(line)
            if m:
                desc = _set_value(doc, m.group("val") or m.group("val2") or "")
        if desc:
            applied.append(f'"{line}" — {desc}')
        else:
            ignored.append(line)

    # preserve leading comment block
    leading = []
    for ln in yaml_text.splitlines():
        if ln.startswith("#") or not ln.strip():
            leading.append(ln)
        else:
            break
    body = _yaml.safe_dump(doc, sort_keys=False, default_flow_style=False,
                           allow_unicode=True, width=100)
    out = ("\n".join(leading) + "\n" if leading else "") + body
    return out, applied, ignored


# ----------------------------------------------------------- public API

def edit_testcases(yaml_text: str, entry: dict | None, prompt: str,
                   use_llm: bool = False) -> EditResult:
    """Apply a natural-language edit to stored test cases.

    1. Run the generic prompt engine (screenshots, skips, waits, row asserts…).
    2. Take whatever it could not handle and try the catalog-aware field/value
       edits in this module.
    """
    if not prompt or not prompt.strip():
        return EditResult(yaml_text=yaml_text)

    pr = apply_prompt(yaml_text, prompt, use_llm=use_llm)
    applied = list(pr.applied)
    text = pr.yaml_text

    # Catalog-aware pass only runs when we actually know the screen.
    if entry:
        # Re-derive the unhandled instruction lines from the engine's report.
        leftovers = pr.ignored or []
        if leftovers:
            text, applied2, ignored2 = _apply_catalog_edits(text, entry, leftovers)
            applied.extend(applied2)
            ignored = ignored2
        else:
            ignored = []
    else:
        ignored = pr.ignored

    return EditResult(yaml_text=text, applied=applied, ignored=ignored,
                      llm_used=pr.llm_used, llm_error=pr.llm_error)
