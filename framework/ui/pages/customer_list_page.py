"""Page object for the Customer List screen.

Maps to the stable IDs in
``WebContent/mv-assets/templates/listscreens/customerlist/``.
Covers the complete feature set: search, list grid, action buttons,
row-level actions, print, close.
"""
from __future__ import annotations

from typing import Iterable

from playwright.sync_api import Locator, Page

from config import settings
from framework.ui.base_page import BasePage
from framework.utils import get_logger

log = get_logger("ui.customer_list")


class CustomerListPage(BasePage):
    # The Stratus dispatcher uses screenType. URL stays under /stratus.
    path = "/stratus?screenType=CustomerList"

    # ------------------------------------------------------------ TOP NAV

    SHOW_CRITERIA = "#ShowCriteria"
    HIDE_CRITERIA = "#HideCriteria"
    # Real Stratus has 1000s of elements with id="New"/"Edit" (every grid
    # row contains one — bad HTML but reality). Scope to the top-nav and
    # action-menu containers so we always click the SCREEN-level buttons,
    # never per-row ones.
    BTN_NEW       = "#topNav #New, #New[screen='CustomerList']"
    BTN_ACTION    = "#topNav a.dropdown-toggle.action-btn, a.dropdown-toggle.action-btn"
    ACTION_MENU   = "#actionButtonMenu"
    BTN_EDIT      = "#actionButtonMenu #Edit, #actionButtonMenu button[id='Edit']"
    BTN_DELETE    = "#actionButtonMenu #Delete, #actionButtonMenu button[id='Delete']"
    BTN_PRINT     = "#actionButtonMenu #PrintList, #actionButtonMenu button[id='PrintList']"
    BTN_MERGE     = "#actionButtonMenu #mergeCustomer"
    BTN_CLOSE     = "#topNav #Close, #Close[screen='CustomerList']"

    # ----------------------------------------------------------- SEARCH FORM

    FORM           = "#componentFormId"
    F_LAST_NAME    = "#LastName"
    F_FIRST_NAME   = "#FirstName"
    F_MIDDLE_NAME  = "#MiddleName"
    F_EMAIL        = "#Email"
    F_COMPANY      = "#Company"
    F_PHONE        = "#Phone"
    F_CLUB_ID      = "#ClubID"
    F_ZIP          = "#Zip"
    F_STORE        = "#STORE"
    F_CUST_TYPE    = "#CUST_TYPE"
    F_CAT1         = "#CAT1"
    F_CAT2         = "#CAT2"
    F_DUPLICATES   = "#duplicatesCheck"

    # ----------------------------------------------------------- BOTTOM NAV

    BTN_SEARCH = "#Search"
    BTN_RESET  = "#Reset"

    # -------------------------------------------------------------- GRID

    GRID         = ".ui-jqgrid, #grid_table, table.ui-jqgrid-btable"
    GRID_ROW     = "tr.jqgrow"
    GRID_LOADING = ".loading, #load_grid_table"

    # ============================================================ lifecycle

    def __init__(self, page: Page) -> None:
        super().__init__(page)

    def wait_loaded(self) -> "CustomerListPage":
        """The list page renders the top nav with the New button as a tell."""
        # Use .first to avoid strict-mode violation when multiple #New exist
        # (real Stratus puts one per grid row).
        try:
            self.locator(self.BTN_NEW).first.wait_for(state="visible", timeout=settings.browser.timeout_ms)
        except Exception:
            # Fall back to bare #New for the mock server
            self.locator("#New").first.wait_for(state="visible", timeout=5000)
        log.info("Customer List page is ready")
        return self

    # ============================================================ search

    def show_search_criteria(self) -> "CustomerListPage":
        loc = self.locator(self.SHOW_CRITERIA)
        if loc.is_visible():
            log.info("clicking 'Show Search Criteria'")
            loc.click()
            self.wait_for_visible(self.F_LAST_NAME)
        return self

    def hide_search_criteria(self) -> "CustomerListPage":
        loc = self.locator(self.HIDE_CRITERIA)
        if loc.is_visible():
            log.info("clicking 'Hide Search Criteria'")
            loc.click()
        return self

    def fill_search(self, **criteria) -> "CustomerListPage":
        """Fill any of: last_name, first_name, middle_name, email, company,
        phone, club_id, zip, store, cust_type, cat1, cat2."""
        field_map = {
            "last_name":   self.F_LAST_NAME,
            "first_name":  self.F_FIRST_NAME,
            "middle_name": self.F_MIDDLE_NAME,
            "email":       self.F_EMAIL,
            "company":     self.F_COMPANY,
            "phone":       self.F_PHONE,
            "club_id":     self.F_CLUB_ID,
            "zip":         self.F_ZIP,
        }
        dropdown_map = {
            "store":     self.F_STORE,
            "cust_type": self.F_CUST_TYPE,
            "cat1":      self.F_CAT1,
            "cat2":      self.F_CAT2,
        }
        for k, v in criteria.items():
            if v is None or v == "":
                continue
            if k in field_map:
                log.info("  fill %s = %r", k, v)
                self.locator(field_map[k]).fill(str(v))
            elif k in dropdown_map:
                log.info("  select %s = %r", k, v)
                self.locator(dropdown_map[k]).select_option(label=str(v))
            else:
                raise KeyError(f"Unknown search field: {k}")
        return self

    def click_search(self) -> "CustomerListPage":
        log.info("clicking Search")
        self.locator(self.BTN_SEARCH).click()
        self._wait_for_grid()
        return self

    def click_reset(self) -> "CustomerListPage":
        log.info("clicking Reset")
        self.locator(self.BTN_RESET).click()
        return self

    def search_by_last_name(self, last_name: str) -> "CustomerListPage":
        """Convenience: show criteria → fill last name → search."""
        self.show_search_criteria()
        self.fill_search(last_name=last_name)
        self.click_search()
        return self

    # ============================================================ grid

    def _wait_for_grid(self, timeout_ms: int = 15_000) -> None:
        try:
            self.page.wait_for_selector(self.GRID, state="visible", timeout=timeout_ms)
        except Exception:
            log.warning("grid did not appear within %dms", timeout_ms)

    def row_count(self) -> int:
        rows = self.locator(self.GRID_ROW)
        return rows.count()

    def rows(self) -> Iterable[Locator]:
        rows = self.locator(self.GRID_ROW)
        for i in range(rows.count()):
            yield rows.nth(i)

    def click_row(self, index: int = 0) -> "CustomerListPage":
        """Click the Nth row to select it. Required before Edit/Delete."""
        rows = self.locator(self.GRID_ROW)
        if rows.count() == 0:
            raise AssertionError("No rows in the grid to click")
        log.info("clicking row %d", index)
        rows.nth(index).click()
        return self

    def double_click_row(self, index: int = 0) -> "CustomerListPage":
        rows = self.locator(self.GRID_ROW)
        if rows.count() == 0:
            raise AssertionError("No rows in the grid to double-click")
        log.info("double-clicking row %d", index)
        rows.nth(index).dblclick()
        return self

    # ============================================================ actions

    def click_new(self) -> "CustomerListPage":
        log.info("clicking New")
        self.locator(self.BTN_NEW).first.click()
        return self

    def _open_action_menu(self) -> None:
        log.info("opening Action dropdown")
        self.locator(self.BTN_ACTION).first.click()
        # ACTION_MENU may already be in DOM as hidden — wait for any of
        # its items to actually be visible.
        try:
            self.page.wait_for_selector(self.BTN_EDIT, state="visible", timeout=5000)
        except Exception:
            pass

    def click_edit(self) -> "CustomerListPage":
        self._open_action_menu()
        log.info("clicking Action -> Edit")
        self.locator(self.BTN_EDIT).first.click()
        return self

    def click_delete(self) -> "CustomerListPage":
        self._open_action_menu()
        log.info("clicking Action -> Delete")
        self.locator(self.BTN_DELETE).first.click()
        return self

    def click_print_list(self) -> "CustomerListPage":
        self._open_action_menu()
        log.info("clicking Action -> Print List")
        self.locator(self.BTN_PRINT).first.click()
        return self

    def click_close(self) -> "CustomerListPage":
        log.info("clicking Close")
        self.locator(self.BTN_CLOSE).first.click()
        return self

    # ============================================================ assertions

    def assert_loaded(self) -> "CustomerListPage":
        assert self.locator(self.BTN_NEW).is_visible(), "Customer List did not load"
        return self

    def assert_has_rows(self, minimum: int = 1) -> "CustomerListPage":
        n = self.row_count()
        assert n >= minimum, f"Expected >= {minimum} rows, got {n}"
        return self
