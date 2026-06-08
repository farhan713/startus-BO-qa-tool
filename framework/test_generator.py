"""Generate comprehensive tests for any screen — 100% catalog-driven.

The key rule: NEVER hardcode selectors. Every test step refers to an
element that's actually in the catalog for THIS screen. If the catalog
shows the screen has 13 fields → we generate fill tests for those 13.
If the catalog has 0 fields → we don't generate fill tests.

Different recipes per screen type. Configurations are MAXIMUM possible:
we generate every reasonable test for every catalog element.
"""
from __future__ import annotations

from dataclasses import dataclass


# Sample values per field type
SAMPLE_VALUES = {
    "text":     "QA",
    "email":    "qa@example.com",
    "number":   "1",
    "date":     "01/01/2025",
    "textarea": "QA automation test",
    "password": "TestPass1",
}


@dataclass
class GeneratedTest:
    name: str
    steps: list


# ============================================================ helpers

def _safe_value(field: dict) -> str:
    """Sample value for a catalog field. For selects, return first non-blank option."""
    ftype = (field.get("type") or "text").lower()
    if ftype == "select":
        for opt in field.get("options") or []:
            o = (opt or "").strip()
            if o and o.lower() not in ("select", "select…", "all", "select a date range", ""):
                return o
        return ""
    if ftype == "checkbox":
        return "true"
    return SAMPLE_VALUES.get(ftype, "QA")


def _field_label(field: dict) -> str:
    return field.get("label") or field.get("name") or field.get("id") or "field"


def _btn_label(btn: dict) -> str:
    return btn.get("text") or btn.get("id") or btn.get("name") or "button"


def _btn_target(btn: dict) -> str:
    """Best target string to pass to the 'click' action."""
    return btn.get("id") or btn.get("text") or btn.get("name") or ""


# ============================================================ LIST screens

def generate_for_list(cat: dict, safe_mode: bool = True) -> list[GeneratedTest]:
    """Comprehensive tests for a list screen — purely from catalog."""
    out: list[GeneratedTest] = []
    label = cat.get("label") or cat.get("screenname")
    fields = cat.get("fields") or []
    topnav = cat.get("topnav_buttons") or []
    action_items = cat.get("action_menu_items") or []
    grid_columns = cat.get("grid_columns") or []

    # Map text-input and select fields (the searchable ones)
    text_fields = [f for f in fields
                   if (f.get("type") or "text").lower()
                   in ("text", "email", "number", "textarea")
                   and (f.get("id") or f.get("name"))]
    select_fields = [f for f in fields
                     if (f.get("type") or "").lower() == "select"
                     and (f.get("id") or f.get("name"))
                     and f.get("options")]
    checkbox_fields = [f for f in fields
                       if (f.get("type") or "").lower() == "checkbox"
                       and (f.get("id") or f.get("name"))]

    # Locate well-known buttons across ALL button locations in the catalog
    def _find_btn(*candidates):
        all_btns = (topnav + action_items
                  + (cat.get("form_buttons") or [])
                  + (cat.get("other_buttons") or []))
        for b in all_btns:
            if (b.get("id") in candidates or b.get("text") in candidates):
                return b
        return None

    btn_search = _find_btn("Search", "search")
    btn_reset = _find_btn("Reset", "reset")
    btn_close = _find_btn("Close", "close")
    btn_new   = _find_btn("New", "new")
    btn_action = _find_btn("Action", "action")
    btn_print = _find_btn("PrintList", "Print List", "print")
    btn_edit = _find_btn("Edit")
    btn_delete = _find_btn("Delete")

    # ---- 1. Render + smoke ----
    out.append(GeneratedTest(
        name=f"Render check — {label} ({len(fields)} fields, {len(topnav)} buttons)",
        steps=[
            {"action": "assert_no_errors"},
            {"action": "screenshot", "target": f"{cat['screenname']}_initial"},
        ],
    ))

    # ---- 2. Grid columns sanity (if catalog has grid columns) ----
    if grid_columns:
        out.append(GeneratedTest(
            name=f"Grid present ({len(grid_columns)} columns: {', '.join(grid_columns[:4])}…)",
            steps=[{"action": "assert_visible", "target": "table.ui-jqgrid-btable, .ui-jqgrid"}],
        ))

    # ---- 3. Search-by-each-field tests (only if a Search button exists) ----
    if btn_search:
        # 3a. Empty search — just click Search with no filters
        out.append(GeneratedTest(
            name="Search with no filters (all results)",
            steps=[
                {"action": "open_search", "optional": True},   # lenient — may not exist
                {"action": "click", "target": _btn_target(btn_search)},
                {"action": "wait", "target": 2},
                {"action": "assert_no_errors"},
            ],
        ))

        # 3b. One test per text field — fill + search
        for f in text_fields[:8]:   # cap to 8 to keep run time sane
            fid = f.get("id") or f.get("name")
            val = _safe_value(f)
            out.append(GeneratedTest(
                name=f"Search by {_field_label(f)} = {val!r}",
                steps=[
                    {"action": "open_search", "optional": True},
                    {"action": "fill", "target": fid, "value": val, "optional": True},
                    {"action": "click", "target": _btn_target(btn_search)},
                    {"action": "wait", "target": 2},
                    {"action": "assert_no_errors"},
                ],
            ))

        # 3c. One test per dropdown — pick first real option
        for f in select_fields[:5]:
            fid = f.get("id") or f.get("name")
            val = _safe_value(f)
            if not val: continue
            out.append(GeneratedTest(
                name=f"Filter by {_field_label(f)} = {val!r}",
                steps=[
                    {"action": "open_search", "optional": True},
                    {"action": "select", "target": fid, "value": val},
                    {"action": "click", "target": _btn_target(btn_search)},
                    {"action": "wait", "target": 2},
                    {"action": "assert_no_errors"},
                ],
            ))

        # 3d. Checkbox filters (toggle each)
        for f in checkbox_fields[:3]:
            fid = f.get("id") or f.get("name")
            out.append(GeneratedTest(
                name=f"Toggle filter checkbox {_field_label(f)!r}",
                steps=[
                    {"action": "open_search", "optional": True},
                    {"action": "click", "target": fid, "optional": True},
                    {"action": "click", "target": _btn_target(btn_search)},
                    {"action": "wait", "target": 2},
                    {"action": "assert_no_errors"},
                ],
            ))

    # ---- 4. Reset (if Reset button exists) ----
    if btn_reset:
        out.append(GeneratedTest(
            name="Click Reset (clear filters)",
            steps=[
                {"action": "click", "target": _btn_target(btn_reset)},
                {"action": "assert_no_errors"},
            ],
        ))

    # ---- 5. Action menu (if there's an Action dropdown) ----
    if btn_action and action_items:
        item_names = [_btn_label(b) for b in action_items[:5]]
        out.append(GeneratedTest(
            name=f"Open Action menu (items: {', '.join(item_names)})",
            steps=[
                {"action": "click", "target": _btn_target(btn_action)},
                {"action": "wait", "target": 1},
                {"action": "assert_no_errors"},
            ],
        ))

    # ---- 6. Print List (safe — read-only) ----
    if btn_print:
        steps = []
        if btn_action: steps.append({"action": "click", "target": _btn_target(btn_action)})
        steps.append({"action": "wait", "target": 1})
        steps.append({"action": "click", "target": _btn_target(btn_print)})
        steps.append({"action": "wait", "target": 2})
        steps.append({"action": "assert_no_errors"})
        out.append(GeneratedTest(name="Trigger Print List action", steps=steps))

    # ---- 7. NEW button (only verifies it opens a detail screen — skipped if safe) ----
    # Even in safe mode, clicking New just opens a form — no data is written.
    if btn_new:
        out.append(GeneratedTest(
            name="Click New (opens detail form, then close)",
            steps=[
                {"action": "click", "target": _btn_target(btn_new)},
                {"action": "wait", "target": 3},
                {"action": "assert_no_errors"},
                {"action": "screenshot", "target": f"{cat['screenname']}_new_form"},
            ],
        ))

    return out


# ============================================================ DETAIL screens

def generate_for_detail(cat: dict, safe_mode: bool = True) -> list[GeneratedTest]:
    out: list[GeneratedTest] = []
    label = cat.get("label") or cat.get("screenname")
    fields = cat.get("fields") or []
    form_btns = cat.get("form_buttons") or []
    topnav = cat.get("topnav_buttons") or []

    text_fields = [f for f in fields
                   if (f.get("type") or "text").lower()
                   in ("text", "email", "number", "textarea")
                   and (f.get("id") or f.get("name"))]
    select_fields = [f for f in fields
                     if (f.get("type") or "").lower() == "select"
                     and (f.get("id") or f.get("name"))
                     and f.get("options")]
    checkbox_fields = [f for f in fields
                       if (f.get("type") or "").lower() == "checkbox"
                       and (f.get("id") or f.get("name"))]

    def _find_btn(*names):
        for b in form_btns + topnav + (cat.get("other_buttons") or []):
            if (b.get("id") in names or b.get("text") in names): return b
        return None

    btn_save = _find_btn("Save")
    btn_cancel = _find_btn("Cancel")
    btn_close = _find_btn("Close")
    btn_delete = _find_btn("Delete")

    # ---- 1. Render ----
    out.append(GeneratedTest(
        name=f"Render check — {label} ({len(fields)} fields)",
        steps=[
            {"action": "assert_no_errors"},
            {"action": "screenshot", "target": f"{cat['screenname']}_initial"},
        ],
    ))

    # ---- 2. Fill every text field individually (validates each accepts input) ----
    for f in text_fields[:15]:
        fid = f.get("id") or f.get("name")
        val = _safe_value(f)
        out.append(GeneratedTest(
            name=f"Fill field {_field_label(f)!r} with {val!r}",
            steps=[
                {"action": "fill", "target": fid, "value": val},
                {"action": "assert_no_errors"},
            ],
        ))

    # ---- 3. Try each dropdown ----
    for f in select_fields[:8]:
        fid = f.get("id") or f.get("name")
        val = _safe_value(f)
        if not val: continue
        out.append(GeneratedTest(
            name=f"Select dropdown {_field_label(f)!r} = {val!r}",
            steps=[
                {"action": "select", "target": fid, "value": val},
                {"action": "assert_no_errors"},
            ],
        ))

    # ---- 4. Toggle each checkbox ----
    for f in checkbox_fields[:5]:
        fid = f.get("id") or f.get("name")
        out.append(GeneratedTest(
            name=f"Toggle checkbox {_field_label(f)!r}",
            steps=[
                {"action": "click", "target": fid, "optional": True},
                {"action": "assert_no_errors"},
            ],
        ))

    # ---- 5. Fill ALL fields together (full-form test) ----
    if text_fields:
        steps = []
        for f in text_fields[:10]:
            steps.append({
                "action": "fill",
                "target": f.get("id") or f.get("name"),
                "value": _safe_value(f),
                "optional": True,
            })
        steps.append({"action": "assert_no_errors"})
        out.append(GeneratedTest(name=f"Fill all {len(steps)-1} text fields together", steps=steps))

    # ---- 6. Cancel (safe — does not save) ----
    if btn_cancel:
        out.append(GeneratedTest(
            name="Click Cancel (no save)",
            steps=[
                {"action": "click", "target": _btn_target(btn_cancel)},
                {"action": "wait", "target": 1},
                {"action": "assert_no_errors"},
            ],
        ))

    # ---- 7. Save (destructive — only if NOT safe mode) ----
    if not safe_mode and btn_save:
        steps = []
        for f in text_fields[:8]:
            steps.append({
                "action": "fill",
                "target": f.get("id") or f.get("name"),
                "value": _safe_value(f),
                "optional": True,
            })
        steps.append({"action": "click", "target": _btn_target(btn_save)})
        steps.append({"action": "wait", "target": 2})
        steps.append({"action": "assert_no_errors"})
        out.append(GeneratedTest(name="⚠ Save with sample data (writes to DB!)", steps=steps))

    return out


# ============================================================ REPORT screens

def generate_for_report(cat: dict, safe_mode: bool = True) -> list[GeneratedTest]:
    out: list[GeneratedTest] = []
    label = cat.get("label") or cat.get("screenname")
    fields = cat.get("fields") or []
    all_btns = (cat.get("topnav_buttons") or []) + (cat.get("form_buttons") or [])

    out.append(GeneratedTest(
        name=f"Render report — {label}",
        steps=[
            {"action": "assert_no_errors"},
            {"action": "screenshot", "target": f"{cat['screenname']}_initial"},
        ],
    ))

    # Fill any visible filter inputs
    text_fields = [f for f in fields
                   if (f.get("type") or "text").lower() in ("text", "date", "number")
                   and (f.get("id") or f.get("name"))]
    for f in text_fields[:5]:
        fid = f.get("id") or f.get("name")
        val = _safe_value(f)
        out.append(GeneratedTest(
            name=f"Fill filter {_field_label(f)!r} = {val!r}",
            steps=[
                {"action": "fill", "target": fid, "value": val, "optional": True},
                {"action": "assert_no_errors"},
            ],
        ))

    # Click Run / Generate / OK if present
    for b in all_btns:
        text = (b.get("text") or "").strip()
        if text in ("Run", "Generate", "Submit", "OK", "Go", "Search"):
            out.append(GeneratedTest(
                name=f"Click {text}",
                steps=[
                    {"action": "click", "target": _btn_target(b)},
                    {"action": "wait", "target": 4},
                    {"action": "assert_no_errors"},
                ],
            ))
            break
    return out


# ============================================================ WIZARD / OTHER

def generate_for_wizard(cat: dict, safe_mode: bool = True) -> list[GeneratedTest]:
    out: list[GeneratedTest] = []
    label = cat.get("label") or cat.get("screenname")
    out.append(GeneratedTest(
        name=f"Render wizard — {label}",
        steps=[
            {"action": "assert_no_errors"},
            {"action": "screenshot", "target": f"{cat['screenname']}_initial"},
        ],
    ))
    # Click Next if present
    all_btns = (cat.get("topnav_buttons") or []) + (cat.get("form_buttons") or [])
    for b in all_btns:
        if (b.get("text") or "") in ("Next", "Continue", "→"):
            out.append(GeneratedTest(
                name="Advance to next step",
                steps=[
                    {"action": "click", "target": _btn_target(b)},
                    {"action": "wait", "target": 2},
                    {"action": "assert_no_errors"},
                ],
            ))
            break
    return out


def generate_for_other(cat: dict, safe_mode: bool = True) -> list[GeneratedTest]:
    """Catch-all for dashboards, unknown types — render-check only."""
    label = cat.get("label") or cat.get("screenname")
    return [GeneratedTest(
        name=f"Render check — {label}",
        steps=[
            {"action": "assert_no_errors"},
            {"action": "screenshot", "target": f"{cat['screenname']}_initial"},
        ],
    )]


# ============================================================ dispatch

def generate_tests(cat: dict, safe_mode: bool = True) -> list[GeneratedTest]:
    stype = (cat.get("type") or "other").lower()
    if stype == "list":   return generate_for_list(cat, safe_mode)
    if stype == "detail": return generate_for_detail(cat, safe_mode)
    if stype == "report": return generate_for_report(cat, safe_mode)
    if stype == "wizard": return generate_for_wizard(cat, safe_mode)
    return generate_for_other(cat, safe_mode)
