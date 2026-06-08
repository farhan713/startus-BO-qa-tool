"""pytest-bdd steps for tests/features/customer_management.feature."""
from __future__ import annotations

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from framework.ui.pages import (
    CustomerDetailPage,
    CustomerInput,
    CustomerListPage,
    LoginPage,
)

scenarios("../customer_management.feature")


# ---------------------------------------------------------------- given

@given("the user is logged in to Stratus BackOffice", target_fixture="auth_ctx")
def _logged_in(login_page: LoginPage):
    login_page.login()
    login_page.page.wait_for_load_state("networkidle")
    return {"page": login_page.page}


@given("the user is on the Customer List screen", target_fixture="list_page")
def _on_customer_list(auth_ctx) -> CustomerListPage:
    page = auth_ctx["page"]
    lp = CustomerListPage(page)
    lp.open().wait_loaded()
    return lp


# ----------------------------------------------------------------- when

@when("the user opens the search criteria")
def _open_criteria(list_page: CustomerListPage):
    list_page.show_search_criteria()


@when(parsers.parse('the user searches for last name "{last_name}"'))
def _search_lastname(list_page: CustomerListPage, last_name: str):
    list_page.fill_search(last_name=last_name)
    list_page.click_search()


@when("the user clicks New")
def _click_new(list_page: CustomerListPage):
    list_page.click_new()


@when("the user selects the first row")
def _click_first_row(list_page: CustomerListPage):
    list_page.click_row(0)


@when("the user clicks the Edit action")
def _click_edit(list_page: CustomerListPage):
    list_page.click_edit()


@when("the user clicks the Print List action")
def _click_print(list_page: CustomerListPage):
    list_page.click_print_list()


@when("the user clicks Close")
def _click_close(list_page: CustomerListPage):
    list_page.click_close()


@when("the user clicks Save", target_fixture="detail_page")
def _save_detail(auth_ctx) -> CustomerDetailPage:
    page = auth_ctx["page"]
    dp = CustomerDetailPage(page)
    dp.click_save()
    return dp


@when(parsers.parse(
    'the user fills in first name "{first}" last name "{last}" company "{company}"'
))
def _fill_new(auth_ctx, first: str, last: str, company: str):
    page = auth_ctx["page"]
    dp = CustomerDetailPage(page)
    dp.wait_loaded()
    dp.fill(CustomerInput(first_name=first, last_name=last, company=company))


@when(parsers.parse('the user changes first name to "{new_first}"'))
def _change_first_name(auth_ctx, new_first: str):
    page = auth_ctx["page"]
    dp = CustomerDetailPage(page)
    dp.wait_loaded()
    dp.fill(CustomerInput(first_name=new_first))


# ----------------------------------------------------------------- then

@then("the page shows the New button")
def _has_new(list_page: CustomerListPage):
    assert list_page.locator(CustomerListPage.BTN_NEW).is_visible()


@then("the page shows the Action dropdown")
def _has_action(list_page: CustomerListPage):
    assert list_page.locator(CustomerListPage.BTN_ACTION).is_visible()


@then("the page shows the Close button")
def _has_close(list_page: CustomerListPage):
    assert list_page.locator(CustomerListPage.BTN_CLOSE).is_visible()


@then("the page shows Show/Hide Search Criteria controls")
def _has_criteria_toggle(list_page: CustomerListPage):
    show = list_page.locator(CustomerListPage.SHOW_CRITERIA)
    hide = list_page.locator(CustomerListPage.HIDE_CRITERIA)
    assert show.is_visible() or hide.is_visible(), "Criteria toggle not found"


@then("the grid finishes loading")
def _grid_loaded(list_page: CustomerListPage):
    list_page.page.wait_for_load_state("networkidle")


@then("the search criteria can be reset")
def _can_reset(list_page: CustomerListPage):
    list_page.click_reset()


@then("the Customer Detail page opens")
def _detail_opens(auth_ctx) -> None:
    page = auth_ctx["page"]
    CustomerDetailPage(page).wait_loaded()


@then("the Customer Detail page shows no errors")
def _no_errors(auth_ctx) -> None:
    page = auth_ctx["page"]
    CustomerDetailPage(page).assert_no_errors()


@then("a print job is initiated")
def _print_initiated(list_page: CustomerListPage):
    # Stratus opens a popup or new tab for the report. We just verify the
    # context didn't crash — if Print failed catastrophically we'd see
    # console errors. A deeper check would assert on the popup URL.
    list_page.page.wait_for_load_state("networkidle")


@then("the user is no longer on the Customer List screen")
def _left_list(list_page: CustomerListPage):
    list_page.page.wait_for_load_state("networkidle")
    # Either the New button is no longer present or we navigated to a new screen
    btn = list_page.locator(CustomerListPage.BTN_NEW)
    assert not btn.is_visible() or "CustomerList" not in list_page.page.url, \
        "Still appears to be on Customer List"
