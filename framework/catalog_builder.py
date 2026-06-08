"""Screen Catalog Builder.

One-time scan of every Stratus screen → produces a catalog (JSON) with
EVERY visible field, button, dropdown, action menu item etc. on each
screen.

The catalog is then used by ``test_generator.py`` to produce comprehensive
auto-tests for any selected screen — DIFFERENT tests for list vs detail
vs report screens, all driven from what's actually in the catalog.

This is the "Knowledge Base" from the original architecture: built ONCE,
used to test 200+ screens forever.

Run via:
    from framework.catalog_builder import build_catalog
    build_catalog(cfg, on_event=callback)
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable

from framework.crawl_runner import (
    ScreenSpec, _login, _discover_screens, _classify, _browser,
)
from framework.demo_runner import (
    DemoConfig, StepEvent, _Tracker, _emit, build_url,
)


# ============================================================ catalog model

@dataclass
class CatalogField:
    id: str = ""
    name: str = ""
    label: str = ""
    type: str = "text"          # text | number | password | date | select | checkbox | radio | textarea
    required: bool = False
    placeholder: str = ""
    max_length: int = 0
    options: list = field(default_factory=list)   # for select/radio


@dataclass
class CatalogButton:
    id: str = ""
    name: str = ""
    text: str = ""
    location: str = ""          # topnav | actionmenu | inline | form | other


@dataclass
class CatalogScreen:
    screenname: str
    label: str
    type: str                   # list | detail | wizard | report | other
    data_href: str
    rendered: bool = False
    error_words: list = field(default_factory=list)
    fields: list[CatalogField] = field(default_factory=list)
    topnav_buttons: list[CatalogButton] = field(default_factory=list)
    action_menu_items: list[CatalogButton] = field(default_factory=list)
    form_buttons: list[CatalogButton] = field(default_factory=list)
    other_buttons: list[CatalogButton] = field(default_factory=list)
    has_grid: bool = False
    grid_columns: list = field(default_factory=list)
    tabs: list = field(default_factory=list)


# ============================================================ analyzer

# JS that pulls every interactive element off the page.
_DOM_DUMP_JS = """() => {
    const visible = (el) => {
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
    };
    const txt = (el, n=40) => {
        const t = (el.innerText || el.textContent || '').trim().replace(/\\s+/g,' ');
        return t.length > n ? t.slice(0, n-1)+'…' : t;
    };

    // -- Fields (inputs / selects / textareas) --
    const fields = [];
    document.querySelectorAll('input, select, textarea').forEach(el => {
        if (!visible(el)) return;
        const tag = el.tagName.toLowerCase();
        const type = (tag === 'select') ? 'select'
                   : (tag === 'textarea') ? 'textarea'
                   : (el.type || 'text');
        if (type === 'hidden' || type === 'submit' || type === 'button') return;
        // Find an associated label
        let label = '';
        if (el.id) {
            const lab = document.querySelector(`label[for="${el.id}"]`);
            if (lab) label = (lab.innerText || '').trim();
        }
        if (!label && el.name) label = el.name;

        const f = {
            id: el.id || '', name: el.name || '', label,
            type, required: el.required || el.getAttribute('data-required')==='Y',
            placeholder: el.placeholder || '',
            max_length: parseInt(el.maxLength || el.getAttribute('maxlength') || 0) || 0,
            options: [],
        };
        if (tag === 'select') {
            f.options = Array.from(el.options || [])
                .map(o => (o.text || o.value || '').trim())
                .filter(t => t && t.toLowerCase() !== 'select');
        }
        fields.push(f);
    });

    // -- Buttons (categorized by location) --
    const buttons = {topnav: [], actionmenu: [], form: [], other: []};
    document.querySelectorAll('button, a.btn').forEach(b => {
        if (!visible(b)) return;
        const text = txt(b);
        if (!text && !b.id && !b.name) return;
        const entry = {id: b.id || '', name: b.name || '', text};
        // Categorize by location
        if (b.closest('#topNav, .topNav, .top-nav')) {
            buttons.topnav.push(entry);
        } else if (b.closest('#actionButtonMenu, .action-btn-menu, .dropdown-menu')) {
            buttons.actionmenu.push(entry);
        } else if (b.closest('form, #componentFormId')) {
            buttons.form.push(entry);
        } else {
            buttons.other.push(entry);
        }
    });

    // -- Grid columns (jqGrid) --
    const grid_columns = [];
    document.querySelectorAll('.ui-jqgrid-htable th, table.ui-jqgrid-btable thead th').forEach(th => {
        const t = txt(th, 30);
        if (t) grid_columns.push(t);
    });

    // -- Tabs --
    const tabs = [];
    document.querySelectorAll('.tabPanel[data-title]').forEach(t => {
        tabs.push(t.getAttribute('data-title'));
    });

    return {
        title: document.title,
        url: location.href,
        bodyTextStart: (document.body.innerText || '').trim().slice(0, 300),
        fields, buttons, grid_columns, tabs,
        has_grid: !!document.querySelector('.ui-jqgrid, table.ui-jqgrid-btable'),
        has_topnav: !!document.querySelector('#topNav, .topNav'),
        has_form: !!document.querySelector('form#componentFormId, form'),
    };
}"""


_ERROR_WORDS = [
    "session expired", "unauthorized", "access denied",
    "forbidden", "permission", "internal server error",
    "exception:", "http status 500", "http status 404",
]


def _analyze_one_screen(page, spec: ScreenSpec, cfg: DemoConfig) -> CatalogScreen:
    """Navigate to a screen and capture EVERYTHING that's on it."""
    cat = CatalogScreen(
        screenname=spec.screenname, label=spec.label,
        type=spec.type, data_href=spec.data_href,
    )
    full_url = build_url(cfg.base_url, spec.data_href)

    try:
        page.goto(full_url, wait_until="domcontentloaded", timeout=20_000)
    except Exception:
        try: page.goto(full_url, wait_until="commit", timeout=10_000)
        except Exception: return cat

    # Adaptive settle (same trick as crawl_runner)
    time.sleep(3.5)
    quick = page.evaluate("""() => ({
        n: document.querySelectorAll('button:not([disabled]), input:not([type=hidden])').length,
        t: !!document.querySelector('#topNav, .screen-title-header, .ui-jqgrid')
    })""")
    if quick.get("n", 0) == 0 and not quick.get("t"):
        time.sleep(4)

    # Try to expand the Action dropdown so we capture its items too.
    # Lenient — many screens don't have one.
    try:
        page.evaluate("""() => {
            const a = document.querySelector(
                '#topNav .action-btn, a.dropdown-toggle.action-btn'
            );
            if (a) { try { a.click(); } catch(e) {} }
        }""")
        time.sleep(0.4)
    except Exception:
        pass

    try:
        dump = page.evaluate(_DOM_DUMP_JS)
    except Exception:
        return cat

    cat.rendered = bool(
        dump.get("has_topnav") or dump.get("has_grid")
        or dump.get("has_form") or (dump.get("fields") or [])
    )
    body = (dump.get("bodyTextStart") or "").lower()
    cat.error_words = [w for w in _ERROR_WORDS if w in body]

    # Map fields
    for f in dump.get("fields") or []:
        cat.fields.append(CatalogField(
            id=f.get("id",""), name=f.get("name",""), label=f.get("label",""),
            type=f.get("type","text"), required=bool(f.get("required")),
            placeholder=f.get("placeholder",""),
            max_length=int(f.get("max_length") or 0),
            options=list(f.get("options") or []),
        ))

    # Map buttons
    for b in (dump.get("buttons") or {}).get("topnav", []):
        cat.topnav_buttons.append(CatalogButton(
            id=b.get("id",""), name=b.get("name",""),
            text=b.get("text",""), location="topnav",
        ))
    for b in (dump.get("buttons") or {}).get("actionmenu", []):
        cat.action_menu_items.append(CatalogButton(
            id=b.get("id",""), name=b.get("name",""),
            text=b.get("text",""), location="actionmenu",
        ))
    for b in (dump.get("buttons") or {}).get("form", []):
        cat.form_buttons.append(CatalogButton(
            id=b.get("id",""), name=b.get("name",""),
            text=b.get("text",""), location="form",
        ))
    for b in (dump.get("buttons") or {}).get("other", []):
        cat.other_buttons.append(CatalogButton(
            id=b.get("id",""), name=b.get("name",""),
            text=b.get("text",""), location="other",
        ))

    cat.has_grid = bool(dump.get("has_grid"))
    cat.grid_columns = list(dump.get("grid_columns") or [])
    cat.tabs = list(dump.get("tabs") or [])

    return cat


# ============================================================ public API

CATALOG_PATH = Path(__file__).resolve().parent.parent / "knowledge_base" / "screens_catalog.json"


def build_catalog(
    cfg: DemoConfig,
    on_event: Callable[[StepEvent], None] | None = None,
    type_filter: set[str] | None = None,
    max_screens: int | None = None,
    scope: str = "",
) -> dict:
    """Build the screen catalog by visiting every screen.

    Saves to ``knowledge_base/screens_catalog.json`` and returns the catalog
    as a dict.
    """
    t = _Tracker(on_event, cfg)
    t.banner(f"Building Stratus screen catalog from {cfg.base_url}")
    out: list[CatalogScreen] = []

    try:
        with _browser(headless=cfg.headless) as page:
            t.section("Authenticating")
            try:
                landed = _login(page, cfg, t)
                t.info(f"post-login URL: {landed}")
                t.ok("logged in")
            except Exception as e:
                t.fail("login", str(e), page=page)
                return {"screens": [], "error": str(e)}

            t.section("Reading screen menu")
            try:
                screens = _discover_screens(page, cfg, t)
            except Exception as e:
                t.fail("discover", str(e), page=page)
                return {"screens": [], "error": str(e)}

            # Filters
            if scope:
                scope_l = scope.lower()
                screens = [s for s in screens
                           if scope_l in s.screenname.lower() or scope_l in s.label.lower()]
            if type_filter:
                screens = [s for s in screens if s.type in type_filter]
            if max_screens:
                screens = screens[:max_screens]
            t.ok(f"will catalog {len(screens)} screen(s)")

            t.section("Visiting each screen and capturing every element")
            for i, spec in enumerate(screens, 1):
                t.info(f"[{i}/{len(screens)}] {spec.screenname} ({spec.type})")
                try:
                    cat = _analyze_one_screen(page, spec, cfg)
                    out.append(cat)
                    t.ok(
                        f"  {spec.label}: "
                        f"{len(cat.fields)} fields, "
                        f"{len(cat.topnav_buttons)} topnav, "
                        f"{len(cat.action_menu_items)} actions, "
                        f"{len(cat.grid_columns)} grid cols"
                    )
                except Exception as e:
                    t.warn(f"  catalog failed: {e}")

            CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "base_url": cfg.base_url,
                "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "screen_count": len(out),
                "screens": [asdict(s) for s in out],
            }
            CATALOG_PATH.write_text(json.dumps(payload, indent=2))
            t.ok(f"catalog saved → {CATALOG_PATH}")
            return payload

    except Exception as e:
        t.fail("runtime", f"unexpected: {e}")
        return {"screens": [], "error": str(e)}


def load_catalog() -> dict | None:
    if not CATALOG_PATH.exists():
        return None
    try:
        return json.loads(CATALOG_PATH.read_text())
    except Exception:
        return None
