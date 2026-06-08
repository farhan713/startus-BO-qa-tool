"""Shared pytest fixtures.

Scope strategy:
- ``session`` for things that are expensive and stateless (config dump).
- ``function`` for browser context + page (clean state per test, video/screenshot
  artifacts isolated per test).
- ``session`` for DB connection (reused, no per-test schema reset).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterator

import pytest
from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright

# Make project modules importable regardless of pytest invocation dir.
_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))

from config import settings  # noqa: E402
from framework.api import StratusApiClient  # noqa: E402
from framework.db import SqlServerHelper  # noqa: E402
from framework.ui.pages import LoginPage  # noqa: E402
from framework.utils import get_logger  # noqa: E402

log = get_logger("conftest")


# ============================================================================
# Session-level
# ============================================================================

@pytest.fixture(scope="session", autouse=True)
def _banner() -> None:
    log.info("=" * 70)
    log.info("Stratus QA Run | env=%s | run_id=%s", settings.env_name, settings.run_id)
    log.info("  base_url   = %s", settings.app.base_url)
    log.info("  browser    = %s (headless=%s)", settings.browser.name, settings.browser.headless)
    log.info("  db         = %s:%s/%s", settings.db.host, settings.db.port, settings.db.name)
    log.info("=" * 70)


@pytest.fixture(scope="session")
def db() -> Iterator[SqlServerHelper]:
    helper = SqlServerHelper()
    yield helper
    helper.close()


# ============================================================================
# Playwright
# ============================================================================

@pytest.fixture(scope="session")
def playwright_instance() -> Iterator[Playwright]:
    with sync_playwright() as pw:
        yield pw


@pytest.fixture(scope="session")
def browser(playwright_instance: Playwright) -> Iterator[Browser]:
    launcher = getattr(playwright_instance, settings.browser.name)
    br = launcher.launch(
        headless=settings.browser.headless,
        slow_mo=settings.browser.slow_mo_ms,
    )
    yield br
    br.close()


@pytest.fixture
def context(browser: Browser, request: pytest.FixtureRequest) -> Iterator[BrowserContext]:
    record_video_dir = None
    if settings.browser.video_on_failure:
        record_video_dir = str(settings.reports_dir / "videos")
    ctx = browser.new_context(record_video_dir=record_video_dir)
    yield ctx
    # Save video artifacts only on failure, drop on success.
    failed = getattr(request.node, "rep_call", None) and request.node.rep_call.failed
    if not failed:
        for page in ctx.pages:
            try:
                if page.video:
                    page.video.delete()
            except Exception:
                pass
    ctx.close()


@pytest.fixture
def page(context: BrowserContext, request: pytest.FixtureRequest) -> Iterator[Page]:
    pg = context.new_page()
    yield pg
    if settings.browser.screenshot_on_failure:
        rep = getattr(request.node, "rep_call", None)
        if rep and rep.failed:
            name = request.node.name.replace("/", "_")
            out = settings.reports_dir / "screenshots" / f"FAIL_{name}.png"
            out.parent.mkdir(parents=True, exist_ok=True)
            try:
                pg.screenshot(path=str(out), full_page=True)
            except Exception as exc:
                log.warning("failed to capture screenshot: %s", exc)
    pg.close()


# Hook for the screenshot/video logic above — exposes rep_call on the item.
@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):  # noqa: ANN001
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)


# ============================================================================
# Domain fixtures
# ============================================================================

@pytest.fixture
def api() -> Iterator[StratusApiClient]:
    client = StratusApiClient()
    yield client
    client.close()


@pytest.fixture
def login_page(page: Page) -> LoginPage:
    lp = LoginPage(page)
    lp.open().wait_loaded()
    return lp
