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
import queue
import socket
import sys
import threading
import time
from dataclasses import asdict
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
    request, send_file, send_from_directory,
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


# ---------------------------------------------------------- run

@app.route("/api/run", methods=["POST"])
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
def api_history():
    return jsonify(_read_json(HISTORY_FILE, [])[-25:][::-1])


def _append_history(entry: dict) -> None:
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


@app.route("/api/import-testcases", methods=["POST"])
def api_import_testcases():
    """Accept an uploaded .xlsx of test cases and return YAML.

    POST multipart form:  file=<xlsx>,  screen=<screenname>
    Returns JSON: { yaml, layout, n_tests, n_steps_total, n_steps_translated,
                    pct_translated, per_screen?, screens? }

    For template-format files, `per_screen` is a {screen: yaml} dict and
    `screens` lists every screen covered. The user can download one combined
    YAML or fetch a ZIP of per-screen YAMLs via /api/import-testcases/zip.
    """
    from framework.testcase_importer import import_xlsx
    if "file" not in request.files:
        return jsonify({"error": "no file uploaded"}), 400
    f = request.files["file"]
    screen = (request.form.get("screen") or "yourscreen").strip()
    try:
        res = import_xlsx(f.read(), screen=screen)
    except Exception as e:
        return jsonify({"error": f"import failed: {e}"}), 500
    return jsonify({
        "yaml": res.yaml_text,
        "layout": res.layout,
        "n_tests": res.n_tests,
        "n_steps_total": res.n_steps_total,
        "n_steps_translated": res.n_steps_translated,
        "pct_translated": (100 * res.n_steps_translated // res.n_steps_total) if res.n_steps_total else 0,
        "per_screen": res.per_screen,
        "screens": res.screens,
    })


@app.route("/api/import-testcases/zip", methods=["POST"])
def api_import_testcases_zip():
    """Same import, but returns a ZIP of one YAML file per screen.

    Useful when a single template covers many screens — the QA gets back
    `<screen>_tests.yaml` for each row's Screen column."""
    from framework.testcase_importer import import_xlsx
    import io as _io, zipfile
    if "file" not in request.files:
        return jsonify({"error": "no file uploaded"}), 400
    try:
        res = import_xlsx(request.files["file"].read(),
                          screen=(request.form.get("screen") or "yourscreen").strip())
    except Exception as e:
        return jsonify({"error": f"import failed: {e}"}), 500

    buf = _io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if res.per_screen:
            for scr, yml in res.per_screen.items():
                zf.writestr(f"{scr}_tests.yaml", yml)
        else:
            zf.writestr("imported_tests.yaml", res.yaml_text)
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
    app.run(host="0.0.0.0", port=5050, debug=False, threaded=True)
