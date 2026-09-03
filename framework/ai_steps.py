"""AI translation of the steps the deterministic rules could not handle.

The importer is rules-only by design: 48 regex rules translate the steps that
follow a recognisable shape, and everything else becomes a `todo` for a human.
On legacy prose sheets that leaves most of the file untranslated, which makes
the tool look like it is asking the tester to do the work itself.

This module is the second tier the two-tier design always assumed: the rules go
first because they are free, instant and deterministic, and only what they miss
is sent to Gemini. Steps are batched (one request per BATCH, not per step) and
grounded in the target screen's real fields and buttons from the catalog, so the
model returns ids that exist rather than plausible-sounding inventions.
"""
from __future__ import annotations

import json
import os
import re

BATCH = 25                       # steps per request — keeps prompts well inside limits
VALID_ACTIONS = {
    "click", "fill", "select", "check", "open_search", "wait", "screenshot",
    "assert_visible", "assert_not_visible", "assert_text", "assert_no_errors",
    "assert_rows_min", "assert_rows_max", "todo",
}


def available() -> bool:
    from framework import llm
    return llm.available()


def _screen_context(entry: dict | None) -> str:
    """Real ids from the catalog, so the model targets things that exist."""
    if not entry:
        return "(no catalog entry for this screen — use the wording from the step text)"
    def ids(key, cap):
        """Entries are dicts for fields/buttons but plain strings for grid
        columns, and buttons label themselves with `text` rather than `label`."""
        out = []
        for x in (entry.get(key) or [])[:cap]:
            if isinstance(x, str):
                if x.strip():
                    out.append(x.strip())
                continue
            if not isinstance(x, dict):
                continue
            i = x.get("id") or x.get("name")
            lbl = x.get("label") or x.get("text") or ""
            if i:
                out.append(f"{i}" + (f" ({lbl})" if lbl and lbl != i else ""))
        return out
    parts = []
    f = ids("fields", 60)
    if f: parts.append("FIELDS: " + ", ".join(f))
    for k in ("form_buttons", "topnav_buttons", "action_menu_items", "other_buttons"):
        b = ids(k, 30)
        if b: parts.append(f"{k.upper()}: " + ", ".join(b))
    g = ids("grid_columns", 40)
    if g: parts.append("GRID COLUMNS: " + ", ".join(g))
    return "\n".join(parts) or "(catalog entry has no fields or buttons)"


_PROMPT = """You convert manual QA test steps into automation steps for a \
retail back-office web app.

For EACH numbered step below, return one JSON object:
  {{"n": <the number>, "action": <action>, "target": <string>, "value": <string>}}

Allowed actions:
  click              press a button, menu item, tab or link
  fill               type a value into a field
  select             choose an option from a dropdown
  check              tick a checkbox
  open_search        open the search-criteria panel
  assert_visible     confirm something is shown
  assert_not_visible confirm something is hidden
  assert_no_errors   confirm the save/action produced no error
  assert_rows_min    confirm at least `value` rows came back
  screenshot         capture the screen
  todo               KEEP THIS when the text is not a UI action

RULES — follow all of them:
1. If the step text matches something in SCREEN ELEMENTS, copy that id exactly.
2. SCREEN ELEMENTS lists only the screen you land on first. Steps often act on an
edit or detail dialog opened from it, so a field named in the step that is NOT in the
list is still a real field. Do NOT return `todo` merely because you cannot find it —
use the wording from the step as the target.
3. A short noun phrase on its own ("Max Price", "New Tag (Yes/No)", "Tag type") is a
FIELD the tester fills: action `fill`, the phrase as `target` with any trailing
parenthetical hint removed, and `value` left empty for a human. If the phrase says
Yes/No or Enable/Disable, use `check` instead.
4. If the step names a value ("set Qty to 50"), put the field in `target` and 50 in `value`.
5. "verify ..." / "Make sure ... saved" / "Data saved properly" -> `assert_no_errors`.
6. Reserve `todo` for text that is genuinely not an instruction: business-rule
explanations, DB setup notes, permission lists, or descriptions of what a user "will"
be able to do. Do NOT invent a UI action for those.
7. Output ONLY a JSON array. No markdown fences, no commentary.

=== SCREEN: {screen} ===
{context}

=== STEPS ===
{steps}
"""


def _call(model_text: str, model: str | None = None, max_retries: int = 3):
    """Ask the model and parse its JSON reply.

    Provider choice lives in framework.llm, so this works against Azure AI
    Foundry or Gemini without changing anything here.
    """
    from framework import llm
    try:
        t = llm.complete(model_text, max_retries=max_retries)
    except llm.LLMError as e:
        raise RuntimeError(f"Step translation failed: {str(e)[:200]}") from None
    t = re.sub(r"^```(?:json)?\s*", "", t.strip())
    t = re.sub(r"\s*```\s*$", "", t)
    try:
        parsed = json.loads(t)
    except json.JSONDecodeError:
        # The model sometimes wraps the array in prose. Take the outermost
        # bracketed span rather than losing the whole batch: a dropped batch is
        # indistinguishable from "the model declined everything", which is how
        # the same input scored 35 conversions on one run and 59 on the next.
        m = re.search(r"\[.*\]", t, re.S)
        if not m:
            raise RuntimeError("Model did not return JSON") from None
        try:
            parsed = json.loads(m.group(0))
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Model did not return JSON: {str(e)[:120]}") from None
    # A reply may arrive as {"steps": [...]} instead of a bare array.
    if isinstance(parsed, dict):
        for key in ("steps", "results", "rows", "items", "translations"):
            if isinstance(parsed.get(key), list):
                return parsed[key]
    return parsed


def translate_todos(todos: list[str], screen: str, entry: dict | None = None,
                    errors: list | None = None) -> dict:
    """Translate a list of untranslated step texts.

    Returns {index_in_input: {action, target, value}} for the ones the model
    could turn into a real action. Indexes it omits, or returns `todo` for,
    are simply left alone — a step the model declines to guess at is a better
    outcome than a confident wrong click.

    Transport failures are appended to `errors` rather than silently dropped.
    An exhausted quota and a model that declines every row both produce zero
    conversions, and they need completely different responses — one is a
    billing problem, the other is the file. Collapsing them into "filled: 0"
    hid a dead API key behind what looked like careful judgement.
    """
    if not todos or not available():
        return {}
    context = _screen_context(entry)
    out: dict[int, dict] = {}
    for start in range(0, len(todos), BATCH):
        chunk = todos[start:start + BATCH]
        listing = "\n".join(f"{i}. {t[:300]}" for i, t in enumerate(chunk, start=1))
        try:
            rows = _call(_PROMPT.format(screen=screen, context=context, steps=listing))
        except Exception as e:
            if errors is not None:
                errors.append(str(e)[:200])
            continue                       # a failed batch just stays manual
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                n = int(row.get("n", 0))
            except (TypeError, ValueError):
                continue
            action = str(row.get("action") or "").strip().lower()
            if action not in VALID_ACTIONS:
                # gpt-4.1 intermittently emits the action as the KEY rather than
                # as the value of "action" — {"n": 3, "click": "Save"} instead of
                # {"n": 3, "action": "click", "target": "Save"}. Recover it
                # instead of discarding the row.
                for k, v in row.items():
                    if str(k).strip().lower() in VALID_ACTIONS:
                        action = str(k).strip().lower()
                        if not row.get("target") and isinstance(v, str):
                            row = dict(row, target=v)
                        break
            if not (1 <= n <= len(chunk)) or action not in VALID_ACTIONS or action == "todo":
                continue
            target = str(row.get("target") or "").strip()
            if not target and action not in ("open_search", "screenshot", "assert_no_errors"):
                continue
            # Duplicated n is routine in model output. The first answer is the
            # considered one; a later duplicate is usually a restatement or a
            # hallucinated extra row, so it must not overwrite.
            if out.get(start + n - 1):
                continue
            out[start + n - 1] = {
                "action": action,
                "target": target,
                "value": str(row.get("value") or "").strip(),
            }
    return out
