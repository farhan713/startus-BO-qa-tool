"""Stratus QA Tool — Web Console (v0.2 — wizard UI).

Endpoints:
  /                          → SPA shell
  POST /api/run              → start a new run
  GET  /api/events           → SSE stream of run events
  GET  /api/status           → snapshot of current run state
  POST /api/test-connection  → cheap reachability check (no full test)
  GET  /api/profiles         → list saved connection profiles
  POST /api/profiles         → save a profile
  DELETE /api/profiles/<id>  → delete a profile
  GET  /api/history          → list past runs (last 25)
  GET  /screenshots/<file>   → serve a captured screenshot
  GET  /report.html          → serve pytest's HTML report (if present)
"""
from __future__ import annotations

import json
import os
import queue
import socket
import sys
import threading
import time
from dataclasses import asdict

# Load qa-automation/.env into the process so optional integrations
# (Gemini API key, etc.) are picked up automatically when launch.sh runs.
try:
    from dotenv import load_dotenv
    _env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(_env_path):
        load_dotenv(_env_path, override=False)
except Exception:
    pass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import requests

# Make project modules importable.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from flask import (
    Flask, Response, jsonify, render_template,
    request, send_file, send_from_directory, session,
)

from framework.demo_runner import (
    DemoConfig, DemoResult, StepEvent, SUPPORTED_SCREENS, run_demo,
)
from framework.api_demo_runner import run_api_demo
from framework.crawl_runner import run_crawl
from framework.catalog_builder import build_catalog, load_catalog, CATALOG_PATH
from framework.single_screen_runner import run_single_screen
from framework.test_generator import generate_tests
from framework.catalog_analyzer import analyze as analyze_catalog
from framework.bulk_runner import run_bulk
from web_ui.report_builder import build_run_report

app = Flask(__name__, template_folder="templates", static_folder="static")

# ============================================================ Auth
from framework import auth as _auth
app.secret_key = _auth.session_secret()
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# Sessions persist 30 days — laptops typically run one tester for a long time.
from datetime import timedelta as _td
app.permanent_session_lifetime = _td(days=30)


# ---- auth endpoints --------------------------------------------------------

@app.route("/api/auth/status")
def api_auth_status():
    """Tell the SPA who's logged in (and whether bootstrap is needed)."""
    return jsonify({
        "username": _auth.current_user(),
        "role":     _auth.current_role(),
        "has_users": _auth.has_users(),
    })


@app.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    data = request.get_json(force=True) or {}
    username = (data.get("username") or "").strip().lower()
    password = data.get("password") or ""
    if not _auth.verify_password(username, password):
        return jsonify({"error": "invalid username or password"}), 401
    session.permanent = True
    session["username"] = username
    _auth.stamp_login(username)
    u = _auth.get_user(username) or {}
    return jsonify({"username": username, "role": u.get("role", "user")})


@app.route("/api/auth/logout", methods=["POST"])
def api_auth_logout():
    session.pop("username", None)
    return jsonify({"ok": True})


@app.route("/api/auth/register", methods=["POST"])
def api_auth_register():
    """Bootstrap: the FIRST POST (when there are zero users) creates an
    admin without any auth. Subsequent calls require an admin session."""
    data = request.get_json(force=True) or {}
    username = (data.get("username") or "").strip().lower()
    password = data.get("password") or ""
    role     = data.get("role") or "user"

    if not _auth.has_users():
        # Bootstrap — first ever user is admin
        try:
            _auth.create_user(username, password, role="admin")
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        # Auto-login the bootstrap user
        session.permanent = True
        session["username"] = username
        _auth.stamp_login(username)
        return jsonify({"username": username, "role": "admin", "bootstrap": True})

    # Subsequent registrations need an admin caller
    if _auth.current_role() != "admin":
        return jsonify({"error": "admin only"}), 403
    try:
        _auth.create_user(username, password, role=role)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"username": username, "role": role})


@app.route("/api/auth/users")
@_auth.login_required
def api_auth_users():
    return jsonify({"users": _auth.list_users()})


@app.route("/api/auth/users/<username>", methods=["DELETE"])
@_auth.admin_required
def api_auth_users_delete(username: str):
    if username == _auth.current_user():
        return jsonify({"error": "you can't delete yourself"}), 400
    ok = _auth.delete_user(username)
    return jsonify({"ok": ok})


@app.route("/api/auth/password", methods=["POST"])
@_auth.login_required
def api_auth_change_password():
    """Change your own password. Admins may pass `username` to change someone else's."""
    data = request.get_json(force=True) or {}
    target = (data.get("username") or _auth.current_user() or "").strip().lower()
    new_pw = data.get("new_password") or ""
    if target != _auth.current_user() and _auth.current_role() != "admin":
        return jsonify({"error": "admin only"}), 403
    try:
        _auth.change_password(target, new_pw)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True})
# ============================================================

REPORTS_DIR = ROOT / "reports"
SCREENSHOTS_DIR = REPORTS_DIR / "screenshots"
STATE_DIR = ROOT / ".pids"
PROFILES_FILE = STATE_DIR / "profiles.json"
HISTORY_FILE = STATE_DIR / "history.json"

for d in (REPORTS_DIR, SCREENSHOTS_DIR, STATE_DIR):
    d.mkdir(parents=True, exist_ok=True)


# ============================================================ run state

class RunState:
    def __init__(self) -> None:
        self.id: str = ""
        self.queue: queue.Queue[StepEvent] = queue.Queue()
        self.result: DemoResult | None = None
        self.running: bool = False
        self.started_at: float = 0.0
        self.config_snapshot: dict | None = None
        # Cache the full event log so the UI can rehydrate after reload.
        self.event_log: list[dict] = []


CURRENT = RunState()
LOCK = threading.Lock()


def _run_with_net(fn, *args, **kwargs):
    """Call a runner that stashes network log on its tracker; return (result, network)."""
    res = fn(*args, **kwargs)
    # The runner attaches network_log to the tracker, but tracker is internal.
    # We can read the last network log from the runner's module-level cache.
    # Simpler: just inspect the last tracker via a class attribute.
    from framework.demo_runner import _Tracker
    net = getattr(_Tracker, "_last_network_log", [])
    return res, net


# ============================================================ persistence helpers

def _read_json(p: Path, default):
    try:
        if p.exists():
            return json.loads(p.read_text())
    except Exception:
        pass
    return default


def _write_json(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2))


# ============================================================ routes

@app.route("/")
def index():
    return render_template(
        "index.html",
        screens=SUPPORTED_SCREENS,
        # `future_screens` removed — the catalog (~239 screens) already
        # covers SKU/Employee/POS/Consignment/Reports via the catalog-driven
        # modes (Crawl ALL / Single Screen / Bulk Auto-Test). The Jinja
        # template no longer references this variable.
    )


@app.route("/convert")
def convert_screen():
    """Convert Test Cases — the tool's main screen.

    Turns a tester's manual test cases (Excel / pasted prose / a plain-English
    description) into runnable YAML, asks about anything it couldn't work out,
    then saves or runs it. All work is done by the existing APIs:
    /api/import-testcases, /api/nl-to-yaml, /api/modify-testcases,
    /api/scenarios and /api/run.
    """
    return render_template("convert.html")


# ---------------------------------------------------------- run

def _run_guard(fn):
    """Auth guard for /api/run.

    Normally this is `login_required`. Setting QA_DEMO_OPEN_RUN=1 removes it so
    the Convert screen can be demonstrated on its own, without first creating an
    account. It is OFF unless explicitly switched on, and the switch lives in
    .env (gitignored) so it never ships. Turn it off again by deleting the line.

    Do not enable this on a shared or internet-reachable host: the server binds
    0.0.0.0 with no TLS, and /api/run drives a real browser against a real
    Stratus install.
    """
    if os.environ.get("QA_DEMO_OPEN_RUN", "").strip() in ("1", "true", "yes", "on"):
        return fn
    return _auth.login_required(fn)


@app.route("/api/run", methods=["POST"])
@_run_guard
def api_run():
    global CURRENT
    with LOCK:
        if CURRENT.running:
            return jsonify({"error": "A run is already in progress"}), 409

        data = request.get_json(force=True)
        cfg = DemoConfig(
            base_url=(data.get("url") or "").strip(),
            user=(data.get("user") or "").strip(),
            password=data.get("password") or "",
            screen=(data.get("screen") or "customer").strip(),
            machine_id=(data.get("machine_id") or "").strip(),
            headless=True,
            slow_mo_ms=0,
            read_only=bool(data.get("read_only", False)),
            diagnose=bool(data.get("diagnose", False)),
            capture_step_screenshots=True,
            capture_html=True,
            reports_dir=REPORTS_DIR,
        )
        api_mode = bool(data.get("api_mode", False))
        crawl_mode = bool(data.get("crawl_mode", False))
        single_mode = bool(data.get("single_mode", False))
        catalog_mode = bool(data.get("catalog_mode", False))
        bulk_mode = bool(data.get("bulk_mode", False))
        single_screenname = (data.get("single_screenname") or "").strip()
        single_safe = bool(data.get("single_safe", True))
        crawl_scope = (data.get("crawl_scope") or "").strip()
        crawl_max = int(data.get("crawl_max") or 0) or None
        crawl_types = data.get("crawl_types") or []
        if not isinstance(crawl_types, list):
            crawl_types = []
        custom_tests_yaml = data.get("custom_tests_yaml") or ""
        selected_screens = data.get("selected_screens") or []
        if not isinstance(selected_screens, list):
            selected_screens = []

        if not cfg.base_url or not cfg.user or not cfg.password:
            return jsonify({"error": "URL, user, and password are required"}), 400

        CURRENT = RunState()
        CURRENT.id = uuid4().hex[:8]
        CURRENT.running = True
        CURRENT.started_at = time.time()
        CURRENT.config_snapshot = {
            "url": cfg.base_url, "user": cfg.user,
            "screen": cfg.screen, "read_only": cfg.read_only,
            "machine_id": cfg.machine_id,
        }

    def _worker(c: DemoConfig, state: RunState, use_api: bool,
                use_crawl: bool, use_single: bool, use_catalog: bool,
                use_bulk: bool,
                single_screen: str, single_safe_mode: bool,
                scope: str, max_n: int | None,
                types: list, custom_yaml: str,
                sel_screens: list) -> None:
        def on_event(evt: StepEvent) -> None:
            # Always record into the persistent event log so the HTML report
            # has data even if no browser is connected to the SSE stream.
            try:
                if evt.type != "__end__":
                    state.event_log.append({
                        "type": evt.type,
                        "text": evt.text,
                        "step": evt.step,
                        "screenshot": _screenshot_url(evt.screenshot_path) if evt.screenshot_path else None,
                        "html": _html_url(evt.html_path) if evt.html_path else None,
                        "console": evt.console_tail,
                    })
            except Exception:
                pass
            state.queue.put(evt)
        try:
            if use_catalog:
                build_catalog(
                    c, on_event,
                    type_filter=set(types) if types else None,
                    max_screens=max_n, scope=scope,
                )
                res = DemoResult(passed=True, steps_total=1, steps_passed=1,
                                 steps_failed=0, duration_s=0)
            elif use_bulk:
                res = run_bulk(
                    c, on_event,
                    type_filter=set(types) if types else None,
                    scope=scope, max_screens=max_n,
                    safe_mode=single_safe_mode,
                    selected_screens=sel_screens or None,
                )
            elif use_single:
                res = run_single_screen(
                    c, single_screen, on_event,
                    safe_mode=single_safe_mode,
                    custom_tests_yaml=custom_yaml,
                )
            elif use_crawl:
                res = run_crawl(
                    c, on_event,
                    scope=scope, max_screens=max_n,
                    type_filter=set(types) if types else None,
                    custom_tests_yaml=custom_yaml,
                    selected_screens=sel_screens or None,
                )
            elif use_api:
                res = run_api_demo(c, on_event)
            else:
                res = run_demo(c, on_event)
        except Exception as e:
            state.queue.put(StepEvent(type="fail", text=f"runtime: {e}"))
            res = DemoResult(passed=False, steps_total=0, steps_passed=0,
                             steps_failed=1, duration_s=0,
                             failures=[("runtime", str(e))])
        state.result = res
        state.running = False
        # Pull network log + browser console captured during this run from
        # the tracker's class-level cache. _Tracker mirrors any list assigned
        # to its `network_log` / `console_log` attrs into class attrs so the
        # Flask layer can read them without holding a tracker reference.
        try:
            from framework.demo_runner import _Tracker as _Trk
            state.network_log = list(getattr(_Trk, "_last_network_log", []) or [])
            state.console_log = list(getattr(_Trk, "_last_console_log", []) or [])
        except Exception:
            state.network_log = []
            state.console_log = []
        state.queue.put(StepEvent(type="__end__"))
        # Generate a self-contained HTML report for THIS run
        try:
            snap = dict(state.config_snapshot or {})
            snap["started_at"] = state.started_at
            snap["run_id"] = state.id
            build_run_report(
                out_path=REPORTS_DIR / "last_run.html",
                config=snap,
                result=asdict(res),
                events=state.event_log,
                network=getattr(state, "network_log", []),
                console=getattr(state, "console_log", []),
            )
        except Exception as e:
            print(f"  [warn] report build failed: {e}")
        # Persist to history
        _append_history({
            "id": state.id,
            "ts": datetime.now().isoformat(timespec="seconds"),
            "config": state.config_snapshot,
            "passed": res.passed,
            "steps_total": res.steps_total,
            "steps_passed": res.steps_passed,
            "steps_failed": res.steps_failed,
            "duration_s": res.duration_s,
            "failures": res.failures,
        })

    CURRENT.config_snapshot["api_mode"] = api_mode
    CURRENT.config_snapshot["crawl_mode"] = crawl_mode
    CURRENT.config_snapshot["single_mode"] = single_mode
    CURRENT.config_snapshot["catalog_mode"] = catalog_mode
    CURRENT.config_snapshot["bulk_mode"] = bulk_mode
    CURRENT.config_snapshot["single_screenname"] = single_screenname
    CURRENT.config_snapshot["crawl_scope"] = crawl_scope
    CURRENT.config_snapshot["crawl_types"] = crawl_types
    CURRENT.config_snapshot["custom_tests"] = bool(custom_tests_yaml.strip())
    CURRENT.config_snapshot["selected_screens_count"] = len(selected_screens)
    threading.Thread(
        target=_worker,
        args=(cfg, CURRENT, api_mode, crawl_mode, single_mode, catalog_mode,
              bulk_mode, single_screenname, single_safe, crawl_scope, crawl_max,
              crawl_types, custom_tests_yaml, selected_screens),
        daemon=True,
    ).start()
    return jsonify({"run_id": CURRENT.id})


@app.route("/api/events")
def api_events():
    def stream():
        state = CURRENT
        while True:
            try:
                evt = state.queue.get(timeout=30)
            except queue.Empty:
                yield ": ping\n\n"
                continue
            if evt.type == "__end__":
                yield "event: end\ndata: {}\n\n"
                return
            payload = {
                "type": evt.type,
                "text": evt.text,
                "step": evt.step,
                "screenshot": _screenshot_url(evt.screenshot_path) if evt.screenshot_path else None,
                "html": _html_url(evt.html_path) if evt.html_path else None,
                "console": evt.console_tail,
            }
            # Note: state.event_log is populated by the worker's on_event
            # callback so the HTML report has data even without an SSE client.
            yield f"data: {json.dumps(payload)}\n\n"
    return Response(stream(), mimetype="text/event-stream")


@app.route("/api/status")
def api_status():
    res = CURRENT.result
    return jsonify({
        "running": CURRENT.running,
        "started_at": CURRENT.started_at,
        "config": CURRENT.config_snapshot,
        "result": asdict(res) if res else None,
        "event_log": CURRENT.event_log,
    })


# ---------------------------------------------------------- connection test

@app.route("/api/test-connection", methods=["POST"])
def api_test_connection():
    """Cheap reachability + login.jsp probe. No browser, no test run."""
    data = request.get_json(force=True)
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"ok": False, "error": "URL is required"}), 400

    out = {"url": url, "checks": []}

    # Check 1 — TCP reach
    try:
        p = urlparse(url)
        host = p.hostname
        port = p.port or (443 if p.scheme == "https" else 80)
        sock = socket.create_connection((host, port), timeout=5)
        sock.close()
        out["checks"].append({"name": "TCP connection", "ok": True, "detail": f"{host}:{port} reachable"})
    except Exception as e:
        out["checks"].append({"name": "TCP connection", "ok": False, "detail": str(e)})
        out["ok"] = False
        return jsonify(out)

    # Check 2 — HTTP/HTTPS to login.jsp
    from framework.demo_runner import build_url
    login_url = build_url(url, "/login.jsp")
    try:
        r = requests.get(login_url, timeout=8, verify=False, allow_redirects=True)
        ok = r.status_code < 500
        out["checks"].append({
            "name": "HTTP fetch login.jsp",
            "ok": ok,
            "detail": f"HTTP {r.status_code} ({len(r.content)} bytes)",
        })
        # Check 3 — does it look like a Stratus login page?
        body = r.text.lower()
        has_login_form = "userid" in body and "passwd" in body
        out["checks"].append({
            "name": "Stratus login form present",
            "ok": has_login_form,
            "detail": "found #userid + #passwd fields" if has_login_form
                     else "login form not detected (check URL / path)",
        })
    except requests.exceptions.SSLError as e:
        out["checks"].append({
            "name": "HTTPS handshake",
            "ok": False,
            "detail": f"SSL error: {e}. Self-signed certs are OK at run time.",
        })
    except Exception as e:
        out["checks"].append({
            "name": "HTTP fetch login.jsp",
            "ok": False,
            "detail": str(e),
        })

    out["ok"] = all(c["ok"] for c in out["checks"])
    return jsonify(out)


# ---------------------------------------------------------- profiles

@app.route("/api/profiles")
def api_profiles():
    return jsonify(_read_json(PROFILES_FILE, []))


@app.route("/api/profiles", methods=["POST"])
def api_profile_save():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    profiles = _read_json(PROFILES_FILE, [])
    profiles = [p for p in profiles if p.get("name") != name]
    profiles.append({
        "name": name,
        "url": data.get("url", ""),
        "user": data.get("user", ""),
        "machine_id": data.get("machine_id", ""),
        # NOTE: we deliberately don't store passwords on disk.
    })
    _write_json(PROFILES_FILE, profiles)
    return jsonify({"ok": True, "profiles": profiles})


@app.route("/api/profiles/<name>", methods=["DELETE"])
def api_profile_delete(name: str):
    profiles = _read_json(PROFILES_FILE, [])
    profiles = [p for p in profiles if p.get("name") != name]
    _write_json(PROFILES_FILE, profiles)
    return jsonify({"ok": True, "profiles": profiles})


# ---------------------------------------------------------- history

@app.route("/api/history")
@_auth.login_required
def api_history():
    """Run history filtered to the current user (admin sees all)."""
    rows = _read_json(HISTORY_FILE, [])
    me = _auth.current_user()
    if _auth.current_role() != "admin":
        rows = [r for r in rows if (r.get("owner") or "") in ("", me)]
    return jsonify(rows[-25:][::-1])


def _append_history(entry: dict) -> None:
    # Stamp owner so per-user filtering works.
    # This runs on the background run thread, where there is no Flask request
    # context, so current_user() raises. Without the guard the worker dies here
    # and no run is ever written to history.
    if "owner" not in entry:
        try:
            entry["owner"] = _auth.current_user() or ""
        except Exception:
            entry["owner"] = ""
    h = _read_json(HISTORY_FILE, [])
    h.append(entry)
    _write_json(HISTORY_FILE, h[-200:])  # keep last 200


# ---------------------------------------------------------- assets

@app.route("/screenshots/<path:filename>")
def screenshot(filename: str):
    return send_from_directory(SCREENSHOTS_DIR, filename)


@app.route("/api/catalog")
def api_catalog():
    """Return the saved screen catalog (or {} if not yet built)."""
    cat = load_catalog() or {}
    # Trim heavy fields so the dropdown loads fast
    if cat.get("screens"):
        light = [{
            "screenname": s["screenname"],
            "label": s.get("label", ""),
            "type": s.get("type", ""),
            "field_count": len(s.get("fields") or []),
            "button_count": (len(s.get("topnav_buttons") or [])
                           + len(s.get("action_menu_items") or [])
                           + len(s.get("form_buttons") or [])),
        } for s in cat["screens"]]
        return jsonify({
            "built_at": cat.get("built_at"),
            "screen_count": cat.get("screen_count"),
            "screens": light,
        })
    return jsonify({"screens": [], "built_at": None})


@app.route("/api/catalog/analyze")
def api_catalog_analyze():
    """Return a full enterprise analysis of the catalog."""
    return jsonify(analyze_catalog())


@app.route("/api/catalog/<screenname>")
def api_catalog_one(screenname: str):
    """Return the full catalog entry for one screen + auto-generated test plan."""
    cat = load_catalog() or {}
    for s in cat.get("screens") or []:
        if s.get("screenname", "").lower() == screenname.lower():
            tests = generate_tests(s, safe_mode=True)
            return jsonify({
                "entry": s,
                "auto_tests": [{"name": t.name, "steps": t.steps} for t in tests],
            })
    return jsonify({"error": "not found"}), 404


def _yaml_for_screen(entry: dict, safe_mode: bool = True) -> str:
    """Render a screen's auto-generated tests as an editable, round-trippable
    YAML file. The output is accepted verbatim by parse_custom_tests(), so a
    user can download, tweak, and re-upload it as custom test cases."""
    import yaml  # PyYAML — already a dependency

    sn = entry.get("screenname", "screen")
    stype = entry.get("type", "other")
    tests = generate_tests(entry, safe_mode=safe_mode)

    # Build the data structure parse_custom_tests expects: {tests: [{screen,name,steps}]}
    doc_tests = []
    for t in tests:
        doc_tests.append({
            "screen": sn,
            "name": t.name,
            # Steps are plain dicts already ({action, target?, value?, ...})
            "steps": [dict(step) for step in t.steps],
        })

    body = yaml.safe_dump(
        {"tests": doc_tests},
        sort_keys=False, default_flow_style=False, allow_unicode=True, width=100,
    )

    header = (
        f"# ============================================================\n"
        f"#  Stratus QA — auto-generated test cases\n"
        f"#  Screen : {sn}  (type: {stype})\n"
        f"#  Tests  : {len(tests)}   ·   safe_mode: {safe_mode}\n"
        f"# ------------------------------------------------------------\n"
        f"#  This file was generated from the catalog. Edit it freely:\n"
        f"#    • change `value:` to test different inputs\n"
        f"#    • add / remove / reorder steps\n"
        f"#    • duplicate a test block to add your own scenario\n"
        f"#\n"
        f"#  Re-upload it in:  New run → One screen → 'Add custom test cases (YAML)'\n"
        f"#\n"
        f"#  Available actions:\n"
        f"#    open_search · fill · click · select · wait · screenshot\n"
        f"#    assert_visible · assert_text · assert_no_errors\n"
        f"#    assert_rows_min · assert_rows_max\n"
        f"#  Step format:  - {{ action: fill, target: '#fieldId', value: 'X' }}\n"
        f"# ============================================================\n\n"
    )
    return header + body


# Words that say nothing about *which* screen — never match on these.
_SCREEN_STOPWORDS = {
    "test", "check", "screen", "page", "module", "the", "and", "also",
    "for", "each", "not", "working", "work", "search", "data", "api",
    "call", "calls", "coming", "with", "run", "verify", "make", "sure",
    "every", "all", "this", "that", "from", "into", "click", "fill",
    "select", "save", "open", "show", "showing", "value", "field", "button",
}


def _resolve_screen_from_text(prompt: str, screens: list) -> tuple:
    """Pick the catalog screen a plain-English request is talking about.

    Signals, strongest first:
      1. The exact screenname appears in the text (after stripping spaces/punct),
         e.g. "test the customerlist" or "test the customer list".
      2. Token match with the screen's screenname/label — a prompt word counts
         as a hit if it equals a name token OR is a prefix of one (so "customer"
         matches "customers"/"customerlist", "receipt" matches "receiptslist").
         Stop-words ("test", "screen", "search"...) are ignored so only the
         topical words decide.

    Returns (entry|None, candidates[list of {screen,score}], how:str).
    """
    import re
    p = (prompt or "").lower()
    p_compact = re.sub(r"[^a-z0-9]", "", p)
    p_words = {w for w in re.findall(r"[a-z0-9]{3,}", p)
               if w not in _SCREEN_STOPWORDS}

    def _hit(name_token: str) -> str | None:
        """Return the prompt word that matches this name token, else None."""
        for pw in p_words:
            if name_token == pw:
                return pw
            if len(pw) >= 4 and (name_token.startswith(pw) or pw.startswith(name_token)):
                return pw
        return None

    best, best_score, best_how = None, 0.0, ""
    cands: list[dict] = []
    for s in screens:
        sn = (s.get("screenname") or "").lower()
        label = (s.get("label") or "").lower()
        score, how = 0.0, ""
        if len(sn) >= 5 and sn in p_compact:
            score, how = 1.0, f"screen name '{sn}' found in your text"
        else:
            name_tokens = [t for t in set(re.findall(r"[a-z0-9]{3,}", sn + " " + label))
                           if len(t) >= 4]
            if name_tokens:
                matched = {nt: _hit(nt) for nt in name_tokens}
                hit_words = {w for w in matched.values() if w}
                if hit_words:
                    hits = sum(1 for v in matched.values() if v)
                    # Fraction of the screen's identity that the prompt covered,
                    # lightly rewarded for matching more distinct prompt words.
                    score = (hits / len(name_tokens)) * 0.8
                    how = "matched on " + ", ".join(sorted(hit_words))
        if score > 0:
            cands.append({"screen": s.get("screenname"), "score": round(score, 2)})
            if score > best_score:
                best, best_score, best_how = s, score, how
    cands.sort(key=lambda c: -c["score"])
    if best is not None and best_score >= 0.4:
        return best, cands[:6], f"{best_how} (confidence {best_score:.2f})"
    return None, cands[:6], ""


@app.route("/api/nl-to-yaml", methods=["POST"])
def api_nl_to_yaml():
    """Generate runnable YAML from a plain-English test description — no file.

    POST JSON: { prompt, screen?, use_llm?, safe_mode? }

    Pipeline:
      1. Resolve which catalog screen the request is about (explicit `screen`
         wins; otherwise infer from the text).
      2. Generate the comprehensive auto-test plan for that screen
         (test_generator — purely catalog-driven, real field/button ids).
      3. Layer the user's plain-English refinements on top via the prompt
         engine (rule engine + optional Gemini).

    Returns { yaml, screen, screen_type, matched_how, candidates, n_fields,
              prompt }  — the YAML is ready to run in One-screen mode.
    """
    from framework.prompt_engine import apply_prompt
    data = request.get_json(force=True) or {}
    prompt = (data.get("prompt") or "").strip()
    explicit_screen = (data.get("screen") or "").strip()
    use_llm = bool(data.get("use_llm"))
    safe_mode = bool(data.get("safe_mode", True))

    if not prompt and not explicit_screen:
        return jsonify({"error": "Describe what to test — e.g. "
                                 "\"test the customer list, search by name, screenshot after each click\"."}), 400

    cat = load_catalog() or {}
    screens = cat.get("screens") or []
    if not screens:
        return jsonify({"error": "No catalog yet. Run New run → Rebuild catalog first."}), 400

    entry, candidates, matched_how = None, [], ""
    if explicit_screen:
        entry = next((s for s in screens
                      if (s.get("screenname") or "").lower() == explicit_screen.lower()), None)
        if entry is not None:
            matched_how = f"you picked '{explicit_screen}'"
    if entry is None:
        entry, candidates, matched_how = _resolve_screen_from_text(prompt, screens)
    if entry is None:
        return jsonify({
            "error": "Couldn't tell which screen you mean. Add the screen name "
                     "(e.g. 'customerlist') or pick one from the suggestions.",
            "candidates": candidates,
        }), 422

    sn = entry.get("screenname")
    base_yaml = _yaml_for_screen(entry, safe_mode=safe_mode)
    pr = apply_prompt(base_yaml, prompt, use_llm=use_llm)

    import yaml as _yaml
    try:
        n_tests = len((_yaml.safe_load(pr.yaml_text) or {}).get("tests") or [])
    except Exception:
        n_tests = 0

    return jsonify({
        "yaml": pr.yaml_text,
        "screen": sn,
        "screen_type": entry.get("type"),
        "matched_how": matched_how,
        "candidates": candidates,
        "n_fields": len(entry.get("fields") or []),
        "n_tests": n_tests,
        "prompt": {
            "applied":     pr.applied,
            "ignored":     pr.ignored,
            "llm_used":    pr.llm_used,
            "llm_error":   pr.llm_error,
            "llm_changed": pr.llm_changed,
        },
    })


def _catalog_entry(screenname: str) -> dict | None:
    cat = load_catalog() or {}
    for s in cat.get("screens") or []:
        if (s.get("screenname") or "").lower() == (screenname or "").lower():
            return s
    return None


@app.route("/api/modify-testcases", methods=["POST"])
def api_modify_testcases():
    """Apply a plain-English edit to a STORED (or supplied) set of test cases.

    This is the day-2 flow: a tester recalls test cases saved earlier and tweaks
    them — e.g. "search by first name instead of last name", "search for Smith",
    "also screenshot after each click".

    POST JSON (one source required):
      { scenario_id, prompt, use_llm? }     — edit a saved scenario, or
      { yaml, screen, prompt, use_llm? }    — edit YAML you pass in

    Returns { yaml, screen, n_tests, applied, ignored, llm_used, llm_error }.
    """
    import yaml
    from framework import scenario_store
    from framework.testcase_editor import edit_testcases

    data = request.get_json(force=True) or {}
    prompt = (data.get("prompt") or "").strip()
    use_llm = bool(data.get("use_llm"))
    if not prompt:
        return jsonify({"error": "Type what to change — e.g. "
                                 "\"search by first name instead of last name\"."}), 400

    scenario_id = (data.get("scenario_id") or "").strip()
    if scenario_id:
        s = scenario_store.get_scenario(scenario_id)
        if not s:
            return jsonify({"error": f"saved test cases '{scenario_id}' not found"}), 404
        base_yaml = scenario_store.to_yaml_text(s)
        screen = (s.get("screen") if isinstance(s, dict) else getattr(s, "screen", "")) or ""
    else:
        base_yaml = data.get("yaml") or ""
        screen = (data.get("screen") or "").strip()
        if not base_yaml.strip():
            return jsonify({"error": "provide a scenario_id or yaml to edit"}), 400

    # If we don't have an explicit screen, infer it from the first test block.
    if not screen:
        try:
            doc = yaml.safe_load(base_yaml) or {}
            screen = ((doc.get("tests") or [{}])[0] or {}).get("screen") or ""
        except Exception:
            screen = ""

    entry = _catalog_entry(screen) if screen else None
    res = edit_testcases(base_yaml, entry, prompt, use_llm=use_llm)

    try:
        n_tests = len((yaml.safe_load(res.yaml_text) or {}).get("tests") or [])
    except Exception:
        n_tests = 0

    return jsonify({
        "yaml":      res.yaml_text,
        "screen":    screen,
        "scenario_id": scenario_id or None,
        "n_tests":   n_tests,
        "applied":   res.applied,
        "ignored":   res.ignored,
        "llm_used":  res.llm_used,
        "llm_error": res.llm_error,
        "catalog_known": entry is not None,
    })


from framework import step_kinds as _step_kinds
from framework import screen_resolver as _screen_resolver
from framework import line_kinds as _line_kinds


def _catalog_screens() -> list:
    """Catalog as a flat list, loaded once per process."""
    global _CAT_LIST
    try:
        return _CAT_LIST
    except NameError:
        pass
    from framework.catalog_builder import load_catalog
    d = load_catalog() or {}
    scr = d.get("screens") if isinstance(d, dict) else d
    if isinstance(scr, dict):
        scr = list(scr.values())
    _CAT_LIST = scr or []
    return _CAT_LIST


def _classify_lines(lines: list, steps: list) -> list:
    """One kind per line, plus grouping of consecutive same-kind questions.

    Three identical questions stacked is the alert-fatigue failure with better
    wording, so consecutive bullet questions collapse into one card that can be
    answered in a single click.
    """
    # Count only steps that survive into the test. A line whose entire output was
    # notes has produced nothing runnable, and calling it an "action" line left
    # the tester's sentence sitting there with nothing underneath and no
    # explanation — silence that reads as a glitch rather than as "all fine".
    # Only a RESOLVED step counts. A line whose sole output is an unresolved
    # placeholder had been treated as an action line, so it rendered as a step
    # row reading "? <your own sentence>" — the tool echoing the question back
    # instead of asking it. Such a line is a question, and belongs in a card
    # that says what it needs.
    used = {s.get("src") for s in steps
            if s.get("src", -1) >= 0 and s.get("action") != "todo"}
    out = []
    for n, t in enumerate(lines):
        kind, qtype = _line_kinds.classify_line(t, n in used)
        out.append({"i": n, "text": t, "kind": kind, "qtype": qtype,
                    "group": [], "answer": None})

    # Fold runs of the same bullet question into the first of the run.
    i = 0
    while i < len(out):
        if out[i]["qtype"] == _line_kinds.Q_BULLET:
            j = i + 1
            while j < len(out) and out[j]["qtype"] == _line_kinds.Q_BULLET:
                out[i]["group"].append(out[j]["i"])
                out[j]["kind"] = "grouped"
                out[j]["qtype"] = None
                j += 1
            i = j
        else:
            i += 1
    return out


def _structure(res, screen):
    """Reshape an ImportResult into the editor's JSON.

    Every step carries `ready`, which is simply "the importer produced a real
    action, not a todo". That single flag is what the UI colours on, and what
    the progress count is built from, so a tester can see at a glance how much
    of the sheet still needs a human."""
    scenarios, n_ready, n_steps = [], 0, 0
    for i, tc in enumerate(res.cases or []):
        # Legacy sheets carry section-header rows ("Backoffice", "Security")
        # that parse into a named case with no steps. They are labels, not
        # tests, and an empty card in the editor is pure noise.
        if not (tc.steps or []):
            continue
        name = (tc.name or "").strip() or f"Scenario {i + 1}"
        # Legacy sheets encode the section in the name as "Group>>Case".
        group, _, short = name.partition(">>")
        if not short:
            group, short = "", name
        steps = []
        for j, st in enumerate(tc.steps or []):
            action = (st.get("action") or "todo").lower()
            ready = action != "todo"
            n_ready += 1 if ready else 0
            n_steps += 1
            target = str(st.get("target") or "")
            steps.append({
                "id":     f"s{i}_{j}",
                "action": action,
                "target": target,
                "value":  str(st.get("value") or ""),
                "ready":  ready,
                # Which prose line produced this step, and whether the tester
                # wrote it or the tool added it. The editor groups by the first
                # and labels the second.
                "src":    st.get("_src", -1),
                "origin": st.get("_origin", "sheet"),
                # Only untranslated rows carry a kind — it explains WHY a row is
                # not automated, which is the difference between "you have 94
                # steps to write" and "you have 37, the rest were never steps".
                "kind":   (None if ready else _step_kinds.classify(target)),
            })
        # The sheet names its own destination ("Path: A > B > C"), so resolve it
        # rather than forcing every scenario onto whatever the tester typed.
        sc_screen, conf = _screen_resolver.resolve(steps, _catalog_screens(), screen)
        scenarios.append({
            "id":         f"sc{i}",
            "name":       short.strip(),
            "group":      group.strip(),
            "screen":     sc_screen,
            "screen_conf": conf,
            "expected":   (tc.expected or "").strip(),
            # The raw spreadsheet prose for this scenario. The editor shows it
            # beside the converted steps so a tester can see what their sheet
            # said and what the tool made of it, without opening Excel.
            "original":   (getattr(tc, "notes", "") or "").strip(),
            # The tester's own sentences, in order. A line with no steps is the
            # failure the old two-column layout could not show.
            "lines":      _classify_lines(getattr(tc, "lines", []) or [], steps),
            "steps":      steps,
        })
    kinds = [t["kind"] for s in scenarios for t in s["steps"] if t.get("kind")]
    n_lines = sum(len(s["lines"]) for s in scenarios)
    n_covered = 0
    for s in scenarios:
        used = {t["src"] for t in s["steps"] if t.get("src", -1) >= 0}
        n_covered += sum(1 for ln in s["lines"] if ln["i"] in used)
    return {
        "scenarios": scenarios,
        "summary": {
            "scenarios": len(scenarios),
            "steps":     n_steps,
            "ready":     n_ready,
            "needs":     n_steps - n_ready,
            "pct":       round(100 * n_ready / n_steps) if n_steps else 0,
            "layout":    res.layout,
            "kinds":     _step_kinds.summarise(kinds),
            "lines":     n_lines,
            "lines_covered": n_covered,
            "questions": sum(1 for s in scenarios for ln in s["lines"]
                             if ln["kind"] == "question"),
            "notes_kept": sum(1 for s in scenarios for ln in s["lines"]
                              if ln["kind"] in ("note", "heading", "setup")),
        },
    }



def _asset_v(filename: str) -> str:
    """Cache-buster from the file's mtime.

    Static assets are served with far-future caching, so an edited convert.js
    kept loading from cache and the page ran yesterday's code — which looks
    exactly like a bug in today's. Stamping the mtime makes a changed file a
    changed URL.
    """
    try:
        return str(int(os.path.getmtime(
            os.path.join(app.static_folder, filename))))
    except OSError:
        return "0"


@app.context_processor
def _inject_asset_v():
    return {"asset_v": _asset_v}


@app.route("/api/yaml-to-structured", methods=["POST"])
def api_yaml_to_structured():
    """Turn a YAML test plan into the editor's scenario/step JSON.

    The Convert screen also accepts pasted or described tests, which never pass
    through the Excel importer. Going through YAML keeps all three input methods
    on one code path into the same editor.

    POST JSON: {"yaml": "...", "screen": "..."}
    """
    import yaml as _yaml
    body = request.get_json(silent=True) or {}
    text = body.get("yaml") or ""
    screen = (body.get("screen") or "yourscreen").strip()
    try:
        doc = _yaml.safe_load(text) or {}
    except Exception as e:
        return jsonify({"error": f"could not read the test file: {e}"}), 400

    class _TC:
        __slots__ = ("name", "steps", "expected", "notes")

    cases = []
    for t in (doc.get("tests") or []):
        c = _TC()
        c.name = str(t.get("name") or "")
        c.steps = list(t.get("steps") or [])
        c.expected = ""
        c.notes = ""
        cases.append(c)

    class _Res:
        pass
    res = _Res()
    res.cases = cases
    res.layout = "yaml"
    return jsonify(_structure(res, screen))


@app.route("/api/classify-steps", methods=["POST"])
def api_classify_steps():
    """Label a list of untranslated step texts by kind.

    The Convert screen parses YAML client-side and cannot run the Python
    classifier, so it asks here. Without this it raises a question for every
    todo — including database setup, permission lists and business rules —
    which is what turned 39 real questions into 212.

    POST JSON: {"texts": ["...", ...]}
    -> {"kinds": ["step"|"setup_db"|"setup_sec"|"rule", ...], "counts": {...}}
    """
    body = request.get_json(silent=True) or {}
    texts = body.get("texts") or []
    if not isinstance(texts, list):
        return jsonify({"error": "texts must be a list"}), 400
    kinds = [_step_kinds.classify(str(t or "")) for t in texts[:5000]]
    return jsonify({"kinds": kinds, "counts": _step_kinds.summarise(kinds)})


@app.route("/api/sheets", methods=["POST"])
def api_sheets():
    """List the workbook's sheets so the tester picks one.

    Reading whichever sheet Excel left active silently imported the wrong tab.
    POST multipart: file = <xlsx>
    """
    from framework.testcase_importer import list_sheets
    if "file" not in request.files:
        return jsonify({"error": "no file uploaded"}), 400
    try:
        return jsonify({"sheets": list_sheets(request.files["file"].read())})
    except Exception as e:
        return jsonify({"error": f"could not read that file: {e}"}), 500


@app.route("/api/import-structured", methods=["POST"])
def api_import_structured():
    """Same import as /api/import-testcases, but returns scenarios and steps as
    editable JSON instead of a YAML blob.

    POST multipart form:  file = <xlsx>, screen = <screenname>
    """
    from framework.testcase_importer import import_xlsx
    if "file" not in request.files:
        return jsonify({"error": "no file uploaded"}), 400
    screen = (request.form.get("screen") or "yourscreen").strip()
    sheet  = (request.form.get("sheet") or "").strip() or None
    use_ai = (request.form.get("use_ai") or "").strip() in ("1", "true", "yes", "on")
    try:
        res = import_xlsx(request.files["file"].read(), screen=screen, sheet=sheet)
    except Exception as e:
        return jsonify({"error": f"import failed: {e}"}), 500

    out = _structure(res, screen)
    out["ai"] = {"available": _ai_available(), "used": False, "filled": 0}
    if use_ai and _ai_available():
        out = _ai_fill(out, screen)
    return jsonify(out)


def _ai_available() -> bool:
    from framework import ai_steps
    return ai_steps.available()


def _ai_fill(out: dict, screen: str) -> dict:
    """Second tier: send only the steps the rules could not translate.

    The rules already did the cheap, certain work; this pays for the rest.
    Anything the model declines to convert stays a todo, so the tester is never
    handed a confident wrong click in place of an honest blank."""
    from framework import ai_steps
    # Group by resolved screen: a batch is only as good as the field list it is
    # grounded in, so scenarios on different screens must not share a prompt.
    groups: dict = {}
    for sc in out["scenarios"]:
        for st in sc["steps"]:
            # Setup/permission/rule rows are not UI actions — spending tokens
            # asking the model to convert them invites exactly the fabricated
            # clicks the classifier exists to prevent.
            if st["action"] == "todo" and st["target"].strip() and \
               (st.get("kind") or "step") == "step":
                groups.setdefault(sc.get("screen") or screen, []).append(st)
    if not groups:
        return out
    total, filled_n, errors = 0, 0, []
    for scr_name, items in groups.items():
        total += len(items)
        try:
            filled = ai_steps.translate_todos(
                [s["target"] for s in items], scr_name, _catalog_entry(scr_name),
                errors=errors)
        except Exception as e:
            # Record it — a silently swallowed failure here looks identical to
            # "the model declined everything", which hid a real bug once.
            errors.append(f"{scr_name}: {str(e)[:120]}")
            continue
        for i, st in enumerate(items):
            got = filled.get(i)
            if not got:
                continue
            st["action"] = got["action"]
            st["target"] = got["target"] or st["target"]
            st["value"]  = got["value"]
            st["ready"]  = True
            st["by_ai"]  = True
            filled_n += 1
    for sc in out["scenarios"]:
        for t in sc["steps"]:
            t["kind"] = None if t["action"] != "todo" else _step_kinds.classify(t["target"])
    steps = sum(len(s["steps"]) for s in out["scenarios"])
    ready = sum(1 for s in out["scenarios"] for t in s["steps"] if t["action"] != "todo")
    kinds = [t["kind"] for s in out["scenarios"] for t in s["steps"] if t.get("kind")]
    out["summary"].update({"steps": steps, "ready": ready, "needs": steps - ready,
                           "pct": round(100 * ready / steps) if steps else 0,
                           "kinds": _step_kinds.summarise(kinds)})
    out["ai"] = {"available": True, "used": True, "filled": filled_n,
                 "considered": total, "screens": len(groups)}
    if errors:
        out["ai"]["errors"] = errors[:5]
    return out


@app.route("/api/structured-to-yaml", methods=["POST"])
def api_structured_to_yaml():
    """Turn the editor's edited scenarios back into runnable YAML.

    Steps still marked `todo` are kept rather than dropped: they run as
    soft-skips and stay visible in the log, so nothing a tester has not yet
    filled in silently disappears from the suite.
    """
    import yaml as _yaml
    body = request.get_json(silent=True) or {}
    tests = []
    for sc in body.get("scenarios") or []:
        steps = []
        for st in sc.get("steps") or []:
            action = (st.get("action") or "todo").lower()
            out = {"action": action, "target": st.get("target") or ""}
            if action in ("fill", "select") or (st.get("value") or ""):
                out["value"] = st.get("value") or ""
            steps.append(out)
        if not steps:
            continue
        tests.append({
            "screen": sc.get("screen") or "yourscreen",
            "name":   sc.get("name") or "Untitled scenario",
            "steps":  steps,
        })
    text = _yaml.safe_dump({"tests": tests}, sort_keys=False,
                           default_flow_style=False, allow_unicode=True, width=100)
    return jsonify({"yaml": text, "n_tests": len(tests)})


@app.route("/api/import-testcases", methods=["POST"])
def api_import_testcases():
    """Accept an uploaded .xlsx of test cases and return YAML.

    POST multipart form:
      file   = <xlsx>
      screen = <screenname>     (default screen for legacy prose import)
      prompt = <free-text>      (optional — applied via prompt engine)
      use_llm = "1"/"0"         (opt-in Gemini refinement; requires API key)

    Returns JSON: { yaml, layout, n_tests, n_steps_total, n_steps_translated,
                    pct_translated, per_screen?, screens?, prompt? }
    """
    from framework.testcase_importer import import_xlsx
    from framework.prompt_engine import apply_prompt
    if "file" not in request.files:
        return jsonify({"error": "no file uploaded"}), 400
    f = request.files["file"]
    screen = (request.form.get("screen") or "yourscreen").strip()
    prompt = (request.form.get("prompt") or "").strip()
    use_llm = (request.form.get("use_llm") or "").strip() in ("1", "true", "yes")
    try:
        res = import_xlsx(f.read(), screen=screen)
    except Exception as e:
        return jsonify({"error": f"import failed: {e}"}), 500

    prompt_summary = None
    yaml_out = res.yaml_text
    if prompt:
        pr = apply_prompt(yaml_out, prompt, use_llm=use_llm)
        yaml_out = pr.yaml_text
        prompt_summary = {
            "applied":     pr.applied,
            "ignored":     pr.ignored,
            "llm_used":    pr.llm_used,
            "llm_error":   pr.llm_error,
            "llm_changed": pr.llm_changed,
        }

    return jsonify({
        "yaml": yaml_out,
        "layout": res.layout,
        "n_tests": res.n_tests,
        "n_steps_total": res.n_steps_total,
        "n_steps_translated": res.n_steps_translated,
        "pct_translated": (100 * res.n_steps_translated // res.n_steps_total) if res.n_steps_total else 0,
        "per_screen": res.per_screen,
        "screens": res.screens,
        "prompt": prompt_summary,
    })


@app.route("/api/llm-status")
def api_llm_status():
    """Tell the UI whether Gemini is configured + today's usage."""
    from framework.prompt_engine import llm_available
    return jsonify(llm_available())


@app.route("/api/import-testcases/zip", methods=["POST"])
def api_import_testcases_zip():
    """Same import, but returns a ZIP of one YAML file per screen.

    Useful when a single template covers many screens — the QA gets back
    `<screen>_tests.yaml` for each row's Screen column."""
    from framework.testcase_importer import import_xlsx
    import io as _io, zipfile
    if "file" not in request.files:
        return jsonify({"error": "no file uploaded"}), 400
    from framework.prompt_engine import apply_prompt
    try:
        res = import_xlsx(request.files["file"].read(),
                          screen=(request.form.get("screen") or "yourscreen").strip())
    except Exception as e:
        return jsonify({"error": f"import failed: {e}"}), 500

    prompt = (request.form.get("prompt") or "").strip()
    use_llm = (request.form.get("use_llm") or "").strip() in ("1", "true", "yes")

    buf = _io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if res.per_screen:
            for scr, yml in res.per_screen.items():
                if prompt:
                    yml = apply_prompt(yml, prompt, use_llm=use_llm).yaml_text
                zf.writestr(f"{scr}_tests.yaml", yml)
        else:
            yml = res.yaml_text
            if prompt:
                yml = apply_prompt(yml, prompt, use_llm=use_llm).yaml_text
            zf.writestr("imported_tests.yaml", yml)
        # Index file so the QA knows what's in the zip
        idx = "Stratus QA — imported test cases\n\n"
        idx += f"Layout    : {res.layout}\n"
        idx += f"Tests     : {res.n_tests}\n"
        idx += f"Steps     : {res.n_steps_total}\n"
        if res.screens:
            idx += f"Screens   : {len(res.screens)}\n"
            for s in res.screens: idx += f"  • {s}\n"
        zf.writestr("README.txt", idx)
    buf.seek(0)
    return Response(
        buf.getvalue(), mimetype="application/zip",
        headers={"Content-Disposition": 'attachment; filename="testcases.zip"'},
    )


@app.route("/api/template/xlsx")
def api_template_xlsx():
    """Download the official Stratus QA Test-Case template (.xlsx)."""
    p = Path(__file__).resolve().parent.parent / "docs" / "Stratus-QA-TestCase-Template.xlsx"
    if not p.exists():
        # Lazily generate it on first hit (so a fresh checkout still works)
        from framework.template_builder import build_template
        build_template(p.parent)
    return send_file(str(p), as_attachment=True,
                     download_name="Stratus-QA-TestCase-Template.xlsx")


@app.route("/api/template/csv")
def api_template_csv():
    """Download the CSV form of the template — for Google Sheets import."""
    p = Path(__file__).resolve().parent.parent / "docs" / "Stratus-QA-TestCase-Template.csv"
    if not p.exists():
        from framework.template_builder import build_template
        build_template(p.parent)
    return send_file(str(p), as_attachment=True,
                     download_name="Stratus-QA-TestCase-Template.csv")


def _user_can_see(scenario: dict) -> bool:
    """Per-user visibility: admins see all, regular users see their own
    + any scenario with no owner (legacy / shared)."""
    me = _auth.current_user()
    role = _auth.current_role()
    if not me: return False
    if role == "admin": return True
    owner = (scenario or {}).get("author") or ""
    return owner == me or owner == ""    # blank owner = shared / legacy


@app.route("/api/scenarios")
@_auth.login_required
def api_scenarios_list():
    """List the current user's scenarios (admins see all). Newest first."""
    from framework import scenario_store
    all_scn = scenario_store.list_scenarios()
    visible = [s for s in all_scn if _user_can_see(s)]
    return jsonify({
        "scenarios": visible,
        "directory": scenario_store.load_directory(),
        "total":   len(all_scn),
        "visible": len(visible),
    })


@app.route("/api/scenarios", methods=["POST"])
@_auth.login_required
def api_scenarios_create():
    """Save a new scenario. POST JSON body:
       { id, title, description?, screen?, tags?, variables?, steps OR yaml,
         overwrite? }
    Owner is forced to the logged-in user (you can't impersonate)."""
    from framework import scenario_store
    data = request.get_json(force=True) or {}
    overwrite = bool(data.pop("overwrite", False))
    yaml_text = data.pop("yaml", None)
    me = _auth.current_user() or ""
    if yaml_text and not data.get("steps"):
        s = scenario_store.from_yaml_text(
            scenario_id=data.get("id", ""),
            title=data.get("title", ""),
            yaml_text=yaml_text,
            description=data.get("description", ""),
            tags=data.get("tags") or [],
            author=me,
        )
        s.screen = data.get("screen") or s.screen
        s.variables = data.get("variables") or {}
    else:
        try:
            data["author"] = me
            s = scenario_store.Scenario.from_dict(data)
        except Exception as e:
            return jsonify({"error": f"bad scenario payload: {e}"}), 400
    # Check overwrite: only the original owner (or admin) may overwrite
    if overwrite:
        existing = scenario_store.get_scenario(s.id)
        if existing and existing.get("author") and existing["author"] != me \
                and _auth.current_role() != "admin":
            return jsonify({"error": "this scenario belongs to another user; you can't overwrite it"}), 403
    try:
        saved = scenario_store.save_scenario(s, overwrite=overwrite)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True, "scenario": scenario_store.get_scenario(saved.id)})


@app.route("/api/scenarios/<scenario_id>")
@_auth.login_required
def api_scenarios_get(scenario_id: str):
    from framework import scenario_store
    s = scenario_store.get_scenario(scenario_id)
    if not s: return jsonify({"error": "not found"}), 404
    if not _user_can_see(s): return jsonify({"error": "not found"}), 404
    return jsonify(s)


@app.route("/api/scenarios/<scenario_id>", methods=["DELETE"])
@_auth.login_required
def api_scenarios_delete(scenario_id: str):
    from framework import scenario_store
    s = scenario_store.get_scenario(scenario_id)
    if not s: return jsonify({"ok": False})
    if not _user_can_see(s): return jsonify({"error": "not found"}), 404
    ok = scenario_store.delete_scenario(scenario_id)
    return jsonify({"ok": ok})


@app.route("/api/scenarios/<scenario_id>/yaml")
@_auth.login_required
def api_scenarios_yaml(scenario_id: str):
    """Download a scenario as runnable YAML. Optional ?vars=key=val,key=val."""
    from framework import scenario_store
    s = scenario_store.get_scenario(scenario_id)
    if not s: return jsonify({"error": "not found"}), 404
    if not _user_can_see(s): return jsonify({"error": "not found"}), 404
    overrides = {}
    raw = request.args.get("vars") or ""
    for chunk in raw.split(","):
        if "=" in chunk:
            k, v = chunk.split("=", 1)
            overrides[k.strip()] = v.strip()
    yml = scenario_store.to_yaml_text(s, overrides or None)
    fname = f"{scenario_id}.yaml"
    return Response(yml, mimetype="application/x-yaml",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@app.route("/api/intent-parse", methods=["POST"])
@_auth.login_required
def api_intent_parse():
    """Parse a natural-language request → structured launch plan.
    POST JSON: { prompt, use_llm? }
    Returns: { scenario_id, user_alias, env_alias, overrides, confidence,
               notes, llm_used, llm_error, runnable, yaml_preview? }
    """
    from framework.intent_parser import parse_intent
    from framework import scenario_store
    data = request.get_json(force=True) or {}
    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"error": "prompt is required"}), 400
    use_llm = bool(data.get("use_llm", True))
    intent = parse_intent(prompt, use_llm=use_llm)
    out = {
        "scenario_id": intent.scenario_id,
        "user_alias":  intent.user_alias,
        "env_alias":   intent.env_alias,
        "overrides":   intent.overrides,
        "confidence":  intent.confidence,
        "notes":       intent.notes,
        "llm_used":    intent.llm_used,
        "llm_error":   intent.llm_error,
        "runnable":    intent.is_runnable(),
    }
    if intent.scenario_id:
        s = scenario_store.get_scenario(intent.scenario_id)
        if s:
            out["scenario_title"] = s.get("title")
            out["yaml_preview"] = scenario_store.to_yaml_text(s, intent.overrides)
    return jsonify(out)


@app.route("/api/scenarios/<scenario_id>/run", methods=["POST"])
@_auth.login_required
def api_scenarios_run(scenario_id: str):
    """Launch a saved scenario through the existing single-screen runner.

    POST JSON: { user_alias?, env_alias?, overrides?, url?, user?, password? }
    Anything explicit in the body wins over the directory lookup.
    """
    global CURRENT
    from framework import scenario_store
    s = scenario_store.get_scenario(scenario_id)
    if not s:
        return jsonify({"error": "scenario not found"}), 404
    data = request.get_json(force=True) or {}

    directory = scenario_store.load_directory()
    user_alias = data.get("user_alias")
    env_alias  = data.get("env_alias")

    # Resolve env → url + machine_id
    env_cfg = (directory.get("envs") or {}).get(env_alias) or {}
    url = data.get("url") or env_cfg.get("url") or ""
    machine_id = data.get("machine_id") or env_cfg.get("machine_id") or ""

    # Resolve user → username + password (passwords are never stored on disk,
    # so the caller must supply it for "real" runs)
    user_cfg = (directory.get("users") or {}).get(user_alias) or {}
    username = data.get("user") or user_cfg.get("username") or ""
    password = data.get("password") or user_cfg.get("password") or ""

    if not (url and username and password):
        return jsonify({"error": "url, user, and password are required "
                                  "(set them in env-directory or pass explicitly)"}), 400

    # Build a YAML doc with overrides applied, hand to the existing
    # single-screen runner via the custom_tests_yaml input.
    overrides = data.get("overrides") or {}
    yaml_text = scenario_store.to_yaml_text(s, overrides)

    cfg = DemoConfig(
        base_url=url, user=username, password=password,
        screen=s.get("screen") or "customer",
        machine_id=machine_id,
        headless=True, slow_mo_ms=0, read_only=False, diagnose=False,
        capture_step_screenshots=True, capture_html=True,
        reports_dir=REPORTS_DIR,
    )

    with LOCK:
        if CURRENT.running:
            return jsonify({"error": "A run is already in progress"}), 409
        CURRENT = RunState()
        CURRENT.id = uuid4().hex[:8]
        CURRENT.running = True
        CURRENT.started_at = time.time()
        CURRENT.config_snapshot = {
            "url": url, "user": username, "screen": s.get("screen"),
            "machine_id": machine_id, "scenario_id": scenario_id,
            "scenario_title": s.get("title"),
            "user_alias": user_alias, "env_alias": env_alias,
            "single_mode": True, "single_screenname": s.get("screen"),
            "from_scenario": True,
        }

    # Record the run on the scenario (for "last run" + "run count" badges)
    scenario_store.record_run(scenario_id)

    def _worker(c, state, sn, custom_yaml):
        def on_event(evt: StepEvent) -> None:
            try:
                if evt.type != "__end__":
                    state.event_log.append({
                        "type": evt.type, "text": evt.text, "step": evt.step,
                        "screenshot": _screenshot_url(evt.screenshot_path) if evt.screenshot_path else None,
                        "html":       _html_url(evt.html_path) if evt.html_path else None,
                        "console":    evt.console_tail,
                    })
            except Exception: pass
            state.queue.put(evt)
        try:
            res = run_single_screen(c, sn, on_event, safe_mode=True,
                                    custom_tests_yaml=custom_yaml)
        except Exception as e:
            state.queue.put(StepEvent(type="fail", text=f"runtime: {e}"))
            res = DemoResult(passed=False, steps_total=0, steps_passed=0,
                             steps_failed=1, duration_s=0,
                             failures=[("runtime", str(e))])
        state.result = res
        state.running = False
        try:
            from framework.demo_runner import _Tracker as _Trk
            state.network_log = list(getattr(_Trk, "_last_network_log", []) or [])
            state.console_log = list(getattr(_Trk, "_last_console_log", []) or [])
        except Exception:
            state.network_log = []; state.console_log = []
        state.queue.put(StepEvent(type="__end__"))
        try:
            snap = dict(state.config_snapshot or {})
            snap["started_at"] = state.started_at
            snap["run_id"] = state.id
            build_run_report(out_path=REPORTS_DIR / "last_run.html",
                             config=snap, result=asdict(res),
                             events=state.event_log,
                             network=getattr(state, "network_log", []),
                             console=getattr(state, "console_log", []))
        except Exception as e:
            print(f"  [warn] report build failed: {e}")
        _append_history({
            "id": state.id,
            "ts": datetime.now().isoformat(timespec="seconds"),
            "config": state.config_snapshot,
            "passed": res.passed,
            "steps_total": res.steps_total,
            "steps_passed": res.steps_passed,
            "steps_failed": res.steps_failed,
            "duration_s": res.duration_s,
            "failures": res.failures,
        })

    threading.Thread(target=_worker,
                     args=(cfg, CURRENT, s.get("screen"), yaml_text),
                     daemon=True).start()
    return jsonify({"run_id": CURRENT.id, "scenario_id": scenario_id})


@app.route("/api/catalog/<screenname>/yaml")
def api_catalog_yaml(screenname: str):
    """Download the auto-generated tests for ONE screen as an editable YAML
    file. ?safe=0 includes destructive (Save/Delete) steps."""
    safe = request.args.get("safe", "1") not in ("0", "false", "no")
    cat = load_catalog() or {}
    for s in cat.get("screens") or []:
        if s.get("screenname", "").lower() == screenname.lower():
            text = _yaml_for_screen(s, safe_mode=safe)
            fname = f"{s.get('screenname','screen')}_tests.yaml"
            return Response(
                text,
                mimetype="application/x-yaml",
                headers={"Content-Disposition": f'attachment; filename="{fname}"'},
            )
    return jsonify({"error": f"screen {screenname!r} not in catalog"}), 404


@app.route("/html-snapshots/<path:filename>")
def html_snapshot(filename: str):
    return send_from_directory(REPORTS_DIR / "html", filename)


@app.route("/report.html")
def report_download():
    """Serves the LATEST web-UI run report. Falls back to pytest's
    report.html if no web-UI run has happened yet."""
    last = REPORTS_DIR / "last_run.html"
    if last.exists():
        return send_file(last, as_attachment=False)
    p = REPORTS_DIR / "report.html"
    if p.exists():
        return send_file(p, as_attachment=False)
    return "No report yet — run a test first.", 404


# ---------------------------------------------------------- helpers

def _screenshot_url(abs_path: str) -> str:
    return f"/screenshots/{Path(abs_path).name}"


def _html_url(abs_path: str) -> str:
    return f"/html-snapshots/{Path(abs_path).name}"


# ============================================================ main

if __name__ == "__main__":
    # Suppress noisy SSL warnings from requests (we test self-signed by design)
    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except Exception:
        pass

    print("\n" + "=" * 60)
    print("  Stratus QA Tool — Web Console v0.2")
    print("=" * 60)
    print("  Open http://localhost:5050 in your browser.")
    print("  Press Ctrl+C to stop.\n")
    # Port is configurable because 5050 collides with Docker on some machines.
    # Docker binds it on IPv6 while this binds IPv4, so "localhost" silently
    # reaches Docker and every request 404s while the tool looks fine on
    # 127.0.0.1 — a confusing failure worth being able to sidestep.
    port = int(os.environ.get("QA_PORT") or 5055)
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
