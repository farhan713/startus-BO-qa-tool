"""Page object for the Customer Entry/Edit Detail screen.

Maps to the IDs in
``WebContent/mv-assets/templates/detailscreens/customerentrydtl/``.
Handles the form fields + Save/Cancel.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from playwright.sync_api import Page

from framework.ui.base_page import BasePage
from framework.utils import get_logger

log = get_logger("ui.customer_detail")


@dataclass
class CustomerInput:
    """Plain-Python record of values to fill in the Customer Detail form."""
    first_name: str = ""
    middle_name: str = ""
    last_name: str = ""
    company: str = ""
    club_id: str = ""
    cust_type: Optional[str] = None    # dropdown — label, e.g. "Regular"


class CustomerDetailPage(BasePage):
    # Stratus opens this via the dispatcher when New / Edit is clicked.
    # For deep-linking in tests use:
    #   /stratus?screenType=CustomerEntryDtl       (new)
    #   /stratus?screenType=CustomerEditDtl&CUSTOMER_ID=<id>   (edit)
    path = "/stratus?screenType=CustomerEntryDtl"

    # ----------------------------------------------------------- form fields

    F_FIRST_NAME  = "#firstName"
    F_MIDDLE_NAME = "#middleName"
    F_LAST_NAME   = "#lastName"
    F_COMPANY     = "#company"
    F_CLUB_ID     = "#clubID"
    F_CUST_TYPE   = "#custType"
    FORM          = "#componentFormId"

    # ------------------------------------------------------------ buttons
    # Real Stratus has multiple #Save buttons (one is the taxonomy sub-form
    # save inside #add-taxonomy-form). Target the MAIN customer detail Save
    # button by its custom attributes.
    BTN_SAVE   = "button#Save[data-name='CustomBTN'], button#Save.btn-width-115:not(.btn-save)"
    BTN_CANCEL = "button#Cancel[data-name='CustomBTN'], button#Cancel.btn-width-115:not(.btn-save)"

    # ---------------------------------------------------------- error / msg

    ERROR_BANNER = ".alert-danger, #errorMsg, .error-message"

    def __init__(self, page: Page) -> None:
        super().__init__(page)

    # ============================================================ lifecycle

    def wait_loaded(self) -> "CustomerDetailPage":
        # firstName and Save both have multiple matches on real Stratus
        # (other sub-forms can have similar IDs). Use .first to be safe.
        try:
            self.locator(self.F_FIRST_NAME).first.wait_for(state="visible", timeout=15_000)
        except Exception:
            # SPA may take longer to render the detail screen
            import time as _t; _t.sleep(2)
            self.locator(self.F_FIRST_NAME).first.wait_for(state="visible", timeout=15_000)
        self.locator(self.BTN_SAVE).first.wait_for(state="visible", timeout=10_000)
        log.info("Customer Detail page is ready")
        return self

    # ============================================================ actions

    def fill(self, data: CustomerInput) -> "CustomerDetailPage":
        if data.first_name:
            log.info("  first_name = %r", data.first_name)
            self.locator(self.F_FIRST_NAME).first.fill(data.first_name)
        if data.middle_name:
            log.info("  middle_name = %r", data.middle_name)
            self.locator(self.F_MIDDLE_NAME).first.fill(data.middle_name)
        if data.last_name:
            log.info("  last_name = %r", data.last_name)
            self.locator(self.F_LAST_NAME).first.fill(data.last_name)
        if data.company:
            log.info("  company = %r", data.company)
            self.locator(self.F_COMPANY).first.fill(data.company)
        if data.club_id:
            log.info("  club_id = %r", data.club_id)
            self.locator(self.F_CLUB_ID).first.fill(data.club_id)
        if data.cust_type:
            log.info("  cust_type = %r", data.cust_type)
            self.locator(self.F_CUST_TYPE).first.select_option(label=data.cust_type)
        return self

    def click_save(self) -> "CustomerDetailPage":
        log.info("clicking Save")
        self.locator(self.BTN_SAVE).first.click()
        try:
            self.page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:
            pass
        return self

    def click_cancel(self) -> "CustomerDetailPage":
        log.info("clicking Cancel")
        self.locator(self.BTN_CANCEL).first.click()
        return self

    # ============================================================ assertions

    def read(self) -> CustomerInput:
        return CustomerInput(
            first_name=self.locator(self.F_FIRST_NAME).input_value(),
            middle_name=self.locator(self.F_MIDDLE_NAME).input_value(),
            last_name=self.locator(self.F_LAST_NAME).input_value(),
            company=self.locator(self.F_COMPANY).input_value(),
            club_id=self.locator(self.F_CLUB_ID).input_value(),
            cust_type=None,  # dropdowns: skip readback for the demo
        )

    def error_text(self) -> str:
        loc = self.locator(self.ERROR_BANNER)
        if loc.count() and loc.first.is_visible():
            return loc.first.inner_text().strip()
        return ""

    def assert_no_errors(self) -> "CustomerDetailPage":
        msg = self.error_text()
        assert not msg, f"Detail page reported error: {msg!r}"
        return self
