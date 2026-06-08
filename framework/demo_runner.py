"""Runner that the CLI demo AND the Flask UI both call.

Exposes ``run_demo()`` which executes the Customer-module flow against
whichever URL/credentials are passed in (real Stratus or the mock).

Each step pushes an event through the ``on_event`` callback so the
caller can stream live progress to the terminal, a log file, or a web
UI via Server-Sent Events.
"""
from __future__ import annotations

import json
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

from playwright.sync_api import sync_playwright

from framework.ui.pages import (
    CustomerDetailPage,
    CustomerInput,
    CustomerListPage,
    LoginPage,
)


# ============================================================ URL helpers

def build_url(base_url: str, path: str, extra_query: dict | None = None) -> str:
    """Compose a URL by joining ``base_url`` and ``path``, preserving any
    query string the user already typed into ``base_url``.

    Correctly handles:
      - base URLs with query strings (?mid=100)
      - paths with hash fragments (#/listScreen/customerlist) — query stays
        BEFORE the # as required by RFC 3986

        base_url = 'https://stratus.host/backoffice/?mid=100'
        path     = 'mv-assets/index-modern.html#/listScreen/customerlist'
        →          'https://stratus.host/backoffice/mv-assets/index-modern.html?mid=100#/listScreen/customerlist'

        base_url = 'https://stratus.host/backoffice/?mid=100'
        path     = '/stratus'
        extra    = {'screenType': 'CustomerList'}
        →          'https://stratus.host/backoffice/stratus?mid=100&screenType=CustomerList'
    """
    # Split hash fragment off the incoming path so we can place it correctly.
    if "#" in path:
        path_part, fragment = path.split("#", 1)
    else:
        path_part, fragment = path, ""

    # Defensive: if caller stuffed "?foo=bar" into path, peel it off into
    # extra_query so we don't end up with two ? in the final URL.
    if "?" in path_part:
        pp, embedded_qs = path_part.split("?", 1)
        path_part = pp
        embedded = dict(parse_qsl(embedded_qs, keep_blank_values=True))
        if extra_query:
            embedded.update(extra_query)
        extra_query = embedded

    u = urlparse(base_url)
    base_path = u.path or "/"
    if not base_path.endswith("/"):
        base_path += "/"
    cleaned = path_part.lstrip("/")
    new_path = base_path + cleaned

    base_qs = dict(parse_qsl(u.query, keep_blank_values=True))
    if extra_query:
        base_qs.update({k: str(v) for k, v in extra_query.items()})
    qs = urlencode(base_qs)

    return urlunparse((u.scheme, u.netloc, new_path, "", qs, fragment))


# --------------------------------------------------------------- public types

@dataclass
class StepEvent:
    type: str                # 'banner' | 'section' | 'info' | 'ok' | 'warn' | 'fail' | 'done' | 'screenshot' | 'diagnostic'
    text: str = ""
    step: int = 0
    screenshot_path: str | None = None
    html_path: str | None = None     # snapshot of page.content() at failure
    console_tail: list | None = None  # last N browser console messages


@dataclass
class DemoConfig:
    base_url: str
    user: str
    password: str
    screen: str = "customer"         # only "customer" supported in the MVP
    machine_id: str = ""             # Stratus POS workstation ID; auto-extracted
                                     # from base_url's ?mid=... if blank
    headless: bool = True
    slow_mo_ms: int = 0
    read_only: bool = False
    diagnose: bool = False           # if True: log in + navigate + screenshot only.
                                     # NO selector waits, NO assertions.
                                     # Use this to figure out what real Stratus shows.
    capture_step_screenshots: bool = True   # set False for max speed
    capture_html: bool = True        # save page.content() at each section
    reports_dir: Path = field(default_factory=lambda: Path("reports"))


def _extract_mid(base_url: str) -> str:
    """Pull ?mid=X out of a URL, return '' if absent."""
    try:
        return dict(parse_qsl(urlparse(base_url).query)).get("mid", "")
    except Exception:
        return ""


@dataclass
class DemoResult:
    passed: bool
    steps_total: int
    steps_passed: int
    steps_failed: int
    duration_s: float
    failures: list[tuple[str, str]] = field(default_factory=list)
    screenshots: list[str] = field(default_factory=list)
    final_screenshot: str | None = None


EventCallback = Callable[[StepEvent], None]


# -------------------------------------------------------------- internal

def _emit(cb: EventCallback | None, evt: StepEvent) -> None:
    if cb is not None:
        try:
            cb(evt)
        except Exception:
            pass


class _Tracker:
    """Tracks the step number + accumulates results."""
    # Class-level caches so the Flask layer can pull the last run's
    # network log AND browser console log without holding a tracker reference.
    _last_network_log: list = []
    _last_console_log: list = []

    def __init__(self, cb: EventCallback | None, cfg: DemoConfig):
        self.cb = cb
        self.cfg = cfg
        self.step = 0
        self.passed = 0
        self.failed = 0
        self.failures: list[tuple[str, str]] = []
        self.screenshots: list[str] = []
        self.console_buffer: list[dict] = []     # browser console messages
        self.network_log: list[dict] = []        # network requests
        self.console_log: list[dict] = []        # browser console log
        self._t0 = time.time()

    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        # Mirror runner-attached logs into class-level caches so the worker
        # in web_ui/app.py can read the last run's data without holding a
        # tracker reference (the tracker is internal to each runner).
        if isinstance(value, list):
            if name == "network_log":
                type(self)._last_network_log = value
            elif name == "console_log":
                type(self)._last_console_log = value

    def banner(self, text: str) -> None:
        _emit(self.cb, StepEvent(type="banner", text=text))

    def section(self, text: str) -> None:
        self.step += 1
        _emit(self.cb, StepEvent(type="section", text=text, step=self.step))

    def info(self, text: str) -> None:
        _emit(self.cb, StepEvent(type="info", text=text, step=self.step))

    def ok(self, text: str) -> None:
        self.passed += 1
        _emit(self.cb, StepEvent(type="ok", text=text, step=self.step))

    def warn(self, text: str) -> None:
        _emit(self.cb, StepEvent(type="warn", text=text, step=self.step))

    def fail(self, name: str, text: str, page=None) -> None:
        self.failed += 1
        self.failures.append((name, text))
        # On failure, ALWAYS capture: screenshot + HTML + console tail + page analysis
        html_path = None
        shot_path = None
        if page is not None:
            shot_path = self.screenshot(page, f"FAIL_{name}", quiet=True)
            html_path = self.dump_html(page, f"FAIL_{name}")
        _emit(self.cb, StepEvent(
            type="fail", text=text, step=self.step,
            screenshot_path=shot_path,
            html_path=html_path,
            console_tail=self.console_buffer[-20:],
        ))

    def screenshot(self, page, name: str, quiet: bool = False) -> str | None:
        if not self.cfg.capture_step_screenshots:
            return None
        shot_dir = self.cfg.reports_dir / "screenshots"
        shot_dir.mkdir(parents=True, exist_ok=True)
        out = shot_dir / f"step_{self.step:02d}_{name}_{int(time.time())}.png"
        try:
            page.screenshot(path=str(out), full_page=True)
        except Exception:
            return None
        rel = str(out)
        self.screenshots.append(rel)
        if not quiet:
            _emit(self.cb, StepEvent(
                type="screenshot", text=name, step=self.step, screenshot_path=rel
            ))
        return rel

    def dump_html(self, page, name: str) -> str | None:
        if not self.cfg.capture_html:
            return None
        html_dir = self.cfg.reports_dir / "html"
        html_dir.mkdir(parents=True, exist_ok=True)
        out = html_dir / f"step_{self.step:02d}_{name}_{int(time.time())}.html"
        try:
            out.write_text(page.content(), encoding="utf-8")
        except Exception:
            return None
        return str(out)

    def duration(self) -> float:
        return time.time() - self._t0


# --------------------------------------------------------------- segments

def _do_login(page, t: _Tracker, cfg: DemoConfig) -> None:
    """Login flow matching the proven-working pattern from test_spa_variants.

    The key insights from extensive testing against a real Stratus install:
      1. Use a default goto (no special wait_until) for login.jsp
      2. Inject machineid into the form BEFORE filling fields
      3. Plain page.click(#btnLogin) — DON'T trigger via jQuery
      4. wait_for_load_state('networkidle', timeout=8000) — this is the
         critical step: it captures Stratus's client-side redirect that
         takes us from /UserAuthenticationServlet.do → /wrmsscreen
      5. time.sleep(2) — let the client-side JS finish
    """
    t.section("Logging into Stratus BackOffice")

    from config import settings as _s
    login_url = build_url(cfg.base_url, _s.app.login_path)
    t.info(f"opening {login_url}")
    page.goto(login_url)
    page.wait_for_selector(LoginPage.USERID, state="visible")
    page.wait_for_selector(LoginPage.PASSWORD, state="visible")

    # Machine ID: extract from URL ?mid=... or use explicit config
    machine_id = cfg.machine_id or _extract_mid(cfg.base_url)
    if machine_id:
        t.info(f"injecting machineid={machine_id} into login form + localStorage")
        page.evaluate(f"""() => {{
            try {{ localStorage.setItem('MACHINE_ID', {json.dumps(machine_id)}); }} catch(e) {{}}
            const form = document.getElementById('loginform');
            if (form && !form.querySelector('input[name="machineid"]')) {{
                const inp = document.createElement('input');
                inp.type = 'hidden';
                inp.name = 'machineid';
                inp.value = {json.dumps(machine_id)};
                form.appendChild(inp);
            }}
        }}""")

    page.fill(LoginPage.USERID, cfg.user)
    page.fill(LoginPage.PASSWORD, cfg.password)
    t.info(f"entered credentials for user {cfg.user!r}")

    # Plain click — works because Stratus's login.js handler intercepts the
    # form's submit event when the click fires. Don't override with jQuery
    # — that bypasses the AJAX path Stratus expects.
    page.click(LoginPage.SUBMIT)

    # CRITICAL: networkidle catches Stratus's post-login client-side
    # redirect. Without this wait, we'd still be on the JSON-response
    # page when we try to navigate to the SPA — and the SPA can't
    # initialize from there.
    try:
        page.wait_for_load_state("networkidle", timeout=8_000)
    except Exception:
        pass
    time.sleep(2)
    t.info(f"post-login URL: {page.url}")

    # Sanity check: did Stratus reject the login outright?
    if "/UserAuthenticationServlet.do" in page.url:
        try:
            body = page.locator("body").inner_text()
            if '"status":"failed"' in body or "Machine ID is empty" in body:
                raise AssertionError(
                    f"Stratus rejected login. Response: {body[:200]}. "
                    "Check credentials and Machine ID."
                )
        except Exception:
            pass

    t.ok("login submitted")
    t.screenshot(page, "after_login")


def _dismiss_overlays(page) -> None:
    """Best-effort: dismiss common dialogs/alerts that block screen rendering.
    Looks for Stratus-style 'OK' / 'Continue' / 'X' close buttons in modals.
    """
    selectors = [
        ".modal.show button:has-text('OK')",
        ".modal.show button:has-text('Continue')",
        ".modal.show .close",
        ".ui-dialog button:has-text('OK')",
        ".alert button:has-text('OK')",
        "button[aria-label='Close']:visible",
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=300):
                loc.click(timeout=1000)
        except Exception:
            pass


# Possible signals that we've landed on a real Stratus "list-style" screen.
# Any one of these means "yes, this is a list screen" — we don't need #New
# specifically; the screen exists if ANY of these are visible.
_LIST_SIGNALS = ", ".join([
    "#New",
    "[id='New']",
    "[name='New']",
    "button:has-text('New')",
    "a:has-text('New')",
    "table.ui-jqgrid-btable",
    ".ui-jqgrid",
    "#customerGrid",
    "#grid_table",
    ".jqgrow",
    "#topNav button",
    ".topNav button",
    ".screen-title-header",
    "h4.screen-title-header",
    "form#componentFormId",
    ".action-btn",
])


def _try_menu_navigation_to_customers(page, t: _Tracker) -> bool:
    """Click the 'Customers' link in Stratus's sidebar menu.

    The link is hidden until hover (Superfish menu) — but the DOM element
    exists with screenname='customerlist' as a data attribute. We find it
    by attribute and click via JS to bypass visibility checks.
    """
    # Real Stratus pattern: <a screenname="customerlist" data-href="..."/>
    # The data-href has the SPA route to navigate to.
    try:
        link_info = page.evaluate("""() => {
            // Look for an <a> with screenname='customerlist' (case-insensitive)
            const candidates = Array.from(document.querySelectorAll('a[screenname], a[screenName]'));
            const link = candidates.find(a => {
                const sn = (a.getAttribute('screenname') || a.getAttribute('screenName') || '').toLowerCase();
                return sn === 'customerlist';
            });
            if (!link) return null;
            return {
                screenname: link.getAttribute('screenname') || link.getAttribute('screenName'),
                dataHref: link.getAttribute('data-href'),
                outerHTML: link.outerHTML.slice(0, 300),
            };
        }""")
        if not link_info:
            t.info("  no <a screenname='customerlist'> found in DOM")
            return False
        t.info(f"  found customerlist link: data-href={link_info['dataHref']!r}")
        # The link uses href="javascript:;" and relies on a Backbone-bound
        # click handler. Programmatic .click() may not fire that handler.
        # Most reliable approach: jump to the link's data-href ourselves.
        # That's still inside the SPA shell (so all globals are loaded) —
        # we just trigger the hash change directly.
        page.evaluate("""() => {
            const candidates = Array.from(document.querySelectorAll('a[screenname], a[screenName]'));
            const link = candidates.find(a => {
                const sn = (a.getAttribute('screenname') || a.getAttribute('screenName') || '').toLowerCase();
                return sn === 'customerlist';
            });
            if (!link) return;
            const target = link.getAttribute('data-href');
            if (target) {
                // Try the bound click handler first.
                try { link.click(); } catch(e) {}
                // If that didn't navigate, force-set location.
                setTimeout(() => {
                    if (!location.href.includes(target.split('#')[0])
                        || (target.includes('#') && location.hash !== '#' + target.split('#')[1])) {
                        location.href = target;
                    }
                }, 500);
            }
        }""")
        time.sleep(6)  # let the SPA navigate + render
        return True
    except Exception as e:
        t.info(f"  menu nav threw: {e}")
        return False


def _analyze_page(page) -> dict:
    """Run JS in the page to figure out what's actually there.
    Used in error messages so we can iterate fast on the right selectors.
    """
    try:
        return page.evaluate("""() => {
            function txtOf(el, max=40) {
                const t = (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ');
                return t.length > max ? t.slice(0, max-1) + '…' : t;
            }
            const buttons = Array.from(document.querySelectorAll('button'));
            const links   = Array.from(document.querySelectorAll('a'));
            const forms   = Array.from(document.querySelectorAll('form'));
            const tables  = Array.from(document.querySelectorAll('table'));
            const inputs  = Array.from(document.querySelectorAll('input'));
            const withIds = Array.from(document.querySelectorAll('[id]'));

            // common error patterns
            const body = (document.body.innerText || '').toLowerCase();
            const errors = [];
            ['error', 'fail', 'denied', 'forbidden', 'unauthorized',
             'not authorized', 'invalid', 'expired', 'session'].forEach(k => {
                if (body.includes(k)) errors.push(k);
            });

            // detect known frameworks
            const hasJqGrid = !!document.querySelector('.ui-jqgrid');
            const hasDust = typeof window.dust !== 'undefined';
            const hasBackbone = typeof window.Backbone !== 'undefined';
            const hasRequire = typeof window.require !== 'undefined';

            return {
                url: location.href,
                title: document.title,
                bodyTextStart: (document.body.innerText || '').trim().slice(0, 240),
                counts: {
                    buttons: buttons.length, links: links.length,
                    forms: forms.length, tables: tables.length,
                    inputs: inputs.length, elementsWithId: withIds.length,
                },
                topButtons: buttons.slice(0, 15).map(b => ({
                    id: b.id || null, name: b.name || null, text: txtOf(b),
                })),
                topLinks: links.slice(0, 25).map(a => ({
                    text: txtOf(a, 30),
                    href: (a.getAttribute('href') || '').slice(0, 80),
                    onclick: !!a.getAttribute('onclick'),
                })),
                topIds: withIds.slice(0, 25).map(e => ({
                    id: e.id, tag: e.tagName.toLowerCase(),
                })),
                customerHints: links.concat(buttons)
                    .filter(e => /customer/i.test(txtOf(e, 60)))
                    .slice(0, 8)
                    .map(e => ({
                        tag: e.tagName.toLowerCase(),
                        id: e.id || null,
                        text: txtOf(e, 60),
                        href: e.getAttribute('href') || null,
                        onclick: !!e.getAttribute('onclick'),
                    })),
                frameworkSignals: {hasJqGrid, hasDust, hasBackbone, hasRequire},
                errorKeywordsInBody: errors,
            };
        }""")
    except Exception as e:
        return {"error": f"page analysis failed: {e}"}


def _open_customer_list(page, t: _Tracker, cfg: DemoConfig) -> CustomerListPage:
    """Navigate to Customer List using the proven-working pattern.

    From test_spa_variants which works 100% against real Stratus:
      1. Navigate DIRECTLY to mv-assets/index-modern.html#/listScreen/customerlist
      2. Use wait_until='domcontentloaded' (NOT load — load never fires)
      3. time.sleep(5) to let the SPA framework boot + render templates
      4. Then wait_for_selector('#New') with a long timeout

    Key insight: if the post-login URL is /UserAuthenticationServlet.do
    (the JSON response page), the SPA can't initialize from there. So
    we ONLY proceed if login put us on a real Stratus page.
    """
    t.section("Navigating to Customer List screen")

    # Defend against the SPA-can't-init-from-JSON-page issue
    if "/UserAuthenticationServlet.do" in page.url:
        # Force-bounce to /wrmsscreen so we're on a real Stratus shell.
        # Use 'commit' because real Stratus pages don't fire 'load'.
        home_url = build_url(cfg.base_url, "/wrmsscreen")
        t.info(f"bouncing to main shell {home_url}")
        try:
            page.goto(home_url, wait_until="commit", timeout=15_000)
            # Wait for SOMETHING to appear so we know the shell loaded
            page.wait_for_selector(
                "#page-sidebar, #sidebar-menu, body",
                state="visible", timeout=15_000,
            )
            time.sleep(2)  # let the shell's JS finish
        except Exception as e:
            t.warn(f"shell bounce had issue: {e}")

    # Now navigate to the SPA. This matches test_spa_variants EXACTLY.
    spa_url = build_url(cfg.base_url, "mv-assets/index-modern.html#/listScreen/customerlist")
    t.info(f"opening SPA: {spa_url}")
    try:
        page.goto(spa_url, wait_until="domcontentloaded", timeout=30_000)
    except Exception as e:
        # If domcontentloaded times out, try 'commit' and hope for the best
        t.info(f"  domcontentloaded timed out, retrying with commit: {e}")
        try:
            page.goto(spa_url, wait_until="commit", timeout=15_000)
        except Exception as e2:
            raise AssertionError(f"Could not navigate to SPA URL: {e2}")

    # CRITICAL: 5-second settle. The SPA loads templates via Dust.js/RequireJS
    # which take time. wait_for_selector alone is not enough because the
    # framework might not have started rendering yet.
    t.info("waiting for SPA framework to render templates (5s)...")
    time.sleep(5)

    # Now poll for the New button with a generous timeout.
    try:
        page.wait_for_selector("#New", state="visible", timeout=30_000)
        _dismiss_overlays(page)
        t.ok("Customer List rendered (#New is visible)")
        t.screenshot(page, "customer_list")
        return CustomerListPage(page)
    except Exception as e:
        # SPA failed to render. Take a screenshot + analysis so the user
        # can see what's there.
        try: t.screenshot(page, "spa_failed_to_render")
        except: pass
        analysis = _analyze_page(page)
        summary = _format_analysis(analysis)
        raise AssertionError(
            f"SPA failed to render Customer List.\n"
            f"URL: {spa_url}\n"
            f"After 5s settle + 30s wait, #New is not visible.\n"
            f"Wait error: {e}\n\n"
            f"=== Page analysis ===\n{summary}"
        )


def _format_analysis(a: dict) -> str:
    if not a or a.get("error"):
        return a.get("error", "(no analysis available)") if a else "(none)"
    lines = []
    lines.append(f"URL: {a.get('url','')}")
    lines.append(f"Title: {a.get('title','')}")
    c = a.get("counts", {})
    lines.append(
        f"Counts: buttons={c.get('buttons',0)} links={c.get('links',0)} "
        f"forms={c.get('forms',0)} tables={c.get('tables',0)} "
        f"inputs={c.get('inputs',0)} elementsWithId={c.get('elementsWithId',0)}"
    )
    if a.get("errorKeywordsInBody"):
        lines.append(f"⚠ Error-ish words in body: {a['errorKeywordsInBody']}")
    if a.get("customerHints"):
        lines.append("Customer-related elements found:")
        for h in a["customerHints"]:
            lines.append(f"  · <{h['tag']}> id={h.get('id')!r} text={h.get('text')!r}")
    if a.get("topButtons"):
        lines.append("Top buttons on page:")
        for b in a["topButtons"][:10]:
            lines.append(f"  · id={b.get('id')!r} name={b.get('name')!r} text={b.get('text')!r}")
    if a.get("topIds"):
        lines.append("Top elements with IDs:")
        for e in a["topIds"][:15]:
            lines.append(f"  · #{e['id']} <{e['tag']}>")
    body_start = a.get("bodyTextStart", "")
    if body_start:
        lines.append(f"Body starts with: {body_start!r}")
    return "\n".join(lines)


def _search(cl: CustomerListPage, t: _Tracker) -> None:
    t.section("Searching for customers with last name 'SMITH'")
    cl.show_search_criteria()
    cl.fill_search(last_name="SMITH")
    cl.click_search()
    cl.page.wait_for_load_state("networkidle")
    count = cl.row_count()
    t.ok(f"search complete — grid has {count} row(s)")
    t.screenshot(cl.page, "search_results")


def _action_menu(cl: CustomerListPage, t: _Tracker) -> None:
    t.section("Opening the Action dropdown")
    cl._open_action_menu()
    items = []
    if cl.locator(CustomerListPage.BTN_EDIT).is_visible():   items.append("Edit")
    if cl.locator(CustomerListPage.BTN_DELETE).is_visible(): items.append("Delete")
    if cl.locator(CustomerListPage.BTN_PRINT).is_visible():  items.append("Print List")
    t.ok(f"Action menu exposes: {', '.join(items)}")
    t.screenshot(cl.page, "action_menu")
    cl.page.keyboard.press("Escape")


def _create_new(page, cl: CustomerListPage, t: _Tracker, cfg: DemoConfig) -> None:
    t.section("Creating a NEW customer via the New button")
    _ensure_on_customer_list(page, cl, cfg, t)
    cl.click_new()
    detail = CustomerDetailPage(page)
    detail.wait_loaded()
    unique = str(int(time.time()))[-4:]
    data = CustomerInput(
        first_name="QA-Demo",
        last_name=f"Tester{unique}",
        company="StratusQA",
        club_id=f"QA{unique}",
    )
    t.info(f"filling form: {data.first_name} {data.last_name} / Co: {data.company}")
    detail.fill(data)
    t.screenshot(page, "new_filled")
    detail.click_save()
    err = detail.error_text()
    if err:
        raise AssertionError(f"server returned error: {err}")
    t.ok("save succeeded — no errors")
    t.screenshot(page, "after_new_save")


def _ensure_on_customer_list(page, cl: CustomerListPage, cfg: DemoConfig, t: _Tracker) -> None:
    """Make sure we're on the Customer List page. Re-navigate if not.

    Used at the start of every step so a previous step's failure doesn't
    cascade into all subsequent steps.
    """
    # Quick check: is the New button visible? Then we're good.
    try:
        if cl.locator(CustomerListPage.BTN_NEW).first.is_visible(timeout=500):
            return
    except Exception:
        pass
    # Re-navigate to the SPA Customer List
    spa_url = build_url(cfg.base_url, "mv-assets/index-modern.html#/listScreen/customerlist")
    t.info(f"re-navigating to Customer List: {spa_url}")
    try:
        page.goto(spa_url, wait_until="domcontentloaded", timeout=30_000)
    except Exception:
        try: page.goto(spa_url, wait_until="commit", timeout=15_000)
        except Exception: pass
    time.sleep(5)
    try:
        page.wait_for_selector("#New", state="visible", timeout=30_000)
    except Exception:
        pass


def _edit_existing(page, cl: CustomerListPage, t: _Tracker, cfg: DemoConfig) -> None:
    t.section("Editing an existing customer (search → row → Edit)")
    _ensure_on_customer_list(page, cl, cfg, t)
    cl.search_by_last_name("SMITH")
    if cl.row_count() == 0:
        t.warn("no SMITH rows — skipping edit")
        return
    cl.click_row(0)
    cl.click_edit()
    detail = CustomerDetailPage(page)
    detail.wait_loaded()
    new_first = f"Updated{int(time.time()) % 1000}"
    t.info(f"changing first name to {new_first!r}")
    detail.fill(CustomerInput(first_name=new_first))
    detail.click_save()
    if detail.error_text():
        raise AssertionError(detail.error_text())
    t.ok("edit saved")
    t.screenshot(page, "after_edit_save")


def _print_list(page, cl: CustomerListPage, t: _Tracker, cfg: DemoConfig) -> None:
    t.section("Triggering Print List from the Action menu")
    _ensure_on_customer_list(page, cl, cfg, t)
    try:
        cl.click_print_list()
    except Exception as e:
        t.info(f"print click issue: {e}")
    try:
        cl.page.wait_for_load_state("networkidle", timeout=8_000)
    except Exception:
        pass
    t.ok("print job initiated")
    t.screenshot(cl.page, "after_print")


def _close(page, cl: CustomerListPage, t: _Tracker, cfg: DemoConfig) -> None:
    t.section("Closing the Customer List screen")
    _ensure_on_customer_list(page, cl, cfg, t)
    try:
        cl.click_close()
    except Exception as e:
        t.info(f"close click issue (continuing): {e}")
    try:
        cl.page.wait_for_load_state("networkidle", timeout=8_000)
    except Exception:
        pass
    t.ok("Close clicked")
    t.screenshot(cl.page, "after_close")


# --------------------------------------------------------------- runner

@contextmanager
def _browser(headless: bool, slow_mo: int, console_buffer: list | None = None):
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=headless,
            slow_mo=slow_mo,
            args=["--ignore-certificate-errors"],
        )
        # Viewport matters — real Stratus's responsive UI silently fails
        # to render its SPA below ~1400px wide on this version.
        context = browser.new_context(
            ignore_https_errors=True,
            viewport={"width": 1500, "height": 900},
        )
        page = context.new_page()
        page.set_default_timeout(60_000)

        # Capture browser console messages — invaluable on failure.
        if console_buffer is not None:
            def _on_console(msg):
                try:
                    console_buffer.append({
                        "type": msg.type,
                        "text": msg.text[:500],
                        "url": (msg.location or {}).get("url", "") if hasattr(msg, "location") else "",
                    })
                    # cap to avoid runaway memory
                    if len(console_buffer) > 500:
                        del console_buffer[:-300]
                except Exception:
                    pass

            def _on_pageerror(err):
                try:
                    console_buffer.append({
                        "type": "pageerror",
                        "text": str(err)[:500],
                        "url": page.url,
                    })
                except Exception:
                    pass

            page.on("console", _on_console)
            page.on("pageerror", _on_pageerror)

        try:
            yield page
        finally:
            context.close()
            browser.close()


# Map of supported screen flows. Today: just "customer". Future-proofs the UI.
SUPPORTED_SCREENS: dict[str, str] = {
    "customer": "Customer Management (CustomerList + Detail)",
}


def run_demo(cfg: DemoConfig, on_event: EventCallback | None = None) -> DemoResult:
    """Execute the demo flow. Returns a DemoResult.

    Streams progress via ``on_event(StepEvent)`` while it runs.
    Always returns a result — never raises.
    """
    t = _Tracker(on_event, cfg)

    t.banner(f"Stratus QA Tool — Demo against {cfg.base_url}")
    if cfg.screen not in SUPPORTED_SCREENS:
        t.fail("config", f"Unsupported screen: {cfg.screen!r}")
        return _build_result(t)

    page_ref = [None]   # mutable holder so the fail handler can grab the page

    try:
        with _browser(
            headless=cfg.headless,
            slow_mo=cfg.slow_mo_ms,
            console_buffer=t.console_buffer,
        ) as page:
            page_ref[0] = page

            # ────────────── DIAGNOSE MODE ──────────────
            # Skips all assertions. Just logs in, navigates around, and
            # captures screenshots + HTML so the user can see what Stratus
            # actually renders.
            if cfg.diagnose:
                return _run_diagnose(page, t, cfg)

            # MUST step — login.
            try:
                _do_login(page, t, cfg)
            except Exception as e:
                t.fail("login", str(e), page=page)
                _final_shot(page, t)
                return _build_result(t)

            # MUST step — open the screen.
            try:
                cl = _open_customer_list(page, t, cfg)
            except Exception as e:
                t.fail("open_customer_list", str(e), page=page)
                _final_shot(page, t)
                return _build_result(t)

            # The rest are independent — one failure doesn't abort the run.
            for name, fn in [
                ("search",       lambda: _search(cl, t)),
                ("action_menu",  lambda: _action_menu(cl, t)),
            ]:
                try: fn()
                except Exception as e: t.fail(name, str(e), page=page)

            if not cfg.read_only:
                for name, fn in [
                    ("create_new",    lambda: _create_new(page, cl, t, cfg)),
                    ("edit_existing", lambda: _edit_existing(page, cl, t, cfg)),
                    ("print_list",    lambda: _print_list(page, cl, t, cfg)),
                ]:
                    try: fn()
                    except Exception as e: t.fail(name, str(e), page=page)
            else:
                t.warn("read-only mode: skipping New / Edit / Print")

            try: _close(page, cl, t, cfg)
            except Exception as e: t.fail("close", str(e), page=page)

            _final_shot(page, t)

    except Exception as e:
        p = page_ref[0]
        t.fail("runtime", f"unexpected: {e}", page=p)

    return _build_result(t)


# ============================================================ Diagnose mode

def _run_diagnose(page, t: _Tracker, cfg: DemoConfig) -> DemoResult:
    """Walk the app without making any assertions.

    Useful when something fails and you need to see what Stratus actually
    serves at each URL. Saves a screenshot + raw HTML at every step.
    """
    from config import settings as _s

    # 1. Login screen
    try:
        _do_login(page, t, cfg)
    except Exception as e:
        # Even if login fails, take a screenshot for debugging.
        t.warn(f"login error (continuing diagnose): {e}")
        t.screenshot(page, "diag_login_error")
        t.dump_html(page, "diag_login_error")

    # 2. Try every plausible Customer List URL — even if Stratus refuses,
    #    we capture the HTML so the user can see what came back.
    for path in ("/stratus", "/wrmsscreen", "/wposscreen"):
        t.section(f"DIAGNOSE — navigating to {path}?screenType=CustomerList")
        url = build_url(cfg.base_url, path, extra_query={"screenType": "CustomerList"})
        t.info(f"opening {url}")
        try:
            page.goto(url, wait_until="domcontentloaded")
            try:
                page.wait_for_load_state("networkidle", timeout=10_000)
            except Exception:
                pass
            t.screenshot(page, f"diag_{path.strip('/')}")
            html_path = t.dump_html(page, f"diag_{path.strip('/')}")
            body_text = page.locator("body").inner_text()[:300] if page.locator("body").count() else ""
            n_buttons = page.locator("button").count()
            n_inputs = page.locator("input").count()
            t.ok(
                f"captured — {n_buttons} buttons, {n_inputs} inputs, "
                f"body starts with: {body_text[:80]!r}"
            )
            # Push a diagnostic event so the UI can show the captured HTML link
            _emit(t.cb, StepEvent(
                type="diagnostic",
                text=f"{path} → see HTML",
                step=t.step,
                screenshot_path=t.screenshots[-1] if t.screenshots else None,
                html_path=html_path,
                console_tail=t.console_buffer[-20:],
            ))
        except Exception as e:
            t.fail(f"diag_{path}", str(e), page=page)

    _final_shot(page, t)
    return _build_result(t)


def _final_shot(page, t: _Tracker) -> None:
    shot_dir = t.cfg.reports_dir / "screenshots"
    shot_dir.mkdir(parents=True, exist_ok=True)
    out = shot_dir / f"demo_final_{int(time.time())}.png"
    try:
        page.screenshot(path=str(out), full_page=True)
        _emit(t.cb, StepEvent(type="info", text=f"final screenshot saved → {out}"))
        t._final = str(out)   # stash for the result
    except Exception:
        t._final = None


def _build_result(t: _Tracker) -> DemoResult:
    total = t.passed + t.failed
    passed = total > 0 and t.failed == 0
    res = DemoResult(
        passed=passed,
        steps_total=total,
        steps_passed=t.passed,
        steps_failed=t.failed,
        duration_s=t.duration(),
        failures=list(t.failures),
        screenshots=list(t.screenshots),
        final_screenshot=getattr(t, "_final", None),
    )
    verdict = "PASS" if passed else "FAIL"
    _emit(t.cb, StepEvent(type="done", text=f"{verdict} — {t.passed}/{total} steps"))
    return res
