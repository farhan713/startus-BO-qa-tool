"""Generic Stratus crawler — tests EVERY screen, with NO per-screen code.

Strategy:
  1. Login once
  2. Read the sidebar menu — every <a> with `screenname` + `data-href`
     is a screen we can navigate to. Tells us the full catalog.
  3. Classify each screen by URL pattern:
       /listScreen/   → list pattern    (grid + buttons + search)
       /detailScreen/ → detail pattern  (form + Save/Cancel)
       /dtlScreen/    → detail pattern
       /wizardScreen/ → wizard pattern  (steps + Next)
       /reportScreen/ → report pattern  (filters + Run + Export)
       (other)        → generic         (just must render without errors)
  4. For each screen, run the right pattern of generic tests.
  5. If user-provided test cases exist for a screen, ALSO run those.
  6. Stream per-screen results to the UI as they finish.

The Customer work taught us the conventions; this runner uses them
universally. Adding new screens to Stratus = automatically tested. No
code changes required.

================================================================ CUSTOM TEST CASES

YAML format:
    tests:
      - screen: customerlist        # screenname to apply to
        name: "Search SMITH"        # human label
        steps:
          - { action: open_search }
          - { action: fill, target: LastName, value: SMITH }
          - { action: click, target: Search }
          - { action: assert_no_errors }

Supported actions:
    open               navigate to screen URL
    open_search        click Show Search Criteria
    fill               fill input by id/name (target=id, value=text)
    click              click element by id/name/text (target=selector or text)
    select             select dropdown option by label
    wait               sleep N seconds (target=secs)
    assert_visible     assert selector visible (target=selector)
    assert_text        assert page body contains text (target=text)
    assert_no_errors   assert no error words on page
    assert_rows_min    assert grid has at least N rows (target=N)
    assert_rows_max    assert grid has at most N rows (target=N)
    screenshot         capture screenshot (target=name)
"""
from __future__ import annotations

import json
import time
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from playwright.sync_api import sync_playwright

from framework.demo_runner import (
    DemoConfig, DemoResult, StepEvent, _emit, _Tracker,
    build_url, _extract_mid,
)


# ============================================================ screen catalog

@dataclass
class ScreenSpec:
    screenname: str         # e.g. "customerlist"
    label: str              # display label
    data_href: str          # SPA route
    type: str               # 'list' | 'detail' | 'wizard' | 'report' | 'other'

    @property
    def full_url_path(self) -> str:
        return self.data_href


@dataclass
class ScreenResult:
    spec: ScreenSpec
    passed: bool
    rendered: bool = False
    duration_s: float = 0.0
    counts: dict = field(default_factory=dict)
    error: str | None = None
    error_words: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    screenshot: str | None = None


# ============================================================ universal pattern checks

# Selectors that indicate "yes, a Stratus screen is rendered."
RENDERED_HINTS = ", ".join([
    "#topNav",
    "#topNav button",
    ".screen-title-header",
    "h4.screen-title-header",
    "#componentFormId",
    ".ui-jqgrid",
    "table.ui-jqgrid-btable",
    ".cel-tabs",
    "fieldset legend",
])

ERROR_WORDS = [
    "session expired", "unauthorized", "not authorized",
    "access denied", "forbidden", "permission",
    "internal server error", "exception:", "stack trace",
    "http status 500", "http status 404",
]


def _analyze_screen(page) -> dict:
    """Generic JS that works for any Stratus screen."""
    return page.evaluate("""() => {
        const visible = (e) => {
            const r = e.getBoundingClientRect();
            return r.width > 0 && r.height > 0;
        };
        const txt = (e, n=30) => {
            const t = (e.innerText || '').trim();
            return t.length > n ? t.slice(0,n-1)+'…' : t;
        };
        const buttons = Array.from(document.querySelectorAll('button:not([disabled])')).filter(visible);
        const inputs  = Array.from(document.querySelectorAll('input:not([type=hidden]):not([disabled])')).filter(visible);
        const links   = Array.from(document.querySelectorAll('a')).filter(visible);
        return {
            url: location.href, title: document.title,
            buttons: buttons.length,
            inputs:  inputs.length,
            links:   links.length,
            hasGrid:    !!document.querySelector('.ui-jqgrid, table.ui-jqgrid-btable'),
            hasForm:    !!document.querySelector('form#componentFormId, form'),
            hasTopNav:  !!document.querySelector('#topNav button'),
            hasTitle:   !!document.querySelector('.screen-title-header, h4.screen-title-header'),
            hasNew:     !!document.querySelector('#topNav #New'),
            hasClose:   !!document.querySelector('#topNav #Close, #Close'),
            hasSave:    !!document.querySelector('button#Save'),
            hasCancel:  !!document.querySelector('button#Cancel'),
            hasAction:  !!document.querySelector('#topNav .action-btn, .dropdown-toggle.action-btn'),
            hasSearch:  !!document.querySelector('#Search, button#Search'),
            topButtonLabels: buttons.slice(0, 8).map(b => txt(b, 20)).filter(t => t),
            bodyTextStart: (document.body.innerText || '').trim().slice(0, 300),
        };
    }""")


def _classify(href: str) -> str:
    h = (href or "").lower()
    if "/listscreen/"   in h: return "list"
    if "/detailscreen/" in h or "/dtlscreen/" in h: return "detail"
    if "/wizardscreen/" in h: return "wizard"
    if "/reportscreen/" in h: return "report"
    return "other"


# ============================================================ pattern runners

def _verdict_for_screen(stype: str, analysis: dict, error_words: list) -> tuple[bool, list]:
    """Pattern-based pass/fail. Returns (passed, notes)."""
    notes = []
    if error_words:
        notes.append(f"error words on page: {error_words}")
        return False, notes
    if not (analysis.get("buttons", 0) > 0 or analysis.get("inputs", 0) > 0
            or analysis.get("hasGrid") or analysis.get("hasTitle")):
        notes.append("page appears blank — no visible buttons/inputs/grid/title")
        return False, notes

    if stype == "list":
        # Pass if it has either a top-nav or a grid (some lists have one
        # without the other). Don't require #New — many lists are read-only.
        if not (analysis.get("hasTopNav") or analysis.get("hasGrid")
                or analysis.get("hasTitle")):
            notes.append("list-like screen has no top-nav, no grid, no title")
            return False, notes
        return True, notes

    if stype == "detail":
        # Detail screens should have a form or some inputs to fill.
        if not (analysis.get("hasForm") or analysis.get("inputs", 0) > 0):
            notes.append("detail-like screen has no form / no input fields")
            return False, notes
        return True, notes

    if stype == "report":
        # Reports usually have filters (inputs) and maybe a Run button.
        if analysis.get("inputs", 0) == 0 and analysis.get("buttons", 0) == 0:
            notes.append("report screen has no filters and no buttons")
            return False, notes
        return True, notes

    if stype == "wizard":
        if analysis.get("buttons", 0) == 0:
            notes.append("wizard screen has no buttons")
            return False, notes
        return True, notes

    # 'other' / unknown — just check it rendered SOMETHING
    if analysis.get("buttons", 0) == 0 and analysis.get("inputs", 0) == 0:
        notes.append("page has no visible buttons or inputs")
        return False, notes
    return True, notes


# ============================================================ login + discovery

def _login(page, cfg: DemoConfig, t: _Tracker) -> str:
    from config import settings as _s
    login_url = build_url(cfg.base_url, _s.app.login_path)
    page.goto(login_url)
    page.wait_for_selector("#userid", state="visible")
    machine_id = cfg.machine_id or _extract_mid(cfg.base_url) or "100"
    page.evaluate(f"""() => {{
        try {{ localStorage.setItem('MACHINE_ID', {json.dumps(machine_id)}); }} catch(e) {{}}
        const f = document.getElementById('loginform');
        if (f && !f.querySelector('input[name="machineid"]')) {{
            const i = document.createElement('input');
            i.type='hidden'; i.name='machineid'; i.value={json.dumps(machine_id)};
            f.appendChild(i);
        }}
    }}""")
    # A fresh browser (no stored MACHINE_ID) opens a "Machine Screen" modal on
    # load, whose overlay intercepts clicks on the login button. Handle it:
    # fill the dialog's Machine ID and dismiss it before signing in. Real
    # browsers skip this because localStorage already holds the machine.
    try:
        if page.locator("#machineID").is_visible(timeout=4_000):
            page.fill("#machineID", machine_id)
            page.click("#btnOK")
            page.wait_for_selector("#machineID", state="hidden", timeout=6_000)
    except Exception:
        pass

    page.fill("#userid", cfg.user)
    page.fill("#passwd", cfg.password)
    # Wait for any lingering modal overlay to clear so the click is not intercepted.
    try: page.wait_for_selector(".ui-widget-overlay", state="hidden", timeout=4_000)
    except Exception: pass
    page.click("#btnLogin")
    try: page.wait_for_load_state("networkidle", timeout=8_000)
    except Exception: pass
    time.sleep(2)
    return page.url


def _discover_screens(page, cfg: DemoConfig, t: _Tracker) -> list[ScreenSpec]:
    home = build_url(cfg.base_url, "/wrmsscreen")
    page.goto(home, wait_until="commit", timeout=15_000)
    try:
        page.wait_for_selector("#page-sidebar, #sidebar-menu, body",
                               state="visible", timeout=15_000)
    except Exception:
        pass
    time.sleep(2)
    raw = page.evaluate("""() => {
        const links = Array.from(document.querySelectorAll(
            'a[screenname], a[screenName], a[data-href]'
        ));
        const seen = new Set();
        const out = [];
        for (const a of links) {
            const sn = (a.getAttribute('screenname')
                     || a.getAttribute('screenName') || '').toLowerCase();
            const href = a.getAttribute('data-href') || '';
            const label = (a.innerText || a.textContent || '').trim();
            if (!sn || !href) continue;
            if (seen.has(sn)) continue;
            seen.add(sn);
            out.push({screenname: sn, label, dataHref: href});
        }
        return out;
    }""")
    screens = [
        ScreenSpec(
            screenname=r["screenname"], label=r["label"],
            data_href=r["dataHref"], type=_classify(r["dataHref"]),
        )
        for r in raw
    ]
    return screens


# ============================================================ per-screen test

def _test_screen(page, spec: ScreenSpec, t: _Tracker, cfg: DemoConfig) -> ScreenResult:
    full = build_url(cfg.base_url, spec.data_href)
    start = time.time()
    result = ScreenResult(spec=spec, passed=False)

    try:
        page.goto(full, wait_until="domcontentloaded", timeout=20_000)
    except Exception as e:
        # Fall back to 'commit' which always returns immediately
        try:
            page.goto(full, wait_until="commit", timeout=10_000)
        except Exception as e2:
            result.error = f"goto failed: {e2}"
            result.duration_s = time.time() - start
            return result

    # SPA render settle. Try short wait first; if page looks blank, wait longer.
    time.sleep(3.5)
    quick = page.evaluate("""() => ({
        n: document.querySelectorAll('button:not([disabled]), input:not([type=hidden])').length,
        t: !!document.querySelector('#topNav, .screen-title-header, .ui-jqgrid')
    })""")
    if quick.get("n", 0) == 0 and not quick.get("t"):
        # Page still empty — give it more time
        time.sleep(4)

    try:
        analysis = _analyze_screen(page)
    except Exception as e:
        result.error = f"analysis failed: {e}"
        result.duration_s = time.time() - start
        return result

    result.rendered = bool(
        analysis.get("hasTopNav") or analysis.get("hasGrid")
        or analysis.get("hasForm") or analysis.get("hasTitle")
        or analysis.get("buttons", 0) > 0
    )
    result.counts = {
        "buttons": analysis.get("buttons", 0),
        "inputs":  analysis.get("inputs", 0),
        "links":   analysis.get("links", 0),
    }
    body = (analysis.get("bodyTextStart") or "").lower()
    result.error_words = [w for w in ERROR_WORDS if w in body]

    passed, notes = _verdict_for_screen(spec.type, analysis, result.error_words)
    result.passed = passed
    result.notes = notes

    # Take a small screenshot for the gallery
    shot_dir = cfg.reports_dir / "screenshots"
    shot_dir.mkdir(parents=True, exist_ok=True)
    out = shot_dir / f"crawl_{spec.screenname}_{int(time.time())}.png"
    try:
        page.screenshot(path=str(out), full_page=False)
        result.screenshot = str(out)
    except Exception:
        pass

    result.duration_s = time.time() - start
    return result


# ============================================================ runner

@contextmanager
def _browser(headless: bool, network_buffer: list | None = None,
             console_buffer: list | None = None):
    """Launch Chromium. If `network_buffer` is provided, append a record per
    request/response so the HTML report can show API path/payload/response.
    If `console_buffer` is provided, append every browser console.* call and
    every uncaught page error so the report's Console tab is useful."""
    import time as _time
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=headless, slow_mo=0,
            args=["--ignore-certificate-errors"],
        )
        ctx = browser.new_context(
            ignore_https_errors=True,
            viewport={"width": 1500, "height": 900},
        )
        page = ctx.new_page()
        page.set_default_timeout(45_000)

        # ---- Console capture ----------------------------------------------
        if console_buffer is not None:
            def _on_console(msg):
                try:
                    loc = ""
                    try:
                        l = msg.location or {}
                        if l.get("url"):
                            loc = f"{l['url']}:{l.get('lineNumber','?')}"
                    except Exception:
                        pass
                    console_buffer.append({
                        "ts":   _time.time(),
                        "type": msg.type,            # log / warning / error / info / debug
                        "text": (msg.text or "")[:1500],
                        "loc":  loc,
                    })
                except Exception:
                    pass
            def _on_pageerror(err):
                try:
                    console_buffer.append({
                        "ts":   _time.time(),
                        "type": "pageerror",
                        "text": str(err)[:1500],
                        "loc":  "",
                    })
                except Exception:
                    pass
            ctx.on("console", _on_console)
            ctx.on("pageerror", _on_pageerror)
        # -------------------------------------------------------------------

        # ---- Network capture ----------------------------------------------
        # We attach lightweight listeners to the BrowserContext so navigation
        # and XHR/fetch traffic from every page is captured.
        pending: dict = {}    # request -> index in network_buffer

        if network_buffer is not None:
            def _on_request(req):
                try:
                    kind = req.resource_type
                    # Skip noisy static asset fetches
                    if kind in ("image", "font", "stylesheet", "media"):
                        return
                    body = ""
                    try:
                        body = req.post_data or ""
                    except Exception:
                        pass
                    if body and len(body) > 2000:
                        body = body[:2000] + "…(truncated)"
                    rec = {
                        "ts": _time.time(),
                        "method": req.method,
                        "url": req.url,
                        "kind": kind,
                        "req_body": body,
                        "status": None,
                        "resp_size": None,
                        "resp_preview": "",
                    }
                    network_buffer.append(rec)
                    pending[req] = rec
                except Exception:
                    pass

            def _on_response(resp):
                try:
                    rec = pending.pop(resp.request, None)
                    if rec is None:
                        return
                    rec["status"] = resp.status
                    body = ""
                    try:
                        # Only read text for likely-textual responses, to avoid
                        # huge binary reads.
                        ct = (resp.headers or {}).get("content-type", "")
                        if any(k in ct for k in ("json", "text", "xml", "html", "javascript")):
                            body = resp.text() or ""
                    except Exception:
                        pass
                    rec["resp_size"] = len(body) if body else None
                    if body and len(body) > 2000:
                        body = body[:2000] + "…(truncated)"
                    rec["resp_preview"] = body
                except Exception:
                    pass

            ctx.on("request", _on_request)
            ctx.on("response", _on_response)
        # -------------------------------------------------------------------

        try:
            yield page
        finally:
            ctx.close(); browser.close()


def _types_filter(type_filter: set[str], spec: ScreenSpec) -> bool:
    if not type_filter or "all" in type_filter:
        return True
    return spec.type in type_filter


# ============================================================ custom test cases

@dataclass
class CustomTestCase:
    screen: str           # screenname this applies to
    name: str             # human label
    steps: list           # list of {action, target?, value?, ...} dicts


def parse_custom_tests(yaml_text: str) -> list[CustomTestCase]:
    """Parse a YAML test case file. Returns list of CustomTestCase."""
    if not yaml_text or not yaml_text.strip():
        return []
    import yaml
    try:
        doc = yaml.safe_load(yaml_text)
    except Exception as e:
        raise ValueError(f"YAML parse error: {e}")
    if not isinstance(doc, dict) or "tests" not in doc:
        raise ValueError("YAML must have a top-level 'tests' list")
    out = []
    for i, item in enumerate(doc["tests"]):
        if not isinstance(item, dict):
            continue
        out.append(CustomTestCase(
            screen=str(item.get("screen", "")).lower(),
            name=str(item.get("name", f"test#{i+1}")),
            steps=item.get("steps") or [],
        ))
    return out


def _execute_custom_steps(page, steps: list, t: _Tracker, screen_label: str) -> tuple[bool, list]:
    """Run a series of custom test steps. Returns (passed, notes).

    Actions are LENIENT where it makes sense:
      - open_search: skips silently if no toggle present (search may already be visible)
      - fill: skips silently if field not present (best effort)
      - click: tries many selector variants
    """
    notes: list[str] = []
    for step_i, step in enumerate(steps, 1):
        action = (step.get("action") or "").lower()
        target = step.get("target")
        value  = step.get("value")
        optional = bool(step.get("optional", False))
        try:
            if action == "open_search":
                # LENIENT: many Stratus screens have search visible by default.
                # Try common toggle selectors; if none present, skip — that's OK.
                # 600ms was too short for this SPA: the toggle is rendered after
                # the screen's JS module loads, so the click was missed and every
                # later step then failed on a still-hidden search panel.
                clicked = _try_click_any(page, [
                    "#ShowCriteria", "[name='ShowCriteria']",
                    "button:has-text('Show Search Criteria')",
                    "a:has-text('Show Search Criteria')",
                ], timeout_ms=5_000)
                if clicked:
                    # Let the panel finish expanding before anything inside it is used.
                    try:
                        page.wait_for_timeout(400)
                    except Exception:
                        pass
                else:
                    notes.append(f"step {step_i}: no ShowCriteria toggle (search may be visible by default — continuing)")

            elif action == "fill":
                # LENIENT: try id, then name, then label-for. If field truly
                # absent, skip (not all screens have all fields).
                loc = _find_field(page, str(target))
                if loc is None:
                    if optional:
                        continue
                    notes.append(f"step {step_i}: field {target!r} not found (skipping)")
                    continue
                try:
                    loc.wait_for(state="visible", timeout=5_000)
                    loc.fill(str(value))
                except Exception as e:
                    notes.append(f"step {step_i}: fill {target!r} skipped — not interactable: {str(e)[:80]}")
                    continue

            elif action == "click":
                # Try the FULL set of selector variants
                clicked = _try_click_any(page, [
                    f"#{target}",
                    f"[name='{target}']",
                    f"#topNav button:has-text('{target}')",
                    f"#topNav a:has-text('{target}')",
                    f"#topNav #{target}",
                    f"button[id='{target}']",
                    f"button:has-text('{target}')",
                    f"a:has-text('{target}')",
                ], timeout_ms=3_000)
                if not clicked:
                    if optional:
                        continue
                    notes.append(f"step {step_i}: click target {target!r} not found")
                    return False, notes

            elif action == "select":
                loc = _find_field(page, str(target))
                if loc is None:
                    notes.append(f"step {step_i}: select target {target!r} not found (skipping)")
                    continue
                try:
                    loc.select_option(label=str(value))
                except Exception:
                    # Fall back to value match
                    try: loc.select_option(value=str(value))
                    except Exception as e:
                        notes.append(f"step {step_i}: select {target!r}={value!r} failed: {str(e)[:80]}")

            elif action == "wait":
                time.sleep(float(target or value or 1))

            elif action == "assert_visible":
                # Testers write a label ("Buying Club - Goal for Reward") or a
                # field id ("#CLUB_MAX_PURCH"). Try it as a selector first; if
                # that is not valid CSS/is not found, fall back to matching any
                # visible element whose text contains the target. This is what a
                # human means by "the X field should be visible".
                tgt = str(target).strip()
                try:
                    page.locator(tgt).first.wait_for(state="visible", timeout=4_000)
                except Exception:
                    found = page.locator(
                        f"text=/{re.escape(tgt)}/i"
                    ).first
                    try:
                        found.wait_for(state="visible", timeout=6_000)
                    except Exception:
                        # last resort: substring scan of the visible body text
                        body = page.locator("body").inner_text(timeout=3000)
                        if tgt.lower() not in body.lower():
                            notes.append(f"step {step_i}: {tgt!r} not visible on screen")
                            return False, notes

            elif action == "assert_text":
                body = page.locator("body").inner_text(timeout=3000)
                if str(target).lower() not in body.lower():
                    notes.append(f"step {step_i}: text {target!r} not found in body")
                    return False, notes

            elif action == "assert_no_errors":
                body = page.locator("body").inner_text(timeout=3000).lower()
                found = [w for w in ERROR_WORDS if w in body]
                if found:
                    notes.append(f"step {step_i}: error words found: {found}")
                    return False, notes

            elif action in ("assert_rows_min", "assert_rows_max"):
                # The NL converter sometimes emits a bare row assertion with no
                # count ("check results are returned"). int(None) raised a
                # TypeError that surfaced as a step error, which reads like the
                # screen misbehaved when really the test never said what to
                # compare against. Treat a missing bound as "at least one row"
                # for _min and as no upper limit for _max.
                bound = _as_int(target)
                n = page.locator("tr.jqgrow").count()
                if action == "assert_rows_min":
                    if bound is None:
                        bound = 1
                        notes.append(f"step {step_i}: no row count given — "
                                     f"assuming at least 1")
                    if n < bound:
                        notes.append(f"step {step_i}: only {n} rows < min {bound}")
                        return False, notes
                else:
                    if bound is None:
                        notes.append(f"step {step_i}: no row count given — "
                                     f"upper bound not checked")
                    elif n > bound:
                        notes.append(f"step {step_i}: {n} rows > max {bound}")
                        return False, notes

            elif action == "screenshot":
                fname = f"{screen_label}_{target or step_i}"
                t.screenshot(page, fname, quiet=True)

            elif action in ("todo", "note", "manual"):
                # Soft-skip marker produced by the Excel importer for prose
                # steps that couldn't be auto-translated. Logged but never
                # fails the test — the human is expected to fill it in.
                text = target or step.get("value") or step.get("text") or ""
                notes.append(f"step {step_i}: TODO — {text[:120]}")

            else:
                notes.append(f"step {step_i}: unknown action {action!r}")
                return False, notes

            time.sleep(0.3)

        except Exception as e:
            notes.append(f"step {step_i} ({action}) error: {str(e)[:140]}")
            return False, notes
    return True, notes


def _as_int(value):
    """Row-count bound from a step, or None when the step didn't supply one.

    Steps reach here straight from YAML, so the value may be absent, an empty
    string, or already an int.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _find_field(page, target: str):
    """Find a form field by id, name, or label. Returns first locator or None."""
    selectors = [
        f"#{target}",
        f"[name='{target}']",
        f"input[id='{target}']",
        f"select[id='{target}']",
        f"textarea[id='{target}']",
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                return loc
        except Exception:
            continue
    return None


def _try_click_any(page, selectors: list, timeout_ms: int = 2000) -> bool:
    """Try each selector; click the first visible one. Returns True/False."""
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() == 0:
                continue
            try:
                loc.wait_for(state="visible", timeout=timeout_ms)
                loc.click(timeout=timeout_ms)
                return True
            except Exception:
                continue
        except Exception:
            continue
    return False


def _click_first_match(page, selectors: list) -> None:
    """Try each selector in order; click the first that's visible. Strict."""
    if not _try_click_any(page, selectors, timeout_ms=2000):
        raise AssertionError(f"none of these selectors matched: {selectors}")


def run_crawl(
    cfg: DemoConfig,
    on_event: Callable[[StepEvent], None] | None = None,
    scope: str = "",          # blank = all; otherwise substring match on screenname/label
    type_filter: set[str] | None = None,
    max_screens: int | None = None,
    custom_tests_yaml: str = "",   # optional YAML test cases
    selected_screens: list | None = None,   # explicit screennames; if set, overrides scope
) -> DemoResult:
    """Crawl all screens. Returns a DemoResult."""
    t = _Tracker(on_event, cfg)
    t.banner(f"Stratus QA — Crawl ALL screens against {cfg.base_url}")

    type_filter = type_filter or set()
    crawl_results: list[ScreenResult] = []
    network_log: list[dict] = []
    console_log: list[dict] = []

    # Parse custom test cases if provided
    custom_tests: dict[str, list[CustomTestCase]] = {}
    if custom_tests_yaml:
        try:
            tcs = parse_custom_tests(custom_tests_yaml)
            for tc in tcs:
                custom_tests.setdefault(tc.screen, []).append(tc)
            t.banner(f"Loaded {sum(len(v) for v in custom_tests.values())} custom "
                     f"test case(s) for {len(custom_tests)} screen(s)")
        except Exception as e:
            t.fail("custom_tests_parse", str(e))
            return _build_crawl_result(t, crawl_results)

    try:
        with _browser(headless=cfg.headless, network_buffer=network_log,
                      console_buffer=console_log) as page:
            # 1) Login
            t.section("Authenticating")
            try:
                landed = _login(page, cfg, t)
                t.info(f"post-login URL: {landed}")
                t.ok("logged in")
                t.screenshot(page, "after_login")
            except Exception as e:
                t.fail("login", str(e), page=page)
                return _build_crawl_result(t, crawl_results)

            # 2) Discover
            t.section("Discovering screens from menu")
            try:
                screens = _discover_screens(page, cfg, t)
            except Exception as e:
                t.fail("discover", str(e), page=page)
                return _build_crawl_result(t, crawl_results)

            # If explicit selection given, that wins
            if selected_screens:
                sel = {s.lower() for s in selected_screens}
                screens = [s for s in screens if s.screenname.lower() in sel]
                t.info(f"using {len(screens)} explicitly-selected screen(s)")
            else:
                if scope:
                    scope_l = scope.lower()
                    screens = [s for s in screens
                               if scope_l in s.screenname.lower() or scope_l in s.label.lower()]
                screens = [s for s in screens if _types_filter(type_filter, s)]
                if max_screens:
                    screens = screens[:max_screens]

            # Summary by type
            type_counts: dict[str, int] = {}
            for s in screens: type_counts[s.type] = type_counts.get(s.type, 0) + 1
            t.info("counts by type: " + ", ".join(f"{k}={v}" for k, v in sorted(type_counts.items())))
            t.ok(f"will test {len(screens)} screen(s)")

            # 3) Per-screen test (generic checks + any custom test cases)
            for i, spec in enumerate(screens, 1):
                t.section(f"[{i}/{len(screens)}] {spec.screenname} ({spec.type})")
                try:
                    r = _test_screen(page, spec, t, cfg)
                    crawl_results.append(r)
                    msg = (f"{spec.label or spec.screenname}: "
                           f"{r.counts.get('buttons',0)} btns, "
                           f"{r.counts.get('inputs',0)} inputs"
                           + (f" — {', '.join(r.notes)}" if r.notes else ""))
                    if r.passed:
                        t.ok(msg)
                    else:
                        t.fail(spec.screenname, r.error or msg, page=None)
                    if r.screenshot:
                        _emit(t.cb, StepEvent(
                            type="screenshot", text=spec.screenname,
                            step=t.step, screenshot_path=r.screenshot,
                        ))

                    # Run any custom test cases for this screen
                    for tc in custom_tests.get(spec.screenname, []):
                        t.section(f"    custom: {tc.name}")
                        ok, notes = _execute_custom_steps(page, tc.steps, t, spec.screenname)
                        if ok:
                            t.ok(f"custom test '{tc.name}' passed ({len(tc.steps)} steps)")
                        else:
                            t.fail(
                                f"{spec.screenname}::{tc.name}",
                                "; ".join(notes) or "custom test failed",
                                page=page,
                            )
                except Exception as e:
                    t.fail(spec.screenname, str(e), page=page)

    except Exception as e:
        t.fail("runtime", f"unexpected: {e}")

    # Attach network + console logs to the tracker so the report can use them
    t.network_log = network_log
    t.console_log = console_log
    return _build_crawl_result(t, crawl_results)


def _build_crawl_result(t: _Tracker, results: list[ScreenResult]) -> DemoResult:
    total = t.passed + t.failed
    passed = total > 0 and t.failed == 0
    res = DemoResult(
        passed=passed,
        steps_total=total,
        steps_passed=t.passed,
        steps_failed=t.failed,
        duration_s=t.duration(),
        failures=list(t.failures),
        screenshots=[r.screenshot for r in results if r.screenshot],
        final_screenshot=None,
    )
    verdict = ("PASS" if passed else
               (f"PARTIAL — {t.passed}/{total}" if t.passed else "FAIL"))
    _emit(t.cb, StepEvent(type="done", text=f"{verdict} ({len(results)} screens)"))
    return res
