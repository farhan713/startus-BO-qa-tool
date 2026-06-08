"""API-layer smoke tests against the Stratus servlet stack."""
from __future__ import annotations

import pytest

from framework.api import StratusApiClient


@pytest.mark.smoke
@pytest.mark.api
def test_server_is_reachable(api: StratusApiClient) -> None:
    """The backoffice container responds to login.jsp."""
    assert api.ping(), "Stratus BackOffice is not reachable — is the server up?"


@pytest.mark.smoke
@pytest.mark.api
def test_valid_login_returns_redirect_or_ok(api: StratusApiClient) -> None:
    """Real credentials get accepted by UserAuthenticationServlet."""
    resp = api.login()
    assert resp.status_code in (200, 302), (
        f"Expected 200/302 on auth, got {resp.status_code}. Body: {resp.text[:300]}"
    )
    assert api.is_authenticated


@pytest.mark.smoke
@pytest.mark.api
def test_invalid_credentials_do_not_authenticate(api: StratusApiClient) -> None:
    """Bad credentials must not pass."""
    from config import settings
    resp = api.login(
        user=settings.creds.invalid_user,
        password=settings.creds.invalid_password,
    )
    # Auth servlet typically redirects back to login.jsp?msg=... on failure.
    body = resp.text.lower()
    failed = (
        resp.status_code in (302,) and "login.jsp" in resp.headers.get("Location", "").lower()
    ) or ("login" in body and "error" in body)
    assert failed, f"Invalid creds unexpectedly succeeded (status={resp.status_code})"
