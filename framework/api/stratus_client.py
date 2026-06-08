"""Typed client for the Stratus servlet-dispatcher backend.

The backend doesn't expose a REST API. Instead, most operations POST JSON
to ``/stratus`` with a ``screenType`` discriminator that the
``StratusServlet`` routes to a Spring bean. This client encapsulates that
pattern so tests can stay readable.
"""
from __future__ import annotations

from typing import Any

import requests

from config import settings
from framework.utils import get_logger

from .base_client import BaseApiClient

log = get_logger("api.stratus")


class StratusApiClient(BaseApiClient):
    def __init__(self) -> None:
        super().__init__(settings.app.base_url)
        self._authenticated = False

    # ------------------------------------------------------------------ auth

    def login(self, user: str | None = None, password: str | None = None) -> requests.Response:
        """POST to UserAuthenticationServlet.do — mirrors the login.jsp form."""
        user = user or settings.creds.user
        password = password or settings.creds.password
        payload = {
            "userid": user,
            "passwd": password,
            "apps": settings.app.app_name,
            "isChangedPasswrd": "N",
            "isClearSession": "N",
            "isSameUserLoginAllowed": "N",
            "changepasswrdconfirm": "N",
        }
        # Prime the JSESSIONID cookie first by hitting login.jsp.
        self.get(settings.app.login_path)
        r = self.post(settings.app.auth_servlet, data=payload, allow_redirects=False)
        self._authenticated = r.status_code in (200, 302)
        return r

    @property
    def is_authenticated(self) -> bool:
        return self._authenticated

    # ------------------------------------------------------------ dispatcher

    def dispatch(self, screen_type: str, payload: dict[str, Any] | None = None) -> requests.Response:
        """POST to /stratus with a screenType discriminator.

        ``screen_type`` matches the Spring bean keys the StratusServlet
        uses for routing (e.g. ``consignmentIntake``, ``tenderProcessor``).
        """
        body: dict[str, Any] = {"screenType": screen_type}
        if payload:
            body.update(payload)
        return self.post(settings.app.dispatcher, json=body)

    # --------------------------------------------------------------- health

    def ping(self) -> bool:
        """Cheapest possible check that the server is up.

        Hits the login page (always served by the container) and returns
        True on any 2xx.
        """
        try:
            r = self.get(settings.app.login_path)
            return 200 <= r.status_code < 300
        except requests.RequestException as exc:
            log.warning("ping failed: %s", exc)
            return False
