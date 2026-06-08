"""Page object for /backoffice/login.jsp.

Selectors map to the stable IDs present in the JSP form
(see WebContent/login.jsp).
"""
from __future__ import annotations

from playwright.sync_api import Page

from config import settings
from framework.ui.base_page import BasePage


class LoginPage(BasePage):
    path = settings.app.login_path

    USERID = "#userid"
    PASSWORD = "#passwd"
    SUBMIT = "#btnLogin"
    CLEAR = "#btnCancel"
    ERROR_MSG = "#errMsg"

    def __init__(self, page: Page) -> None:
        super().__init__(page)

    # ---------------------------------------------------------------- waits

    def wait_loaded(self) -> "LoginPage":
        self.wait_for_visible(self.USERID)
        self.wait_for_visible(self.PASSWORD)
        self.wait_for_visible(self.SUBMIT)
        return self

    # --------------------------------------------------------------- actions

    def fill_credentials(self, user: str, password: str) -> "LoginPage":
        self.locator(self.USERID).fill(user)
        self.locator(self.PASSWORD).fill(password)
        return self

    def submit(self) -> None:
        self.locator(self.SUBMIT).click()

    def login(self, user: str | None = None, password: str | None = None) -> None:
        self.fill_credentials(
            user or settings.creds.user,
            password or settings.creds.password,
        )
        self.submit()

    # ------------------------------------------------------------- assertions

    def error_text(self) -> str:
        loc = self.locator(self.ERROR_MSG)
        return loc.inner_text().strip() if loc.is_visible() else ""

    def is_on_login_page(self) -> bool:
        return self.locator(self.USERID).is_visible()
