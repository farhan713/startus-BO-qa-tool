"""Bulk runner — runs Single-Screen-style deep tests across many screens.

This is the enterprise-level execution:
  • Loops through every screen in the catalog (or a filtered subset)
  • For each: runs all auto-generated tests using the catalog data
  • Streams per-screen + per-test pass/fail
  • Produces a per-module breakdown report at the end
"""
from __future__ import annotations

import time
from collections import defaultdict
from pathlib import Path
from typing import Callable

from framework.catalog_builder import load_catalog
from framework.crawl_runner import _browser, _login, _execute_custom_steps
from framework.test_generator import generate_tests
from framework.demo_runner import (
    DemoConfig, DemoResult, StepEvent, _Tracker, _emit, build_url,
)


def run_bulk(
    cfg: DemoConfig,
    on_event: Callable[[StepEvent], None] | None = None,
    type_filter: set[str] | None = None,
    scope: str = "",
    max_screens: int | None = None,
    safe_mode: bool = True,
    max_tests_per_screen: int = 0,    # 0 = no limit
    selected_screens: list | None = None,   # explicit screennames; if set, overrides scope
) -> DemoResult:
    """Run auto-generated tests across many screens. Returns DemoResult.

    Per-screen results are tallied into the failures list so the UI can
    show "X/Y screens fully passed, Z had issues."
    """
    t = _Tracker(on_event, cfg)
    t.banner(f"Stratus QA — BULK test against {cfg.base_url}")

    catalog = load_catalog()
    if not catalog:
        t.fail("catalog", "no catalog found — run 'Build catalog' first")
        return _build_bulk_result(t, {})

    screens = catalog.get("screens") or []

    # If explicit selection provided, that's the source of truth.
    if selected_screens:
        sel = {s.lower() for s in selected_screens}
        screens = [s for s in screens if s.get("screenname","").lower() in sel]
        t.info(f"using {len(screens)} explicitly-selected screen(s)")
    else:
        # Apply substring scope + type filter
        if scope:
            scope_l = scope.lower()
            screens = [s for s in screens
                       if scope_l in s.get("screenname","").lower()
                       or scope_l in (s.get("label") or "").lower()]
        if type_filter:
            screens = [s for s in screens if s.get("type") in type_filter]
        if max_screens:
            screens = screens[:max_screens]
    t.info(f"will test {len(screens)} screen(s) from catalog of {len(catalog.get('screens',[]))}")

    # Per-screen score tracking
    screen_scores: dict[str, dict] = defaultdict(lambda: {"pass": 0, "fail": 0, "type": ""})

    network_log: list[dict] = []
    console_log: list[dict] = []
    try:
        with _browser(headless=cfg.headless, network_buffer=network_log,
                      console_buffer=console_log) as page:
            # 1) Login
            t.section("Authenticating")
            try:
                landed = _login(page, cfg, t)
                t.ok(f"logged in (URL: {landed})")
                t.screenshot(page, "after_login")
            except Exception as e:
                t.fail("login", str(e), page=page)
                return _build_bulk_result(t, screen_scores)

            shell_url = build_url(cfg.base_url, "/wrmsscreen")

            # 2) Per-screen loop
            for s_i, cat_entry in enumerate(screens, 1):
                sn = cat_entry["screenname"]
                stype = cat_entry.get("type", "other")
                screen_scores[sn]["type"] = stype
                t.section(f"[{s_i}/{len(screens)}] {sn} ({stype})")

                # Generate tests for this screen from its catalog entry
                auto_tests = generate_tests(cat_entry, safe_mode=safe_mode)
                if max_tests_per_screen > 0:
                    auto_tests = auto_tests[:max_tests_per_screen]
                t.info(f"  generated {len(auto_tests)} auto-tests")

                # Navigate to this screen
                screen_url = build_url(cfg.base_url, cat_entry["data_href"])
                try:
                    page.goto(screen_url, wait_until="domcontentloaded", timeout=20_000)
                except Exception:
                    try: page.goto(screen_url, wait_until="commit", timeout=10_000)
                    except Exception as e:
                        t.fail(sn, f"navigation failed: {e}", page=page)
                        screen_scores[sn]["fail"] += 1
                        continue

                time.sleep(4)
                # One snapshot per screen so the gallery covers all screens
                # (we don't shoot per-test in bulk to keep the gallery small).
                try: t.screenshot(page, f"{sn}_loaded")
                except Exception: pass

                # Run each test for this screen
                for tn, test in enumerate(auto_tests, 1):
                    ok, notes = _execute_custom_steps(page, test.steps, t, sn)
                    if ok:
                        screen_scores[sn]["pass"] += 1
                        t.ok(f"  ✓ {test.name[:60]}")
                    else:
                        screen_scores[sn]["fail"] += 1
                        t.fail(f"{sn}::auto::{tn}",
                               "; ".join(notes) or "test failed",
                               page=None)

                    # Refresh between tests (force reload to avoid state issues)
                    if tn < len(auto_tests):
                        try:
                            page.goto(shell_url, wait_until="commit", timeout=12_000)
                            time.sleep(1)
                            page.goto(screen_url, wait_until="domcontentloaded", timeout=15_000)
                            time.sleep(3)
                        except Exception:
                            pass

    except Exception as e:
        t.fail("runtime", f"unexpected: {e}")

    t.network_log = network_log
    t.console_log = console_log
    return _build_bulk_result(t, screen_scores)


def _build_bulk_result(t: _Tracker, screen_scores: dict) -> DemoResult:
    # Emit per-screen summary as final events
    by_type: dict[str, dict] = defaultdict(lambda: {"pass": 0, "fail": 0, "screens": 0})
    full_pass_screens = 0
    partial_screens = 0
    failed_screens = 0
    for sn, sc in screen_scores.items():
        by_type[sc["type"]]["pass"]   += sc["pass"]
        by_type[sc["type"]]["fail"]   += sc["fail"]
        by_type[sc["type"]]["screens"] += 1
        if sc["fail"] == 0 and sc["pass"] > 0:
            full_pass_screens += 1
        elif sc["pass"] > 0:
            partial_screens += 1
        else:
            failed_screens += 1

    if screen_scores:
        _emit(t.cb, StepEvent(
            type="info",
            text=f"📊 SUMMARY: {full_pass_screens} fully green, "
                 f"{partial_screens} partial, {failed_screens} failed "
                 f"(of {len(screen_scores)} screens)",
        ))
        for stype, sc in sorted(by_type.items()):
            _emit(t.cb, StepEvent(
                type="info",
                text=f"  {stype:8} {sc['screens']:>3} screens, "
                     f"{sc['pass']:>4} tests passed, {sc['fail']:>4} failed",
            ))

    total = t.passed + t.failed
    passed = total > 0 and t.failed == 0
    res = DemoResult(
        passed=passed,
        steps_total=total,
        steps_passed=t.passed,
        steps_failed=t.failed,
        duration_s=t.duration(),
        failures=list(t.failures),
        screenshots=[],
        final_screenshot=None,
    )
    verdict = ("PASS" if passed else
               f"PARTIAL — {t.passed}/{total}" if t.passed else "FAIL")
    _emit(t.cb, StepEvent(type="done", text=f"{verdict} across {len(screen_scores)} screens"))
    return res
