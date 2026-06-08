"""API-mode demo runner — exercises Stratus's /stratus endpoint directly.

No browser. No SPA. No selectors. Just HTTP + JSON. Fast and reliable.

Use this when:
- The SPA is too slow / fragile to drive
- You want CI-quality reliability
- You only need to verify backend behavior
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from framework.api import StratusAPI
from framework.demo_runner import DemoConfig, DemoResult, StepEvent, _Tracker, _emit


def run_api_demo(cfg: DemoConfig, on_event: Callable[[StepEvent], None] | None = None) -> DemoResult:
    """Run the customer-module demo using API calls only — no browser."""
    t = _Tracker(on_event, cfg)
    t.banner(f"Stratus QA — API mode against {cfg.base_url}")

    api = StratusAPI(base_url=cfg.base_url, machine_id=cfg.machine_id or "100")

    # === 1. Login ===
    t.section("Authenticating via /UserAuthenticationServlet.do")
    try:
        auth = api.login(cfg.user, cfg.password)
        if not auth.success:
            t.fail("login", auth.message or "authentication failed")
            return _build_api_result(t)
        t.info(f"employee: {auth.employee_name} (#{auth.employee_id})")
        t.info(f"store:    #{auth.store_id} — {auth.store_name}")
        t.info(f"modules:  {len(auth.enterprise_modules)} enterprise modules visible")
        t.ok("authenticated successfully")
    except Exception as e:
        t.fail("login", str(e))
        return _build_api_result(t)

    # === 2. Search Customers — SMITH ===
    t.section("Searching customers where last_name = 'SMITH'")
    try:
        result = api.search_customers(last_name="SMITH")
        if not result.success:
            t.fail("search_smith", result.message or "search failed")
        else:
            t.info(f"found {result.row_count} row(s); total_count={result.total_count}")
            if result.rows:
                sample = result.rows[0]
                t.info(f"sample: #{sample.get('CUSTOMER_ID')} "
                       f"{sample.get('FIRST_NAME')} {sample.get('LAST_NAME')} "
                       f"({sample.get('EMAIL1') or '—'})")
            t.ok(f"search complete — {result.row_count} match(es)")
    except Exception as e:
        t.fail("search_smith", str(e))

    # === 3. Refined search — SMITH + first_name JOHN ===
    t.section("Refined search: last_name='SMITH' first_name='JOHN'")
    try:
        result = api.search_customers(last_name="SMITH", first_name="JOHN")
        if not result.success:
            t.fail("search_refined", result.message or "search failed")
        else:
            t.info(f"found {result.row_count} match(es) for John Smith")
            t.ok(f"refined search complete")
    except Exception as e:
        t.fail("search_refined", str(e))

    # === 4. Empty search — should return many results ===
    t.section("Open search: no filters (sample of full list)")
    try:
        result = api.search_customers()
        if not result.success:
            t.fail("search_all", result.message or "search failed")
        else:
            t.info(f"returned {result.row_count} row(s) (capped server-side)")
            t.ok("open search complete")
    except Exception as e:
        t.fail("search_all", str(e))

    # === 5. Company search ===
    t.section("Search by company name containing 'CELERANT'")
    try:
        result = api.search_customers(company="CELERANT")
        if not result.success:
            t.fail("search_company", result.message or "search failed")
        else:
            t.info(f"found {result.row_count} CELERANT customer(s)")
            t.ok("company search complete")
    except Exception as e:
        t.fail("search_company", str(e))

    # === 6. Email search ===
    t.section("Search by email containing '.COM'")
    try:
        result = api.search_customers(email=".COM")
        if not result.success:
            t.fail("search_email", result.message or "search failed")
        else:
            t.info(f"found {result.row_count} customer(s) with .com email")
            t.ok("email search complete")
    except Exception as e:
        t.fail("search_email", str(e))

    return _build_api_result(t)


def _build_api_result(t: _Tracker) -> DemoResult:
    total = t.passed + t.failed
    passed = total > 0 and t.failed == 0
    res = DemoResult(
        passed=passed,
        steps_total=total,
        steps_passed=t.passed,
        steps_failed=t.failed,
        duration_s=t.duration(),
        failures=list(t.failures),
        screenshots=[],            # API mode has no screenshots
        final_screenshot=None,
    )
    verdict = "PASS" if passed else "FAIL"
    _emit(t.cb, StepEvent(type="done", text=f"{verdict} — {t.passed}/{total} steps"))
    return res
