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
    // Skip fields the tester cannot edit — trying to type into them just times out.
    if ((tag === 'input' || tag === 'select' || tag === 'textarea') &&
        (el.disabled || el.readOnly)) return;
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

  // A visible dialog/alert is how Stratus reports an action's outcome
  // ("saved", "please select a row", a validation error). Surface its text so
  // the agent can VERIFY a save worked instead of assuming a click succeeded.
  // Is this a detail FORM (editing one record — has a Save/Cancel) or a LIST?
  const btnText = b => (b.value || b.textContent || '').trim();
  const hasSave = [...document.querySelectorAll('button, input[type=button], input[type=submit], a')]
                    .some(b => vis(b) && /^(save|update)$/i.test(btnText(b)));
  const hasCancel = [...document.querySelectorAll('button, input[type=button], a')]
                      .some(b => vis(b) && /^(cancel|close)$/i.test(btnText(b)));
  // A detail form has a SAVE button. Do NOT treat a list's search panel as a
  // detail form just because it has filter dropdowns and a Close button — that
  // mislabelling makes the agent try to Close instead of Search.
  const isDetail = hasSave;
  const screen_kind = isDetail
      ? 'detail form (editing a record — change fields, then Save to persist)'
      : (editBtns.length ? 'list (grid of records — click Search then open one with edit_row)'
                         : 'a screen with no grid — search or navigate to find records');

  let message = '';
  const dlg = [...document.querySelectorAll('.ui-dialog, #dialog, .modal, [role=dialog]')]
                .find(d => vis(d));
  if (dlg) {
    const body = dlg.querySelector('.ui-dialog-content, .modal-body, p') || dlg;
    message = clip(body.textContent).slice(0, 120);
  }

  return {
    url: (location.hash || location.pathname).slice(0, 80),
    title: clip((document.querySelector('h1, h2') || {}).textContent || document.title),
    controls: controls.slice(0, 50),
    rows,
    hasGrid: editBtns.length > 0,
    visibleEditButtons: visibleEdits,
    tabs: [...new Set(tabs)].slice(0, 12),
    message,
    screen_kind,
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
            # Wait for the detail form to actually render (a Save/Cancel button or
            # several inputs) so the next observation is not a half-drawn screen.
            for _ in range(6):
                page.wait_for_timeout(800)
                try:
                    ready = page.evaluate(
                        "() => { const b=[...document.querySelectorAll('button,input,a')]"
                        ".some(e=>e.offsetParent && /^(save|cancel|close)$/i.test((e.value||e.textContent||'').trim()));"
                        " const f=document.querySelectorAll('select:not([style*=none]), input[type=text]').length;"
                        " return b || f>=4; }")
                    if ready:
                        break
                except Exception:
                    pass
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
            # The agent sometimes tries to type into a dropdown. Auto-correct:
            # if the target is a <select>, choose a different option instead.
            try:
                if (loc.evaluate("el => el.tagName") or "").lower() == "select":
                    opts = loc.evaluate(
                        "el => [...el.options].map(o => o.textContent.trim()).filter(Boolean)")
                    cur = loc.evaluate("el => (el.selectedOptions[0]||{}).textContent||''").strip()
                    pick = next((o for o in opts if o and o != cur), None)
                    if pick:
                        loc.select_option(label=pick, timeout=2500)
                        return True, f"selected {tgt}={pick} (was a dropdown)"
            except Exception:
                pass
            loc.fill(val, timeout=1500)
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
  you are on: {screen_kind}
  on-screen message/dialog: {message}

WHAT YOU HAVE DONE:
{history}

Choose the SINGLE best next action. Reply with ONE JSON object:
{{"action":"click|fill|select|check|edit_row|open_tab|assert_visible|done|give_up",
  "target":"<exact id from controls if non-empty, else exact label>",
  "rowIndex":<integer, only for edit_row>,
  "value":"<value for fill/select>",
  "reason":"<one short phrase>"}}

Rules:
- CRITICAL: "target" MUST be copied verbatim from the "interactive controls" list
  above (its id if non-empty, else its label). NEVER invent a field name or guess an
  id — if the field you want is not listed, it is not on this screen; pick from what
  IS listed or take a different step.
- For "select", "value" MUST be one of that dropdown's listed "options".
- To LIST records, click Search WITHOUT setting filter dropdowns first — filters narrow results and often return nothing. Only set a filter if you specifically need one record.
- If a search shows no grid rows, clear/Reset the criteria and Search again.
- To open a record from the grid, use "edit_row" with a rowIndex.
- Once you are on a DETAIL FORM, do NOT open another record — make the change and Save.
- To persist changes, click the button whose label is exactly "Save" (NOT "Action",
  which opens a menu). Only fill/select fields that exist as controls above.
- To press a button (Search, Save, OK, Action, Edit), use "click".
- Never repeat an action that just failed — pick a different element or approach.
- Only "give_up" after at least 6 varied attempts; prefer to keep trying.
- VERIFY before finishing: after clicking Save, look at the on-screen message and
  where you are. A save that CLOSES the detail form (you are back on the list/grid)
  with NO error message is a SUCCESSFUL save — report "done". Do NOT try to re-find
  the edited field on the list; the field only exists on the detail form.
- If a message names an error or validation problem, the save FAILED — fix the cause
  (e.g. a required field) and Save again; do not report done.
- Say "done" once the field change was made and Saved with no error showing.
Reply with only the JSON object."""


def _decide(goal, state, history, stuck_hint=""):
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
        history=("\n".join(history[-7:]) or "(nothing yet)") + ("\n" + stuck_hint if stuck_hint else ""),
        message=(state.get("message") or "(none)")[:120],
        screen_kind=state.get("screen_kind", "other"),
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
_SYNTH = """You turn a manual QA test scenario into ONE clear, ACHIEVABLE objective
for an automation agent that drives a retail back-office web app.

Scenario name: {name}
Screen: {screen}
The tester's raw steps (may be terse or vague):
{steps}

Write a SHORT objective (2-3 sentences) the agent can actually complete on that
screen. Follow these rules:
- Pick the ONE main thing the scenario verifies. Do not chain many sub-flows.
- If it is a list screen, the objective should be: search to load records (with NO
  filters, so results appear), open one record via its Edit button, then do the one
  field change or check the scenario is about, and Save.
- Name a concrete success signal (e.g. "the record saves with no error message",
  "the field X is visible on the detail form").
- Do not invent features not implied by the steps. Keep it doable in ~8 actions.
Reply with the objective text only, no preamble."""


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
    last_actions = []
    for step in range(1, g.max_steps + 1):
        state = _observe(page)
        # If the agent has repeated the same action several times with no progress,
        # it is stuck in a loop (e.g. re-opening the same record). Tell it plainly.
        stuck_hint = ""
        if len(last_actions) >= 3 and len(set(last_actions[-3:])) == 1:
            stuck_hint = (f"NOTE: '{last_actions[-1]}' has repeated 3 times without progressing. "
                          f"Pick a DIFFERENT action or element this step. If you are already on a "
                          f"detail form (fields + Save visible), change a field and Save. Keep trying "
                          f"other options — do not give up yet.")
        act = _decide(g.goal, state, history, stuck_hint)
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
        last_actions.append(f"{a}:{tgt}")
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

    def _login_verified(page):
        landed = ""
        for _ in range(3):
            try:
                landed = _login(page, cfg, t)
            except Exception as e:
                landed = str(e)
            if "login.jsp" not in (page.url or "").lower():
                return True
            page.wait_for_timeout(2500)
        return "login.jsp" not in (page.url or "").lower()

    passed = failed = 0
    # Each goal runs in its OWN fresh browser + login. A long multi-goal session
    # on this heavyweight jQuery app accumulates modal/SPA state that degrades
    # later goals (the same goal passes alone but fails after others). Isolation
    # costs one login per goal but makes every goal behave like the clean case.
    for gi, g in enumerate(goals):
        try:
            with _browser(headless=cfg.headless) as page:
                if gi == 0:
                    t.section("Authenticating")
                if not _login_verified(page):
                    t.fail(g.name, f"login failed ({page.url})", page=page)
                    failed += 1
                    continue
                if gi == 0:
                    t.ok(f"logged in ({(page.url or '').split('/')[-1]})")
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
        except Exception as e:
            t.fail(g.name, f"session error: {str(e)[:60]}")
            failed += 1

    return _result(t, passed, failed)


def _result(t, passed, failed) -> DemoResult:
    total = passed + failed
    verdict = failed == 0 and total > 0
    t.section(f"{'PASS' if verdict else 'PARTIAL'} — {passed}/{total} goal(s) met")
    return DemoResult(passed=verdict, steps_total=total,
                      steps_passed=passed, steps_failed=failed, duration_s=0)
