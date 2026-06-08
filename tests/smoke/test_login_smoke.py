"""UI-layer login smoke tests via Playwright."""
from __future__ import annotations

import pytest

from config import settings
from framework.ui.pages import LoginPage


@pytest.mark.smoke
@pytest.mark.ui
def test_login_page_loads(login_page: LoginPage) -> None:
    """The login form renders with all expected fields."""
    assert login_page.is_on_login_page()
    assert login_page.locator(LoginPage.USERID).is_editable()
    assert login_page.locator(LoginPage.PASSWORD).is_editable()
    assert login_page.locator(LoginPage.SUBMIT).is_enabled()


@pytest.mark.smoke
@pytest.mark.ui
def test_valid_login_leaves_login_page(login_page: LoginPage) -> None:
    """Submitting valid credentials navigates away from login.jsp."""
    login_page.login()
    login_page.page.wait_for_load_state("networkidle")
    assert "login.jsp" not in login_page.page.url.lower(), (
        f"Still on login page after valid login: {login_page.page.url}"
    )


@pytest.mark.smoke
@pytest.mark.ui
def test_invalid_login_shows_error_or_stays_on_login(login_page: LoginPage) -> None:
    """Bad credentials keep the user on login.jsp (or show an error)."""
    login_page.login(
        user=settings.creds.invalid_user,
        password=settings.creds.invalid_password,
    )
    login_page.page.wait_for_load_state("networkidle")
    url = login_page.page.url.lower()
    assert "login.jsp" in url or login_page.error_text(), (
        f"Invalid login appears to have succeeded: url={url}"
    )
