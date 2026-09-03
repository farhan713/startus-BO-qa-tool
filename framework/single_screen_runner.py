"""Run all auto-generated tests for a SINGLE screen from the catalog.

This is the "manual tester for one screen" mode — given a screen name
from the catalog, it runs every auto-generated test plus any custom
tests the user added.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from framework.crawl_runner import (
    _login, _discover_screens, _browser, _test_screen,
    ScreenSpec, _execute_custom_steps, parse_custom_tests,
    _build_crawl_result,
)
from framework.catalog_builder import load_catalog
from framework.test_generator import generate_tests
from framework.demo_runner import (
    DemoConfig, DemoResult, StepEvent, _Tracker, _emit, build_url,
)


def run_single_screen(
    cfg: DemoConfig,
    screenname: str,
    on_event: Callable[[StepEvent], None] | None = None,
    safe_mode: bool = True,
    custom_tests_yaml: str = "",
) -> DemoResult:
    """Run every auto-generated test for one specific screen.

    Args:
      cfg: demo config (URL, creds, etc.)
      screenname: the screen to test (must exist in catalog)
      safe_mode: if True, skip destructive actions (Save/Delete writes)
      custom_tests_yaml: additional user-provided test cases
    """
    t = _Tracker(on_event, cfg)
    t.banner(f"Stratus QA — Single-screen test of '{screenname}' against {cfg.base_url}")

    catalog = load_catalog()
    if not catalog:
        t.fail("catalog", "no catalog found — run 'Discover Catalog' first")
        return _build_crawl_result(t, [])

    # Find the screen in the catalog
    cat_entry = None
    for s in catalog.get("screens") or []:
        if s.get("screenname", "").lower() == screenname.lower():
            cat_entry = s
            break
    if not cat_entry:
        available = ", ".join(s["screenname"] for s in catalog.get("screens", [])[:20])
        t.fail("catalog", f"screen {screenname!r} not in catalog. Available: {available}…")
        return _build_crawl_result(t, [])

    t.info(f"catalog entry: type={cat_entry['type']}, "
           f"{len(cat_entry.get('fields') or [])} fields, "
           f"{len(cat_entry.get('topnav_buttons') or [])} topnav buttons, "
           f"{len(cat_entry.get('action_menu_items') or [])} action items")

    # Generate the tests for this screen
    auto_tests = generate_tests(cat_entry, safe_mode=safe_mode)
    t.ok(f"generated {len(auto_tests)} auto-tests for this {cat_entry['type']} screen")

    # Parse custom tests (optional add-ons)
    custom_tests = []
    if custom_tests_yaml.strip():
        try:
            tcs = parse_custom_tests(custom_tests_yaml)
            # Filter to ones for THIS screen
            custom_tests = [tc for tc in tcs
                            if tc.screen.lower() == screenname.lower()]
            t.info(f"+ {len(custom_tests)} custom test case(s) for this screen")
        except Exception as e:
            t.warn(f"custom test parse failed: {e}")

    network_log: list[dict] = []
    console_log: list[dict] = []
    try:
        with _browser(headless=cfg.headless, network_buffer=network_log,
                      console_buffer=console_log) as page:
            # 1) Login
            t.section("Authenticating")
            try:
                landed = _login(page, cfg, t)
                t.info(f"post-login URL: {landed}")
                t.ok("logged in")
                # Snapshot the post-login state so the report's screenshot
                # gallery has at least one image even on fully-passing runs.
                t.screenshot(page, "after_login")
            except Exception as e:
                t.fail("login", str(e), page=page)
                return _build_crawl_result(t, [])

            # 2) Navigate to the screen ONCE
            t.section(f"Navigating to {screenname}")
            spec = ScreenSpec(
                screenname=cat_entry["screenname"],
                label=cat_entry.get("label", ""),
                data_href=cat_entry["data_href"],
                type=cat_entry["type"],
            )
            base_url_for_screen = build_url(cfg.base_url, spec.data_href)
            try:
                page.goto(base_url_for_screen, wait_until="domcontentloaded", timeout=30_000)
            except Exception:
                try: page.goto(base_url_for_screen, wait_until="commit", timeout=15_000)
                except Exception as e:
                    t.fail("navigate", str(e), page=page)
                    return _build_crawl_result(t, [])

            # Adaptive settle
            time.sleep(4)
            quick = page.evaluate("""() => ({
                n: document.querySelectorAll('button:not([disabled]), input:not([type=hidden])').length,
                t: !!document.querySelector('#topNav, .screen-title-header, .ui-jqgrid')
            })""")
            if quick.get("n", 0) == 0 and not quick.get("t"):
                time.sleep(4)
            t.ok(f"screen loaded ({quick.get('n', 0)} interactive elements)")
            t.screenshot(page, f"{screenname}_initial")

            # Helper to re-navigate to the screen so each test gets fresh state.
            # We bounce through /wrmsscreen then back, because goto(same URL)
            # doesn't actually reload when only the hash differs.
            from framework.demo_runner import build_url as _bu
            shell_url = _bu(cfg.base_url, "/wrmsscreen")

            def _refresh():
                try:
                    # Force a real reload by going to the shell first
                    page.goto(shell_url, wait_until="commit", timeout=15_000)
                    time.sleep(1.5)
                    page.goto(base_url_for_screen, wait_until="domcontentloaded", timeout=20_000)
                except Exception:
                    try: page.goto(base_url_for_screen, wait_until="commit", timeout=10_000)
                    except Exception: pass
                time.sleep(4)    # let SPA render
                quick = page.evaluate("""() => ({
                    n: document.querySelectorAll('button:not([disabled]), input:not([type=hidden])').length,
                    t: !!document.querySelector('#topNav, .screen-title-header, .ui-jqgrid')
                })""")
                if quick.get("n", 0) == 0 and not quick.get("t"):
                    time.sleep(3)

            # 3) Run each auto-generated test — re-navigate between tests
            for i, test in enumerate(auto_tests, 1):
                t.section(f"[Auto {i}/{len(auto_tests)}] {test.name}")
                if i > 1:    # skip refresh on first test — already loaded
                    _refresh()
                ok, notes = _execute_custom_steps(page, test.steps, t, screenname)
                if ok:
                    t.ok(f"passed ({len(test.steps)} step(s))")
                else:
                    t.fail(f"{screenname}::auto::{i}",
                           "; ".join(notes) or "test failed", page=page)
                # Snapshot the end-of-test state regardless of pass/fail so
                # the gallery has one image per test (not just failures).
                try:
                    safe = "".join(c if c.isalnum() else "_" for c in test.name)[:40]
                    t.screenshot(page, f"auto_{i:02d}_{safe}")
                except Exception:
                    pass

            # 4) Run custom tests — also fresh state between each
            for i, tc in enumerate(custom_tests, 1):
                t.section(f"[Custom {i}/{len(custom_tests)}] {tc.name}")
                _refresh()
                ok, notes = _execute_custom_steps(page, tc.steps, t, screenname)
                if ok:
                    t.ok(f"passed ({len(tc.steps)} step(s))")
                else:
                    t.fail(f"{screenname}::custom::{tc.name}",
                           "; ".join(notes) or "custom test failed", page=page)
                try:
                    safe = "".join(c if c.isalnum() else "_" for c in tc.name)[:40]
                    t.screenshot(page, f"custom_{i:02d}_{safe}")
                except Exception:
                    pass

    except Exception as e:
        t.fail("runtime", f"unexpected: {e}")

    t.network_log = network_log
    t.console_log = console_log
    return _build_crawl_result(t, [])


def run_custom_multiscreen(
    cfg: DemoConfig,
    on_event: Callable[[StepEvent], None] | None = None,
    custom_tests_yaml: str = "",
    safe_mode: bool = True,
) -> DemoResult:
    """Run user test cases that span MANY screens, in one login session.

    The single-screen runner filters custom tests to one screen, so a test file
    covering 10 screens needs the app driven to each screen in turn. This logs in
    once, then for every distinct screen named in the YAML it navigates there and
    runs that screen's steps. No auto-generated smoke tests — only the tester's
    own scenarios, each against its real screen.
    """
    t = _Tracker(on_event, cfg)
    t.banner(f"Stratus QA — Multi-screen test against {cfg.base_url}")

    catalog = load_catalog()
    if not catalog:
        t.fail("catalog", "no catalog found — run 'Discover Catalog' first")
        return _build_crawl_result(t, [])

    try:
        tcs = parse_custom_tests(custom_tests_yaml)
    except Exception as e:
        t.fail("parse", f"could not read the test file: {e}")
        return _build_crawl_result(t, [])
    if not tcs:
        t.fail("parse", "no test scenarios found in the file")
        return _build_crawl_result(t, [])

    # Group scenarios by their screen, preserving first-seen order.
    by_screen: dict[str, list] = {}
    for tc in tcs:
        by_screen.setdefault((tc.screen or "").strip(), []).append(tc)

    cat_by_name = {s.get("screenname", "").lower(): s
                   for s in (catalog.get("screens") or [])}

    from framework.demo_runner import build_url as _bu
    shell_url = _bu(cfg.base_url, "/wrmsscreen")

    network_log: list[dict] = []
    console_log: list[dict] = []
    try:
        with _browser(headless=cfg.headless, network_buffer=network_log,
                      console_buffer=console_log) as page:
            t.section("Authenticating")
            try:
                landed = _login(page, cfg, t)
                t.info(f"post-login URL: {landed}")
                t.ok("logged in")
                t.screenshot(page, "after_login")
            except Exception as e:
                t.fail("login", str(e), page=page)
                return _build_crawl_result(t, [])

            for screen, scenarios in by_screen.items():
                cat_entry = cat_by_name.get(screen.lower())
                if not cat_entry:
                    avail = ", ".join(list(cat_by_name.keys())[:15])
                    t.fail(f"{screen or '(blank)'}::catalog",
                           f"screen {screen!r} not in catalog. Known: {avail}…")
                    continue

                url = _bu(cfg.base_url, cat_entry["data_href"])
                t.section(f"→ {cat_entry.get('label') or screen} "
                          f"({len(scenarios)} scenario(s))")

                for i, tc in enumerate(scenarios, 1):
                    # Fresh state per scenario: bounce through the shell, then in.
                    try:
                        page.goto(shell_url, wait_until="commit", timeout=15_000)
                        page.goto(url, wait_until="domcontentloaded", timeout=20_000)
                    except Exception:
                        try:
                            page.goto(url, wait_until="commit", timeout=10_000)
                        except Exception as e:
                            t.fail(f"{screen}::nav", str(e))
                            continue
                    try:
                        page.wait_for_timeout(1200)
                    except Exception:
                        pass

                    t.section(f"[{screen} {i}/{len(scenarios)}] {tc.name}")
                    try:
                        ok, notes = _execute_custom_steps(page, tc.steps, t, screen)
                        for n in (notes or []):
                            t.info(n)
                        if ok:
                            t.ok(f"passed ({len(tc.steps)} step(s))")
                        safe = "".join(c if c.isalnum() else "_"
                                       for c in tc.name)[:40]
                        t.screenshot(page, f"{screen}_{i:02d}_{safe}")
                    except Exception as e:
                        t.fail(f"{screen}::{i}", str(e), page=page)
    finally:
        try:
            _Tracker._last_network_log = list(network_log)
            _Tracker._last_console_log = list(console_log)
        except Exception:
            pass

    return _build_crawl_result(t, [])
