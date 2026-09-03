"""Goal-driven testing AGENT for Stratus BackOffice.

The difference from the script-runner (single_screen_runner): this does not
translate all steps up front and execute them blindly. It works like a human
tester who has never seen the screen — LOOK at the live page, THINK about the
goal, DO one action, OBSERVE what changed, ADAPT — until the goal is met.

That loop is what generalises to any mod: the agent reasons about the goal in
plain English rather than needing a perfectly-worded script, and it recovers
from surprises (dialogs, grids, detail forms) because it re-reads the screen
every step.

The hard part of this particular app is its widgets — virtualised SlickGrids,
custom checkboxes, per-row Edit buttons. Those live in ACT() as robust
primitives; the intelligence lives in DECIDE().
"""
from __future__ import annotations
import json
import re
import time
from dataclasses import dataclass

from framework.demo_runner import (
    DemoConfig, DemoResult, _Tracker, build_url,
)
from framework.crawl_runner import _login, _browser
from framework import llm


# --------------------------------------------------------------------------- #
#  OBSERVE — what the agent can see and act on right now.                      #
#  Returns the interactive controls, grid rows, tabs and the current title,   #
#  each tagged so DECIDE can reference them and ACT can find them again.       #
# --------------------------------------------------------------------------- #
_OBSERVE_JS = r"""
() => {
  const vis = el => {
    const r = el.getBoundingClientRect();
    return r.width > 1 && r.height > 1 && el.offsetParent !== null &&
           getComputedStyle(el).visibility !== 'hidden';
  };
  const near_label = el => {
    if (el.id) { const l = document.querySelector(`label[for="${el.id}"]`);
                 if (l && l.textContent.trim()) return l.textContent.trim(); }
    // a wrapping .form-group usually holds the label just above the control
    let p = el.closest('.form-group, .field, td, .col-sm-12, li');
    if (p) { const l = p.querySelector('label, legend');
             if (l && l.textContent.trim()) return l.textContent.trim(); }
    return '';
  };
  const clip = s => (s || '').trim().replace(/\s+/g, ' ').slice(0, 55);

  const controls = [];
  document.querySelectorAll(
    'button, input, select, textarea, a[href], [onclick], [role=checkbox]'
  ).forEach(el => {
    if (!vis(el)) return;
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    let label = near_label(el) || clip(el.getAttribute('aria-label') ||
                el.value || el.textContent || el.getAttribute('placeholder'));
    let kind = 'button';
    if (tag === 'select') kind = 'dropdown';
    else if (type === 'checkbox' || el.getAttribute('role') === 'checkbox' ||
             (el.className || '').includes('checkbox')) kind = 'checkbox';
    else if (tag === 'input' || tag === 'textarea') kind = 'input';
    const c = { kind, id: el.id || '', name: el.getAttribute('name') || '', label };
    if (tag === 'select') {
      c.options = [...el.options].map(o => (o.textContent || '').trim())
                    .filter(Boolean).slice(0, 8);
    }
    controls.push(c);
  });

  // SlickGrid rows: each opens a record via a per-row Edit button. Expose the
  // visible rows' first-cell text so DECIDE can pick one.
  const rows = [];
  const editBtns = document.querySelectorAll('button[name="Edit"]');
  let visibleEdits = 0;
  editBtns.forEach(b => { if (vis(b)) visibleEdits++; });
  document.querySelectorAll('.slick-row').forEach((r, i) => {
    if (!vis(r) || rows.length >= 12) return;
    const txt = [...r.querySelectorAll('.slick-cell')]
                  .map(c => c.textContent.trim()).filter(Boolean).slice(0, 4).join(' | ');
    rows.push({ rowIndex: rows.length, text: clip(txt) });
  });

  // Detail-screen tabs (Stratus uses .tabPanel / .cel-tabs / ui-tabs)
  const tabs = [];
  document.querySelectorAll('[data-title], .ui-tabs-anchor, .nav-tabs a').forEach(t => {
    if (!vis(t)) return;
    const name = clip(t.getAttribute('data-title') || t.textContent);
    if (name) tabs.push(name);
  });

  return {
    url: (location.hash || location.pathname).slice(0, 80),
    title: clip((document.querySelector('h1, h2') || {}).textContent || document.title),
    controls: controls.slice(0, 50),
    rows,
    hasGrid: editBtns.length > 0,
    visibleEditButtons: visibleEdits,
    tabs: [...new Set(tabs)].slice(0, 12),
  };
}
"""


# --------------------------------------------------------------------------- #
#  ACT — robust primitives. Each returns (ok: bool, message: str).            #
#  This is where the app's awkward widgets are tamed.                         #
# --------------------------------------------------------------------------- #
def _find_visible(page, target: str):
    """Locate a visible element by id, name, or exact text — in that order."""
    if not target:
        return None
    for sel in (f"#{target}", f"[name='{target}']"):
        try:
            loc = page.locator(sel).locator("visible=true").first
            if loc.count() > 0:
                return loc
        except Exception:
            pass
    try:
        loc = page.get_by_text(target, exact=False).locator("visible=true").first
        if loc.count() > 0:
            return loc
    except Exception:
        pass
    return None


def _act(page, a: dict):
    kind = a.get("action")
    tgt = str(a.get("target", "") or "").strip()
    val = str(a.get("value", "") or "").strip()
    try:
        # -- open a grid record -------------------------------------------- #
        if kind in ("edit_row", "click_row"):
            # SlickGrid keeps off-screen rows in the DOM but HIDDEN, so the plain
            # first match is unclickable. Filter to VISIBLE Edit buttons.
            btn = page.locator('button[name="Edit"]:visible')
            try:
                btn.first.wait_for(state="visible", timeout=8000)
            except Exception:
                return False, "no visible grid Edit button (search first?)"
            n = btn.count()
            try:
                idx = int(a.get("rowIndex", 0) or 0)
            except Exception:
                idx = 0
            pick = btn.nth(idx) if 0 <= idx < n else btn.first
            pick.click(timeout=4000)
            return True, "opened a record"

        loc = _find_visible(page, tgt)

        if kind in ("click", "press"):
            if not loc:
                return False, f"no element {tgt!r}"
            loc.click(timeout=2500)
            return True, f"clicked {tgt}"

        if kind == "fill":
            if not loc:
                return False, f"field {tgt!r} not found"
            loc.fill(val, timeout=2500)
            return True, f"filled {tgt}={val}"

        if kind == "select":
            if not loc:
                return False, f"dropdown {tgt!r} not found"
            try:
                loc.select_option(label=val, timeout=3000)
            except Exception:
                loc.select_option(value=val, timeout=3000)
            return True, f"selected {tgt}={val}"

        if kind == "check":
            # Stratus checkboxes are often custom-styled (a div/span, not a native
            # <input>), so .check() times out. Try native, then click the control,
            # then click its label text.
            if loc:
                try:
                    loc.check(timeout=2000)
                    return True, f"checked {tgt}"
                except Exception:
                    try:
                        loc.click(timeout=2000)
                        return True, f"toggled {tgt}"
                    except Exception:
                        pass
            try:
                page.get_by_text(tgt, exact=False).locator("visible=true").first.click(timeout=3000)
                return True, f"toggled via label {tgt}"
            except Exception as e:
                return False, f"checkbox {tgt}: {str(e)[:35]}"

        if kind == "open_tab":
            try:
                page.locator(f"[data-title='{tgt}'], text={tgt}").locator(
                    "visible=true").first.click(timeout=3000)
                return True, f"opened tab {tgt}"
            except Exception as e:
                return False, f"tab {tgt}: {str(e)[:30]}"

        if kind == "assert_visible":
            return (loc is not None), ("visible" if loc else f"{tgt!r} NOT visible")

        if kind in ("done", "give_up"):
            return True, kind

        return False, f"unknown action {kind!r}"
    except Exception as e:
        return False, str(e)[:70]


# --------------------------------------------------------------------------- #
#  DECIDE — one grounded next action toward the goal.                         #
# --------------------------------------------------------------------------- #
_DECIDE = """You are an automated QA agent testing a retail back-office web app.
You act ONE step at a time and adapt to what you see.

GOAL:
{goal}

CURRENT SCREEN:
  title: {title}
  interactive controls: {controls}
  grid rows (records you can open): {rows}
  tabs: {tabs}

WHAT YOU HAVE DONE:
{history}

Choose the SINGLE best next action. Reply with ONE JSON object:
{{"action":"click|fill|select|check|edit_row|open_tab|assert_visible|done|give_up",
  "target":"<exact id from controls if non-empty, else exact label>",
  "rowIndex":<integer, only for edit_row>,
  "value":"<value for fill/select>",
  "reason":"<one short phrase>"}}

Rules:
- For "target", copy the control's "id" if it is non-empty, otherwise its "label". Nothing else.
- For "select", "value" MUST be one of that dropdown's listed "options".
- To LIST records, click Search WITHOUT setting filter dropdowns first — filters narrow results and often return nothing. Only set a filter if you specifically need one record.
- If a search shows no grid rows, clear/Reset the criteria and Search again.
- To open a record from the grid, use "edit_row" with a rowIndex.
- To press a button (Search, Save, OK, Action, Edit), use "click".
- Never repeat an action that just failed — pick a different element or approach.
- Only "give_up" after at least 6 varied attempts; prefer to keep trying.
- Say "done" only when the GOAL is genuinely achieved.
Reply with only the JSON object."""


def _decide(goal, state, history):
    ctrls = []
    for c in state["controls"]:
        item = {"kind": c["kind"], "id": c.get("id", ""), "label": c.get("label", "")}
        if c.get("options"):
            item["options"] = c["options"]      # valid values for dropdowns
        ctrls.append(item)
    prompt = _DECIDE.format(
        goal=goal,
        title=state["title"][:70],
        controls=json.dumps(ctrls[:40], ensure_ascii=False),
        rows=json.dumps(state["rows"][:12], ensure_ascii=False),
        tabs=json.dumps(state["tabs"], ensure_ascii=False),
        history="\n".join(history[-7:]) or "(nothing yet)",
    )
    raw = llm.complete(prompt, max_retries=2)
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip()).strip()
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return {"action": "give_up", "reason": "could not decide"}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {"action": "give_up", "reason": "unparseable decision"}


# --------------------------------------------------------------------------- #
#  The agent loop.                                                            #
# --------------------------------------------------------------------------- #
@dataclass
class AgentGoal:
    name: str
    goal: str
    start_screen: str = ""          # catalog screenname to open before starting
    max_steps: int = 12


# --------------------------------------------------------------------------- #
#  GOAL SYNTHESIS — turn a messy human test scenario into ONE clear testing    #
#  goal the agent can actually pursue. This is the "AI understands the test    #
#  case" step: vague prose in, a focused objective out.                        #
# --------------------------------------------------------------------------- #
_SYNTH = """You turn a manual QA test scenario into ONE clear objective for an
automation agent that drives a retail back-office web app.

Scenario name: {name}
Screen: {screen}
The tester's raw steps (may be terse or vague):
{steps}

Write a SHORT, concrete testing objective (2-3 sentences) describing what to
verify on that screen — what to search for, which record to open, which
field/setting to check or change, and what confirms success. Focus on the ONE
main thing the scenario is really testing. Do not invent features not implied
by the steps. Reply with the objective text only, no preamble."""


def synthesize_goal(name: str, screen: str, step_texts: list[str]) -> str:
    steps = "\n".join(f"  - {s}" for s in step_texts if s) or "  (no steps given)"
    try:
        out = llm.complete(_SYNTH.format(name=name, screen=screen, steps=steps),
                           max_retries=2).strip()
        return out[:600] if out else name
    except Exception:
        return name


def _dismiss_stray_overlay(page):
    """A modal/alert left open blocks every later click via its overlay. Stratus
    alerts are jQuery-UI dialogs with an OK button in `.ui-dialog-buttonpane`
    (class `dialogOk`), e.g. "Please select a row". Close whatever is open so the
    agent can keep working. Returns True if it dismissed something."""
    try:
        overlay = page.locator(".ui-widget-overlay:visible")
        if overlay.count() == 0:
            return False
        # 1) the dialog's action button (OK / Yes / Cancel / No), then its close X
        for sel in (".ui-dialog:visible .dialogOk",
                    ".ui-dialog:visible .ui-dialog-buttonpane button",
                    ".ui-dialog:visible button:has-text('OK')",
                    ".ui-dialog:visible button:has-text('Cancel')",
                    ".ui-dialog:visible button:has-text('Close')",
                    ".ui-dialog:visible .ui-dialog-titlebar-close"):
            try:
                el = page.locator(sel).first
                if el.count() > 0 and el.is_visible():
                    el.click(timeout=1500)
                    page.wait_for_timeout(600)
                    return True
            except Exception:
                pass
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
        return True
    except Exception:
        return False


def _observe(page, settle_ms=0):
    if settle_ms:
        page.wait_for_timeout(settle_ms)
    # a leftover modal overlay makes the whole screen unclickable — clear it first
    _dismiss_stray_overlay(page)
    st = page.evaluate(_OBSERVE_JS)
    tries = 0
    # SPA re-renders empty the DOM briefly; a grid can also lag behind its Search.
    # Wait rather than give up while nothing — or no records — has rendered yet.
    while tries < 4 and ((not st["controls"] and not st["rows"]) or
                         (st.get("hasGrid") and not st["rows"]
                          and st.get("visibleEditButtons", 0) == 0)):
        page.wait_for_timeout(1500)
        st = page.evaluate(_OBSERVE_JS)
        tries += 1
    # Virtualised grids sometimes render Edit buttons without .slick-row text the
    # scraper can read. If records are clearly openable, surface them as rows so
    # the agent does not conclude "no records".
    if not st["rows"] and st.get("visibleEditButtons", 0) > 0:
        st["rows"] = [{"rowIndex": i, "text": f"record {i+1}"}
                      for i in range(min(st["visibleEditButtons"], 5))]
    return st


def _run_one_goal(page, g: AgentGoal, t: _Tracker):
    t.section(f"AGENT GOAL: {g.name}")
    t.info(g.goal)
    history = []
    consec_fail = 0
    for step in range(1, g.max_steps + 1):
        state = _observe(page)
        act = _decide(g.goal, state, history)
        a = act.get("action")
        if a == "done":
            t.ok(f"GOAL MET — {act.get('reason', '')}")
            return True
        if a == "give_up":
            t.fail(g.name, f"agent gave up — {act.get('reason', '')}", page=page)
            return False
        ok, msg = _act(page, act)
        # Consecutive failures usually mean the screen is stuck behind a modal or
        # the agent is targeting something that is not there. Force a recovery so
        # it does not burn its whole step budget hammering a dead element.
        if not ok:
            consec_fail += 1
            if consec_fail >= 2:
                if _dismiss_stray_overlay(page):
                    history.append(f"step{step}: (cleared a blocking dialog)")
                consec_fail = 0
        else:
            consec_fail = 0
        # After navigation / opening a record, let the screen render.
        page.wait_for_timeout(3200 if a in ("click", "edit_row", "click_row", "open_tab") else 2000)
        tgt = act.get("target") or act.get("rowIndex", "")
        line = f"step{step}: {a} {tgt} -> {'ok' if ok else 'FAILED: ' + msg}"
        t.info(f"  {line}  [{act.get('reason','')[:50]}]")
        history.append(line)
        try:
            safe = "".join(c if c.isalnum() else "_" for c in f"{g.name}_{step}_{a}")[:44]
            t.screenshot(page, safe, quiet=True)
        except Exception:
            pass
    t.fail(g.name, "reached step limit before goal", page=page)
    return False


def run_agent(cfg: DemoConfig, goals: list[AgentGoal],
              on_event=None, catalog=None) -> DemoResult:
    """Log in once, then let the agent pursue each goal in turn."""
    t = _Tracker(on_event, cfg)
    t.banner(f"Stratus QA — AGENT testing against {cfg.base_url}")

    # resolve start-screen hrefs from the catalog if given
    href_by_screen = {}
    if catalog:
        for s in (catalog.get("screens") or []):
            href_by_screen[(s.get("screenname") or "").lower()] = s.get("data_href", "")

    from framework.demo_runner import build_url as _bu
    shell_url = _bu(cfg.base_url, "/wrmsscreen")

    passed = failed = 0
    try:
        with _browser(headless=cfg.headless) as page:
            t.section("Authenticating")
            landed = ""
            for attempt in range(3):
                try:
                    landed = _login(page, cfg, t)
                except Exception as e:
                    landed = str(e)
                # a real login leaves login.jsp for the app shell; if still on the
                # login page, the submit did not take — wait and retry.
                if "login.jsp" not in (page.url or "").lower():
                    break
                page.wait_for_timeout(2500)
            if "login.jsp" in (page.url or "").lower():
                t.fail("login", f"still on login page after retries ({page.url})", page=page)
                return _result(t, 0, 1)
            t.ok(f"logged in ({(page.url or '').split('/')[-1]})")

            for g in goals:
                # open the goal's start screen if it names one. Go straight to the
                # screen URL — bouncing through the shell first leaves the SPA on a
                # stale hash route that a same-document hash change won't re-render.
                href = href_by_screen.get((g.start_screen or "").lower(), "")
                if href:
                    try:
                        page.goto(_bu(cfg.base_url, href),
                                  wait_until="domcontentloaded", timeout=25000)
                        page.wait_for_timeout(3500)
                    except Exception as e:
                        t.warn(f"could not open {g.start_screen}: {str(e)[:50]}")
                ok = _run_one_goal(page, g, t)
                passed += int(ok)
                failed += int(not ok)
    finally:
        pass

    return _result(t, passed, failed)


def _result(t, passed, failed) -> DemoResult:
    total = passed + failed
    verdict = failed == 0 and total > 0
    t.section(f"{'PASS' if verdict else 'PARTIAL'} — {passed}/{total} goal(s) met")
    return DemoResult(passed=verdict, steps_total=total,
                      steps_passed=passed, steps_failed=failed, duration_s=0)
