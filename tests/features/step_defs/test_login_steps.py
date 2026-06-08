"""pytest-bdd step definitions for tests/features/login.feature."""
from __future__ import annotations

from pytest_bdd import given, scenarios, then, when

from config import settings
from framework.ui.pages import LoginPage

scenarios("../login.feature")


# ---------------------------------------------------------------------- given

@given("the user is on the Stratus BackOffice login page", target_fixture="page_obj")
def _open_login(login_page: LoginPage) -> LoginPage:
    assert login_page.is_on_login_page()
    return login_page


# ----------------------------------------------------------------------- when

@when("the user submits valid credentials")
def _submit_valid(page_obj: LoginPage) -> None:
    page_obj.login()


@when("the user submits invalid credentials")
def _submit_invalid(page_obj: LoginPage) -> None:
    page_obj.login(
        user=settings.creds.invalid_user,
        password=settings.creds.invalid_password,
    )


# ----------------------------------------------------------------------- then

@then("the user is taken away from the login page")
def _navigated_away(page_obj: LoginPage) -> None:
    page_obj.page.wait_for_load_state("networkidle")
    assert "login.jsp" not in page_obj.page.url.lower()


@then("the user remains on the login page or sees an error message")
def _stays_or_errors(page_obj: LoginPage) -> None:
    page_obj.page.wait_for_load_state("networkidle")
    url = page_obj.page.url.lower()
    assert "login.jsp" in url or page_obj.error_text()
