"""User-friendly + technical HTML report for one Web-UI run.

What's in it (top to bottom):
  1. Hero verdict bar (PASS/FAIL/PARTIAL) with quick stats
  2. Sticky table of contents (jump to failures / network / events / etc.)
  3. Run summary card — URL, mode, user, timing
  4. Failures section (collapsed-expandable, with full text + links to events)
  5. Step-by-step timeline (grouped by section, sortable + filterable)
  6. Network log panel — every API call: method, URL, payload, status, body
  7. Screenshot gallery — full-resolution, captioned, clickable
  8. Browser console messages (errors highlighted)
  9. Raw event log (developer detail)

Self-contained: no JS frameworks, no external CSS. One <style> block, plain JS
for collapse/filter. Opens straight from disk.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from html import escape
from pathlib import Path
from urllib.parse import urlparse


_ICON = {
    "banner":     "★", "section": "▶", "info":   "·",  "ok":   "✓",
    "warn":       "!", "fail":    "✗", "screenshot": "📸",
    "done":       "■", "diagnostic": "🔎",
}


def _short(s, n=160):
    if s is None: return ""
    s = str(s)
    return s if len(s) <= n else s[:n - 1] + "…"


def _human_kb(n):
    if not n: return "—"
    if n < 1024: return f"{n} B"
    if n < 1024 * 1024: return f"{n / 1024:.1f} KB"
    return f"{n / 1024 / 1024:.1f} MB"


def _path_only(url):
    try:
        p = urlparse(url)
        path = p.path
        if p.query: path += "?" + p.query[:80]
        return path
    except Exception:
        return url


def _status_class(status):
    if status is None: return "pending"
    if status < 300: return "ok"
    if status < 400: return "redir"
    if status < 500: return "warn"
    return "fail"


def build_run_report(
    out_path: Path,
    config: dict,
    result: dict,
    events: list,
    network: list | None = None,
    console: list | None = None,
) -> Path:
    network = network or []
    console = console or []

    passed_n = int(result.get("steps_passed") or 0)
    failed_n = int(result.get("steps_failed") or 0)
    total = passed_n + failed_n

    if failed_n == 0 and passed_n > 0:
        verdict_text = "PASS"
        verdict_sub = "All steps green"
        verdict_color = "#16a34a"
        verdict_bg = "#e7f7ec"
        verdict_icon = "✓"
    elif passed_n > 0:
        verdict_text = "PARTIAL"
        verdict_sub = f"{passed_n} of {total} passed"
        verdict_color = "#d97706"
        verdict_bg = "#fff4e0"
        verdict_icon = "~"
    else:
        verdict_text = "FAIL"
        verdict_sub = "Test did not complete"
        verdict_color = "#dc2626"
        verdict_bg = "#fdecec"
        verdict_icon = "✗"

    # Mode label
    mode = "Full test (Customer module deep)"
    if   config.get("api_mode"):     mode = "⚙️ API-only (no browser)"
    elif config.get("crawl_mode"):   mode = "🌐 Crawl ALL screens"
    elif config.get("catalog_mode"): mode = "📚 Build catalog"
    elif config.get("single_mode"):  mode = f"🎯 Single Screen — {config.get('single_screenname','?')}"
    elif config.get("bulk_mode"):    mode = "🏭 Bulk Auto-Test"
    elif config.get("read_only"):    mode = "🛡️ Read-only"
    elif config.get("diagnose"):     mode = "🔬 Diagnose"

    # ---------- BUILD STEP CARDS ----------
    # Group events by section. Each section becomes one collapsible card.
    sections = []
    current_section = None
    for e in events:
        etype = e.get("type") or "info"
        if etype == "banner":
            continue
        if etype == "section":
            current_section = {
                "step": e.get("step") or 0,
                "title": e.get("text") or "(unnamed)",
                "events": [],
                "status": "ok",   # may flip to fail
                "screenshots": [],
                "console": e.get("console") or [],
            }
            sections.append(current_section)
            continue
        if current_section is None:
            current_section = {
                "step": 0, "title": "(pre-section events)",
                "events": [], "status": "ok",
                "screenshots": [], "console": [],
            }
            sections.append(current_section)
        current_section["events"].append(e)
        if etype == "fail":
            current_section["status"] = "fail"
        elif etype == "warn" and current_section["status"] != "fail":
            current_section["status"] = "warn"
        if e.get("screenshot"):
            current_section["screenshots"].append({
                "url": e.get("screenshot"), "label": e.get("text") or "",
            })

    # ---------- FAILURES SUMMARY ----------
    failures = result.get("failures") or []
    fail_blocks = []
    for i, (name, err) in enumerate(failures, 1):
        fail_blocks.append(f"""
        <div class="failcard">
          <div class="failhdr">
            <div class="failbadge">FAIL #{i}</div>
            <div class="failname">{escape(str(name))}</div>
          </div>
          <pre class="failtext">{escape(str(err))}</pre>
        </div>""")
    failures_html = "".join(fail_blocks) if fail_blocks else \
        "<p class='muted' style='padding:14px'>No failures. 🎉</p>"

    # ---------- STEP TIMELINE ----------
    step_blocks = []
    for sec in sections:
        sclass = sec["status"]
        sicon = "✓" if sclass == "ok" else "✗" if sclass == "fail" else "!"
        # Inner event lines
        inner = []
        for ev in sec["events"]:
            etype = ev.get("type") or "info"
            icon = _ICON.get(etype, "·")
            txt = escape(ev.get("text") or "")
            inner.append(
                f'<div class="evrow {etype}">'
                f'<span class="evicon {etype}">{icon}</span>'
                f'<span class="evtext">{txt}</span>'
                f'</div>'
            )
        # Screenshots inline
        shots = ""
        if sec["screenshots"]:
            shot_imgs = "".join(
                f'<div class="ishot">'
                f'<a href="{s["url"]}" target="_blank">'
                f'<img src="{s["url"]}" alt="{escape(s["label"])}" loading="lazy">'
                f'</a>'
                f'<div class="ishot-cap">{escape(s["label"])}</div>'
                f'</div>'
                for s in sec["screenshots"]
            )
            shots = f'<div class="ishot-grid">{shot_imgs}</div>'

        step_blocks.append(f"""
        <details class="step {sclass}" {'open' if sclass == 'fail' else ''}>
          <summary>
            <span class="stepicon {sclass}">{sicon}</span>
            <span class="stepnum">Step {sec['step']}</span>
            <span class="steptitle">{escape(sec['title'])}</span>
            <span class="stepmeta">{len(sec['events'])} events
              {'· ' + str(len(sec['screenshots'])) + ' shots' if sec['screenshots'] else ''}
            </span>
          </summary>
          <div class="stepbody">
            {''.join(inner) if inner else '<div class="muted">No events.</div>'}
            {shots}
          </div>
        </details>""")
    steps_html = "".join(step_blocks) if step_blocks else \
        "<p class='muted'>No steps recorded.</p>"

    # ---------- NETWORK LOG ----------
    # Sort: API / business calls first (XHR/fetch + /stratus + errors),
    # then everything else. This keeps the meaningful traffic on top.
    def _is_stratus_api(url: str) -> bool:
        # Match the actual /stratus endpoint (no file extension),
        # NOT files like stratus-model.js or stratus-service.js
        if "/stratus" not in url: return False
        path = _path_only(url).split("?")[0].rstrip("/")
        tail = path.rsplit("/", 1)[-1].lower()
        return tail == "stratus" or tail.startswith("stratus?")

    def _priority(n: dict) -> int:
        url = (n.get("url") or "")
        st  = n.get("status") or 0
        if st >= 400:                          return 0   # errors first
        if _is_stratus_api(url):               return 1   # the JSON API
        if n.get("kind") in ("xhr", "fetch"):  return 2   # other AJAX
        if n.get("kind") == "document":        return 3   # page navigations
        if n.get("kind") == "script":          return 5
        return 4

    network_sorted = sorted(enumerate(network), key=lambda kv: (_priority(kv[1]), kv[0]))
    net_blocks = []
    api_calls_n = sum(1 for n in network if n.get("kind") in ("xhr", "fetch") or _is_stratus_api(n.get("url") or ""))
    err_calls_n = sum(1 for n in network if (n.get("status") or 0) >= 400)
    for _idx, n in network_sorted[:300]:    # cap to first 300 to keep page light
        status = n.get("status")
        kind = n.get("kind") or ""
        method = n.get("method", "?")
        url = n.get("url", "")
        path = _path_only(url)
        size = n.get("resp_size") or 0
        sc = _status_class(status)
        req_body = n.get("req_body")
        resp_preview = n.get("resp_preview")
        # Format req body if it's URL-encoded JSON
        req_pretty = ""
        if req_body:
            try:
                # Try unquoting + JSON parse for /stratus calls
                from urllib.parse import unquote
                if req_body.startswith("json="):
                    decoded = unquote(req_body[5:])
                    parsed = json.loads(decoded)
                    req_pretty = json.dumps(parsed, indent=2)
                else:
                    req_pretty = req_body
            except Exception:
                req_pretty = req_body
        # Format response if JSON
        resp_pretty = ""
        if resp_preview:
            try:
                parsed = json.loads(resp_preview)
                resp_pretty = json.dumps(parsed, indent=2)[:3000]
            except Exception:
                resp_pretty = resp_preview

        net_blocks.append(f"""
        <details class="net {sc}">
          <summary>
            <span class="netmethod {method.lower()}">{method}</span>
            <span class="netstatus {sc}">{status if status is not None else '...'}</span>
            <span class="netkind">{escape(kind)}</span>
            <span class="netpath">{escape(path)}</span>
            <span class="netsize">{_human_kb(size)}</span>
          </summary>
          <div class="netbody">
            <div class="kv"><b>Full URL</b><span class="mono break">{escape(url)}</span></div>
            {f'<div class="kv"><b>Request body</b><pre>{escape(req_pretty)}</pre></div>' if req_pretty else ''}
            {f'<div class="kv"><b>Response preview</b><pre>{escape(resp_pretty)}</pre></div>' if resp_pretty else ''}
          </div>
        </details>""")
    network_html = "".join(net_blocks) if net_blocks else \
        "<p class='muted' style='padding:14px'>No network requests captured for this run mode.</p>"

    # ---------- ALL SCREENSHOTS GALLERY ----------
    all_shots = [e for e in events if e.get("screenshot")]
    if all_shots:
        gallery_html = "".join(
            f'<div class="gshot">'
            f'<a href="{e["screenshot"]}" target="_blank">'
            f'<img src="{e["screenshot"]}" alt="{escape(e.get("text") or "")}" loading="lazy">'
            f'</a>'
            f'<div class="gshot-cap">Step {e.get("step") or "?"} — {escape(e.get("text") or "")}</div>'
            f'</div>'
            for e in all_shots
        )
    else:
        gallery_html = (
            "<div style='padding:18px; color:#475569; font-size:13px'>"
            "<b>No screenshots captured.</b><br>"
            "Screenshots are auto-captured after login, after each screen loads, "
            "and after every test in Single-Screen mode. If this section is empty, "
            "it usually means the run never reached the browser stage "
            "(e.g. API-only mode, or login failed before the first screen). "
            "Try Single-Screen mode for a full gallery."
            "</div>")

    # ---------- BROWSER CONSOLE ----------
    # Two sources merged: (a) per-event console_tail (legacy, captured on
    # failure by the original run_demo), and (b) the top-level `console` list
    # captured continuously by the new _browser() listener.
    merged_console: list = list(console)
    for e in events:
        for c in (e.get("console") or []):
            merged_console.append(c)

    if merged_console:
        # Counts per severity
        sev = {"error": 0, "warning": 0, "pageerror": 0, "log": 0, "info": 0, "debug": 0, "other": 0}
        for c in merged_console:
            t = (c.get("type") or "log").lower()
            sev[t] = sev.get(t, 0) + 1
        summary_bits = []
        if sev.get("error") or sev.get("pageerror"):
            summary_bits.append(f"<b style='color:#dc2626'>{sev.get('error',0) + sev.get('pageerror',0)} errors</b>")
        if sev.get("warning"):
            summary_bits.append(f"<b style='color:#d97706'>{sev['warning']} warnings</b>")
        summary_bits.append(f"{sev.get('log',0) + sev.get('info',0) + sev.get('debug',0)} info/log")
        summary = " · ".join(summary_bits)
        # Render — errors first, then warnings, then everything else
        order = {"pageerror": 0, "error": 1, "warning": 2, "info": 3, "log": 4, "debug": 5}
        sorted_console = sorted(merged_console, key=lambda c: order.get((c.get("type") or "log").lower(), 9))
        rows = []
        for c in sorted_console[:500]:
            t = (c.get("type") or "log").lower()
            loc = c.get("loc") or ""
            rows.append(
                f'<tr class="cmsg {t}">'
                f'<td class="cmtype">{escape(t)}</td>'
                f'<td class="cmtxt">{escape(_short(c.get("text",""), 600))}</td>'
                f'<td class="cmloc"><span class="mono" style="font-size:11px; color:#64748b">{escape(_short(loc, 90))}</span></td>'
                f'</tr>')
        console_html = (
            f"<div style='padding:8px 14px; font-size:13px; color:#475569'>"
            f"{len(merged_console)} message{'s' if len(merged_console) != 1 else ''} captured · {summary}"
            f"{' · showing first 500' if len(merged_console) > 500 else ''}"
            f"</div>"
            f"<table class='ctbl'><thead>"
            f"<tr><th style='width:90px'>Type</th><th>Message</th><th style='width:280px'>Source</th></tr>"
            f"</thead><tbody>{''.join(rows)}</tbody></table>")
    else:
        console_html = (
            "<div style='padding:18px; color:#475569; font-size:13px'>"
            "<b>No browser console messages.</b><br>"
            "The tool listens to every <code>console.log/info/warn/error</code> "
            "from Stratus and every uncaught JavaScript error. "
            "An empty list usually means: this was an API-only run "
            "(no browser), or Stratus loaded without any console output "
            "(rare — most apps log something)."
            "</div>")

    # ---------- RAW EVENTS ----------
    raw_rows = []
    for e in events[:1000]:
        et = e.get("type") or "info"
        raw_rows.append(
            f'<tr class="ev-{et}">'
            f'<td class="evt">{escape(et)}</td>'
            f'<td>{e.get("step") or ""}</td>'
            f'<td>{escape(e.get("text") or "")}</td>'
            f'</tr>'
        )
    raw_html = (
        f'<table class="rawtbl"><thead><tr><th>type</th><th>step</th><th>text</th></tr></thead>'
        f'<tbody>{"".join(raw_rows)}</tbody></table>'
    )

    # Datetime
    started_at = ""
    try:
        ts = config.get("started_at") or 0
        if ts: started_at = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except Exception: pass
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ---------- HTML ----------
    html = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<title>Stratus QA Run Report — {escape(verdict_text)}</title>
<style>
  :root {{
    --brand: #1f4e79; --brand2: #2e75b6; --bg: #f5f6f8;
    --card: #fff; --text: #1f2329; --muted: #8a93a3; --soft: #5a6271;
    --border: #e3e6ea; --border2: #eef0f3;
    --ok: #16a34a; --okBg: #e7f7ec;
    --fail: #dc2626; --failBg: #fdecec;
    --warn: #d97706; --warnBg: #fff4e0;
    --redir: #2e75b6;
  }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; background: var(--bg); color: var(--text);
    font: 14px/1.5 -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; }}
  a {{ color: var(--brand); text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 12px; }}
  .break {{ word-break: break-all; }}
  .muted {{ color: var(--muted); }}

  /* ---------- HERO ---------- */
  .hero {{ background: linear-gradient(120deg, var(--brand), var(--brand2));
    color: white; padding: 24px 32px; box-shadow: 0 2px 6px rgba(0,0,0,.1); }}
  .hero h1 {{ margin: 0; font-size: 24px; }}
  .hero .sub {{ opacity: 0.85; font-size: 13px; margin-top: 4px; }}
  .verdict-bar {{ background: {verdict_bg}; border-left: 6px solid {verdict_color};
    padding: 20px 32px; display: flex; align-items: center; gap: 24px;
    flex-wrap: wrap; box-shadow: 0 1px 2px rgba(0,0,0,.04); }}
  .verdict-icon {{ width: 56px; height: 56px; border-radius: 50%; background: {verdict_color};
    color: white; display: grid; place-items: center; font-size: 28px; font-weight: 700; }}
  .verdict-text {{ font-size: 28px; font-weight: 700; color: {verdict_color}; line-height: 1; }}
  .verdict-sub {{ color: var(--soft); margin-top: 4px; font-size: 14px; }}
  .stats {{ display: flex; gap: 18px; margin-left: auto; flex-wrap: wrap; }}
  .stat {{ text-align: center; min-width: 80px; }}
  .stat .v {{ font-size: 22px; font-weight: 700; color: var(--text); }}
  .stat .v.ok {{ color: var(--ok); }}
  .stat .v.fail {{ color: var(--fail); }}
  .stat .l {{ font-size: 10px; text-transform: uppercase; color: var(--muted);
    letter-spacing: 0.5px; font-weight: 600; }}

  /* ---------- LAYOUT ---------- */
  .layout {{ max-width: 1240px; margin: 24px auto; padding: 0 24px;
    display: grid; grid-template-columns: 220px 1fr; gap: 24px; }}
  @media (max-width: 920px) {{ .layout {{ grid-template-columns: 1fr; }} }}
  .toc {{ position: sticky; top: 18px; align-self: start;
    background: var(--card); border: 1px solid var(--border);
    border-radius: 8px; padding: 14px; }}
  .toc h3 {{ font-size: 11px; text-transform: uppercase; color: var(--muted);
    margin: 0 0 10px 0; letter-spacing: 0.5px; }}
  .toc a {{ display: block; padding: 7px 10px; border-radius: 4px;
    color: var(--text); font-size: 13px; }}
  .toc a:hover {{ background: var(--border2); text-decoration: none; }}
  .toc .count {{ float: right; background: var(--brand); color: white;
    padding: 1px 7px; border-radius: 10px; font-size: 11px; }}
  .toc .count.fail {{ background: var(--fail); }}
  .toc .count.warn {{ background: var(--warn); }}

  /* ---------- CARDS ---------- */
  section.card {{ background: var(--card); border: 1px solid var(--border);
    border-radius: 8px; padding: 22px 26px; margin-bottom: 18px;
    box-shadow: 0 1px 2px rgba(0,0,0,.04); }}
  section.card h2 {{ margin: 0 0 14px 0; font-size: 16px; color: var(--brand);
    text-transform: uppercase; letter-spacing: 0.5px; display: flex;
    justify-content: space-between; align-items: center; }}
  .card-meta {{ font-weight: normal; color: var(--muted); font-size: 12px;
    text-transform: none; letter-spacing: 0; }}

  /* ---------- CONFIG TABLE ---------- */
  .kvtbl {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  .kvtbl td {{ padding: 8px 12px; border-bottom: 1px solid var(--border2); }}
  .kvtbl td:first-child {{ font-weight: 600; color: var(--soft); width: 180px; }}
  .kvtbl tr:last-child td {{ border-bottom: 0; }}

  /* ---------- FAILURES ---------- */
  .failcard {{ background: #fff7f5; border: 1px solid #fad2cc;
    border-radius: 6px; margin: 0 0 12px 0; overflow: hidden; }}
  .failhdr {{ display: flex; align-items: center; gap: 10px;
    padding: 10px 14px; border-bottom: 1px solid #fad2cc; }}
  .failbadge {{ background: var(--fail); color: white; padding: 2px 10px;
    border-radius: 3px; font-size: 11px; font-weight: 700; letter-spacing: 0.5px; }}
  .failname {{ font-weight: 600; color: var(--fail); font-family: ui-monospace, monospace;
    font-size: 12px; }}
  .failtext {{ margin: 0; padding: 12px 14px; background: white;
    font-family: ui-monospace, monospace; font-size: 11px; color: var(--text);
    white-space: pre-wrap; word-break: break-word; max-height: 280px; overflow: auto; }}

  /* ---------- STEP CARDS ---------- */
  details.step {{ border: 1px solid var(--border); border-radius: 6px;
    margin-bottom: 10px; background: white; overflow: hidden; }}
  details.step.fail {{ border-color: #fad2cc; background: #fff7f5; }}
  details.step.warn {{ border-color: #fae5c0; background: #fffbf2; }}
  details.step summary {{ display: flex; align-items: center; gap: 12px;
    padding: 10px 16px; cursor: pointer; list-style: none; user-select: none; }}
  details.step summary::-webkit-details-marker {{ display: none; }}
  details.step .stepicon {{ width: 22px; height: 22px; border-radius: 50%;
    display: grid; place-items: center; color: white; font-weight: 700;
    font-size: 12px; flex-shrink: 0; }}
  details.step .stepicon.ok {{ background: var(--ok); }}
  details.step .stepicon.fail {{ background: var(--fail); }}
  details.step .stepicon.warn {{ background: var(--warn); }}
  .stepnum {{ color: var(--muted); font-size: 11px; min-width: 50px; }}
  .steptitle {{ flex: 1; font-weight: 500; color: var(--text); }}
  details.step.fail .steptitle {{ color: var(--fail); }}
  .stepmeta {{ color: var(--muted); font-size: 11px; }}
  .stepbody {{ padding: 12px 16px 14px 16px; border-top: 1px solid var(--border); }}
  details.step.fail .stepbody {{ border-top-color: #fad2cc; }}

  .evrow {{ display: grid; grid-template-columns: 20px 1fr; gap: 8px;
    padding: 4px 0; font-size: 13px; align-items: start; }}
  .evicon {{ font-weight: 700; text-align: center; }}
  .evicon.ok {{ color: var(--ok); }} .evicon.fail {{ color: var(--fail); }}
  .evicon.warn {{ color: var(--warn); }} .evicon.info {{ color: var(--brand2); }}
  .evrow.fail .evtext {{ color: var(--fail); }}

  /* inline screenshots inside a step card */
  .ishot-grid {{ display: grid;
    grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
    gap: 8px; margin-top: 10px; padding-top: 10px;
    border-top: 1px solid var(--border2); }}
  .ishot {{ background: #fafbfd; border: 1px solid var(--border); border-radius: 4px;
    overflow: hidden; }}
  .ishot img {{ width: 100%; height: 90px; object-fit: cover; object-position: top;
    display: block; cursor: zoom-in; }}
  .ishot-cap {{ padding: 4px 8px; font-size: 10px; color: var(--muted);
    border-top: 1px solid var(--border2); overflow: hidden; text-overflow: ellipsis;
    white-space: nowrap; }}

  /* ---------- NETWORK ---------- */
  details.net {{ border: 1px solid var(--border); border-radius: 4px;
    margin-bottom: 6px; background: white; }}
  details.net.fail {{ border-color: #fad2cc; }}
  details.net summary {{ display: grid;
    grid-template-columns: 70px 60px 60px 1fr 70px;
    align-items: center; gap: 10px;
    padding: 7px 12px; cursor: pointer; list-style: none;
    font-family: ui-monospace, monospace; font-size: 12px; }}
  details.net summary::-webkit-details-marker {{ display: none; }}
  .netmethod {{ font-weight: 700; text-align: center; padding: 2px 6px;
    border-radius: 3px; background: var(--border2); color: var(--soft); }}
  .netmethod.post {{ background: #e0ecf9; color: var(--brand); }}
  .netmethod.get  {{ background: #e6f7ec; color: var(--ok); }}
  .netstatus {{ font-weight: 700; text-align: center; padding: 2px 6px;
    border-radius: 3px; }}
  .netstatus.ok {{ background: var(--okBg); color: var(--ok); }}
  .netstatus.redir {{ background: #e0ecf9; color: var(--redir); }}
  .netstatus.warn {{ background: var(--warnBg); color: var(--warn); }}
  .netstatus.fail {{ background: var(--failBg); color: var(--fail); }}
  .netstatus.pending {{ background: #f0f0f0; color: var(--muted); }}
  .netkind {{ color: var(--muted); font-size: 10px; text-transform: uppercase; }}
  .netpath {{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    color: var(--text); }}
  .netsize {{ color: var(--muted); text-align: right; font-size: 11px; }}
  .netbody {{ padding: 10px 14px 12px 14px; border-top: 1px solid var(--border); }}
  .kv {{ margin-bottom: 8px; }}
  .kv b {{ display: block; font-size: 11px; text-transform: uppercase;
    color: var(--muted); letter-spacing: 0.5px; margin-bottom: 4px; }}
  .kv pre, .kv span.mono {{ margin: 0; padding: 8px 10px; background: #f8f9fb;
    border: 1px solid var(--border); border-radius: 3px;
    font-family: ui-monospace, monospace; font-size: 11px;
    white-space: pre-wrap; word-break: break-word; max-height: 240px; overflow: auto;
    display: block; }}

  /* ---------- SCREENSHOTS GALLERY ---------- */
  .gallery {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 12px; }}
  .gshot {{ background: white; border: 1px solid var(--border);
    border-radius: 6px; overflow: hidden; }}
  .gshot img {{ width: 100%; height: 140px; object-fit: cover; object-position: top;
    cursor: zoom-in; display: block; }}
  .gshot-cap {{ padding: 8px 12px; font-size: 11px; color: var(--soft);
    border-top: 1px solid var(--border2); background: #fafbfc; }}

  /* ---------- CONSOLE ---------- */
  .ctbl, .rawtbl {{ width: 100%; border-collapse: collapse;
    font-family: ui-monospace, monospace; font-size: 11px; }}
  .ctbl th, .rawtbl th {{ text-align: left; padding: 6px 10px; background: #f6f7f9;
    border-bottom: 1px solid var(--border); font-size: 10px; text-transform: uppercase;
    color: var(--muted); }}
  .ctbl td, .rawtbl td {{ padding: 5px 10px; border-bottom: 1px solid var(--border2);
    vertical-align: top; }}
  .cmtype {{ width: 80px; font-weight: 700; color: var(--soft); }}
  .cmsg.error .cmtxt, .cmsg.pageerror .cmtxt {{ color: var(--fail); }}
  .cmsg.warning .cmtxt {{ color: var(--warn); }}
  .ev-fail td {{ color: var(--fail); }}
  .ev-section td {{ font-weight: 700; color: var(--brand); }}

  footer {{ text-align: center; padding: 32px; font-size: 12px; color: var(--muted); }}
</style>
</head>
<body>

<header class="hero">
  <h1>Stratus QA Run Report</h1>
  <div class="sub">Generated {escape(generated_at)}  ·  Run ID
    <span class="mono">{escape(str(config.get('run_id','—')))}</span></div>
</header>

<div class="verdict-bar">
  <div class="verdict-icon">{verdict_icon}</div>
  <div>
    <div class="verdict-text">{verdict_text}</div>
    <div class="verdict-sub">{escape(verdict_sub)}</div>
  </div>
  <div class="stats">
    <div class="stat"><div class="v">{total}</div><div class="l">Steps</div></div>
    <div class="stat"><div class="v ok">{passed_n}</div><div class="l">Passed</div></div>
    <div class="stat"><div class="v fail">{failed_n}</div><div class="l">Failed</div></div>
    <div class="stat"><div class="v">{float(result.get('duration_s') or 0):.1f}s</div><div class="l">Duration</div></div>
    <div class="stat"><div class="v">{api_calls_n}</div><div class="l">API calls</div></div>
    <div class="stat"><div class="v fail">{err_calls_n}</div><div class="l">HTTP errors</div></div>
    <div class="stat"><div class="v">{len(all_shots)}</div><div class="l">Screenshots</div></div>
  </div>
</div>

<div class="layout">

  <!-- TOC sidebar -->
  <aside class="toc">
    <h3>📑 Sections</h3>
    <a href="#summary">📋 Summary</a>
    <a href="#failures">⚠️ Failures <span class="count fail">{failed_n}</span></a>
    <a href="#timeline">▶ Steps <span class="count">{len(sections)}</span></a>
    <a href="#network">🌐 Network <span class="count">{len(network)}</span></a>
    <a href="#gallery">📸 Screenshots <span class="count">{len(all_shots)}</span></a>
    <a href="#console">🖥 Console <span class="count warn">{len(all_console)}</span></a>
    <a href="#raw">📜 Raw events</a>
  </aside>

  <main>
    <!-- SUMMARY -->
    <section class="card" id="summary">
      <h2>📋 Run summary <span class="card-meta">configuration of this run</span></h2>
      <table class="kvtbl">
        <tr><td>Target URL</td><td class="mono">{escape(config.get('url') or '—')}</td></tr>
        <tr><td>Username</td><td>{escape(config.get('user') or '—')}</td></tr>
        <tr><td>Mode</td><td>{escape(mode)}</td></tr>
        <tr><td>Machine ID</td><td>{escape(config.get('machine_id') or '—')}</td></tr>
        <tr><td>Started at</td><td>{escape(started_at) or '—'}</td></tr>
        <tr><td>Duration</td><td>{float(result.get('duration_s') or 0):.1f} seconds</td></tr>
        <tr><td>Total steps</td><td>{total} ({passed_n} passed, {failed_n} failed)</td></tr>
        <tr><td>Network requests</td><td>{len(network)} total · {api_calls_n} API calls · {err_calls_n} HTTP errors</td></tr>
      </table>
    </section>

    <!-- FAILURES -->
    <section class="card" id="failures">
      <h2>⚠️ Failures <span class="card-meta">{failed_n} failure{'s' if failed_n != 1 else ''}</span></h2>
      {failures_html}
    </section>

    <!-- STEP-BY-STEP TIMELINE -->
    <section class="card" id="timeline">
      <h2>▶ Step-by-step <span class="card-meta">{len(sections)} sections — click to expand</span></h2>
      {steps_html}
    </section>

    <!-- NETWORK -->
    <section class="card" id="network">
      <h2>🌐 Network log <span class="card-meta">{len(network)} requests captured — click to see payload + response</span></h2>
      {network_html}
    </section>

    <!-- SCREENSHOTS -->
    <section class="card" id="gallery">
      <h2>📸 Screenshots <span class="card-meta">{len(all_shots)} captured</span></h2>
      <div class="gallery">{gallery_html}</div>
    </section>

    <!-- CONSOLE -->
    <section class="card" id="console">
      <h2>🖥 Browser console <span class="card-meta">{len(all_console)} message{'s' if len(all_console) != 1 else ''}</span></h2>
      {console_html}
    </section>

    <!-- RAW EVENTS -->
    <section class="card" id="raw">
      <h2>📜 Raw event log <span class="card-meta">{len(events)} events (capped at 1000)</span></h2>
      {raw_html}
    </section>

  </main>
</div>

<footer>
  Stratus QA Tool · Built once, runs forever, no subscriptions ·
  Report generated by web_ui/report_builder.py
</footer>

</body></html>"""

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path
