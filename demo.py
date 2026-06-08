#!/usr/bin/env python3
"""Stratus QA Tool — Live Demo Runner.

Runs an end-to-end demo of the Customer module against a real Stratus
BackOffice instance:

    Login → Customer List → Search → Action menu → New → Fill form →
    Save → Edit → Print → Close

Designed to be RUN VISIBLY — opens a real browser, slows the clicks down
so a human can follow along, narrates each step to the terminal, and
takes a screenshot before exiting.

Usage:

    # Visible browser, slow-motion, narrated
    python demo.py

    # Headless (no browser window) — for CI
    python demo.py --headless

    # Faster (default 600ms slow-mo per action; this drops to 100ms)
    python demo.py --fast

    # Skip the destructive bits (no New customer, no Edit, no Print)
    python demo.py --read-only

Requires that the URL/credentials in .env are valid.
"""
from __future__ import annotations

import argparse
import sys
import time
from contextlib import contextmanager
from pathlib import Path

# Allow running from the qa-automation/ directory.
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright

from config import settings
from framework.ui.pages import (
    CustomerDetailPage,
    CustomerInput,
    CustomerListPage,
    LoginPage,
)


# ---------------------------------------------------------------- narration

class Narrator:
    """Pretty-prints what the tool is doing."""
    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.step = 0
        self._t0 = time.time()

    def banner(self, text: str) -> None:
        line = "=" * 72
        self._print(f"\n{line}\n  {text}\n{line}")

    def section(self, text: str) -> None:
        self.step += 1
        self._print(f"\n[ Step {self.step} ] {text}")

    def info(self, text: str) -> None:
        self._print(f"    → {text}")

    def ok(self, text: str) -> None:
        self._print(f"    ✓ {text}")

    def warn(self, text: str) -> None:
        self._print(f"    ⚠ {text}")

    def fail(self, text: str) -> None:
        self._print(f"    ✗ {text}")

    def done(self) -> None:
        secs = time.time() - self._t0
        self._print(f"\n  Total runtime: {secs:.1f}s\n")

    def _print(self, msg: str) -> None:
        if self.enabled:
            print(msg, flush=True)


# ------------------------------------------------------------ demo segments

def demo_login(page, n: Narrator) -> None:
    n.section("Logging into Stratus BackOffice")
    lp = LoginPage(page)
    n.info(f"opening {lp.url}")
    lp.open().wait_loaded()
    n.info(f"entering credentials for user {settings.creds.user!r}")
    lp.login()
    page.wait_for_load_state("networkidle")
    n.ok("login submitted")


def demo_open_customer_list(page, n: Narrator) -> CustomerListPage:
    n.section("Navigating to Customer List screen")
    cl = CustomerListPage(page)
    n.info(f"opening {cl.url}")
    cl.open().wait_loaded()
    n.ok("Customer List loaded — New, Action and Close buttons visible")
    return cl


def demo_search(cl: CustomerListPage, n: Narrator) -> None:
    n.section("Searching for customers with last name 'SMITH'")
    cl.show_search_criteria()
    n.info("filled Last Name = 'SMITH'")
    cl.fill_search(last_name="SMITH")
    cl.click_search()
    cl.page.wait_for_load_state("networkidle")
    count = cl.row_count()
    n.ok(f"search complete — grid has {count} row(s)")


def demo_action_menu(cl: CustomerListPage, n: Narrator) -> None:
    n.section("Opening the Action dropdown")
    cl._open_action_menu()
    items_visible = []
    if cl.locator(CustomerListPage.BTN_EDIT).is_visible():
        items_visible.append("Edit")
    if cl.locator(CustomerListPage.BTN_DELETE).is_visible():
        items_visible.append("Delete")
    if cl.locator(CustomerListPage.BTN_PRINT).is_visible():
        items_visible.append("Print List")
    n.ok(f"Action menu exposes: {', '.join(items_visible)}")
    # Close the menu by clicking elsewhere
    cl.page.keyboard.press("Escape")


def demo_create_new(page, cl: CustomerListPage, n: Narrator) -> None:
    n.section("Creating a NEW customer via the New button")
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
    n.info(f"filling form: {data.first_name} {data.last_name} / Co: {data.company}")
    detail.fill(data)
    n.info("clicking Save")
    detail.click_save()
    err = detail.error_text()
    if err:
        n.fail(f"server returned error: {err}")
    else:
        n.ok("save succeeded — no errors on the page")


def demo_edit_existing(page, cl: CustomerListPage, n: Narrator) -> None:
    n.section("Editing an existing customer (search → row → Edit action)")
    cl.open().wait_loaded()
    cl.search_by_last_name("SMITH")
    if cl.row_count() == 0:
        n.warn("no SMITH rows in this environment — skipping edit")
        return
    cl.click_row(0)
    n.info("selected the first matching row")
    cl.click_edit()
    detail = CustomerDetailPage(page)
    detail.wait_loaded()
    new_first = f"Updated{int(time.time()) % 1000}"
    n.info(f"changing first name to {new_first!r}")
    detail.fill(CustomerInput(first_name=new_first))
    detail.click_save()
    if detail.error_text():
        n.fail(detail.error_text())
    else:
        n.ok("edit saved")


def demo_print(cl: CustomerListPage, n: Narrator) -> None:
    n.section("Triggering Print List from the Action menu")
    cl.open().wait_loaded()
    cl.search_by_last_name("SMITH")
    cl.click_print_list()
    cl.page.wait_for_load_state("networkidle")
    n.ok("print job initiated (popup/report may open)")


def demo_close(cl: CustomerListPage, n: Narrator) -> None:
    n.section("Closing the Customer List screen")
    cl.open().wait_loaded()
    cl.click_close()
    cl.page.wait_for_load_state("networkidle")
    n.ok("Close clicked — back to main")


def take_screenshot(page, name: str) -> Path:
    out = settings.reports_dir / "screenshots" / f"demo_{name}_{int(time.time())}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(out), full_page=True)
    return out


# ---------------------------------------------------------------- main

@contextmanager
def browser_session(headless: bool, slow_mo: int):
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless, slow_mo=slow_mo)
        context = browser.new_context()
        page = context.new_page()
        page.set_default_timeout(settings.browser.timeout_ms)
        try:
            yield page
        finally:
            context.close()
            browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--headless", action="store_true", help="Hide the browser")
    parser.add_argument("--fast", action="store_true", help="Reduce slow-mo to 100ms")
    parser.add_argument("--read-only", action="store_true",
                        help="Skip the New/Edit/Print steps")
    parser.add_argument("--no-narrate", action="store_true",
                        help="Quiet mode (no step printing)")
    args = parser.parse_args()

    slow_mo = 100 if args.fast else 600
    if args.headless:
        slow_mo = 0

    n = Narrator(enabled=not args.no_narrate)
    n.banner("Stratus QA Tool — Customer Module Demo")
    n.info(f"target: {settings.app.base_url}")
    n.info(f"user:   {settings.creds.user}")
    n.info(f"mode:   headless={args.headless} slow_mo={slow_mo}ms")

    failures = []

    try:
        with browser_session(headless=args.headless, slow_mo=slow_mo) as page:
            demo_login(page, n)

            cl = demo_open_customer_list(page, n)
            demo_search(cl, n)
            demo_action_menu(cl, n)

            if not args.read_only:
                try:
                    demo_create_new(page, cl, n)
                except Exception as e:
                    failures.append(("create_new", str(e)))
                    n.fail(f"create_new failed: {e}")

                try:
                    demo_edit_existing(page, cl, n)
                except Exception as e:
                    failures.append(("edit_existing", str(e)))
                    n.fail(f"edit_existing failed: {e}")

                try:
                    demo_print(cl, n)
                except Exception as e:
                    failures.append(("print", str(e)))
                    n.fail(f"print failed: {e}")
            else:
                n.warn("read-only mode: skipping New / Edit / Print")

            demo_close(cl, n)

            shot = take_screenshot(page, "final")
            n.ok(f"final screenshot saved → {shot}")

    except Exception as e:
        n.banner("DEMO ABORTED")
        n.fail(str(e))
        n.done()
        return 2

    n.banner("DEMO COMPLETE" if not failures else f"DEMO COMPLETE — {len(failures)} segment(s) failed")
    for name, err in failures:
        n.fail(f"{name}: {err}")
    n.done()
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
