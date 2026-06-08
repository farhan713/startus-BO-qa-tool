"""End-to-end demo tests for the Customer module.

Each test exercises one feature on its own so failures are isolated.
"""
from __future__ import annotations

import time

import pytest
from playwright.sync_api import Page

from framework.ui.pages import (
    CustomerDetailPage,
    CustomerInput,
    CustomerListPage,
    LoginPage,
)


# ----------------------------------------------------------------- fixtures

@pytest.fixture
def authed_page(login_page: LoginPage) -> Page:
    """Log in and return the underlying Page on whatever screen we land on."""
    login_page.login()
    login_page.page.wait_for_load_state("networkidle")
    return login_page.page


@pytest.fixture
def customer_list(authed_page: Page) -> CustomerListPage:
    lp = CustomerListPage(authed_page)
    lp.open().wait_loaded()
    return lp


# ----------------------------------------------------------------- tests

@pytest.mark.demo
@pytest.mark.smoke
def test_customer_list_loads_with_full_action_set(customer_list: CustomerListPage):
    """The Customer List shows the full action set we depend on."""
    assert customer_list.locator(CustomerListPage.BTN_NEW).is_visible()
    assert customer_list.locator(CustomerListPage.BTN_ACTION).is_visible()
    assert customer_list.locator(CustomerListPage.BTN_CLOSE).is_visible()
    # Either Show or Hide criteria is visible at any moment (it toggles)
    show = customer_list.locator(CustomerListPage.SHOW_CRITERIA)
    hide = customer_list.locator(CustomerListPage.HIDE_CRITERIA)
    assert show.is_visible() or hide.is_visible()


@pytest.mark.demo
def test_search_by_last_name(customer_list: CustomerListPage):
    """Search for SMITH and confirm the grid responds without crashing."""
    customer_list.show_search_criteria()
    customer_list.fill_search(last_name="SMITH")
    customer_list.click_search()
    # Grid may show 0..N rows depending on data; we just assert no crash.
    customer_list.page.wait_for_load_state("networkidle")
    # Reset cleanly for downstream tests
    customer_list.click_reset()


@pytest.mark.demo
def test_action_dropdown_exposes_edit_delete_print(customer_list: CustomerListPage):
    """Action dropdown opens and exposes Edit/Delete/Print."""
    customer_list._open_action_menu()
    assert customer_list.locator(CustomerListPage.BTN_EDIT).is_visible()
    assert customer_list.locator(CustomerListPage.BTN_DELETE).is_visible()
    assert customer_list.locator(CustomerListPage.BTN_PRINT).is_visible()


@pytest.mark.demo
def test_create_new_customer(authed_page: Page, customer_list: CustomerListPage):
    """Click New → fill form → Save → no errors shown."""
    customer_list.click_new()
    detail = CustomerDetailPage(authed_page)
    detail.wait_loaded()

    unique = str(int(time.time()))
    detail.fill(CustomerInput(
        first_name="QA-Demo",
        last_name=f"Tester{unique[-4:]}",
        company="StratusQA",
        club_id=f"QA{unique[-4:]}",
    ))
    detail.click_save()
    detail.assert_no_errors()


@pytest.mark.demo
def test_edit_existing_customer(authed_page: Page, customer_list: CustomerListPage):
    """Search → select row → Edit → modify → Save."""
    customer_list.search_by_last_name("SMITH")
    if customer_list.row_count() == 0:
        pytest.skip("No customers with last name SMITH exist in this environment")

    customer_list.click_row(0)
    customer_list.click_edit()

    detail = CustomerDetailPage(authed_page)
    detail.wait_loaded()
    detail.fill(CustomerInput(first_name=f"Updated-{int(time.time()) % 10000}"))
    detail.click_save()
    detail.assert_no_errors()


@pytest.mark.demo
def test_print_list(customer_list: CustomerListPage):
    """Print List action triggers a print job without crashing."""
    customer_list.search_by_last_name("SMITH")
    customer_list.click_print_list()
    customer_list.page.wait_for_load_state("networkidle")


@pytest.mark.demo
def test_close_returns_to_main(customer_list: CustomerListPage):
    """Close exits the Customer List screen."""
    customer_list.click_close()
    customer_list.page.wait_for_load_state("networkidle")
    # After close, the New button should no longer be in the DOM as the
    # Customer List one (the user is back on a different screen).
    btn = customer_list.locator(CustomerListPage.BTN_NEW)
    assert (not btn.is_visible()) or ("CustomerList" not in customer_list.page.url)
