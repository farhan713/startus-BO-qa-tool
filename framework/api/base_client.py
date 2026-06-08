"""Thin wrapper around ``requests`` with logging + session reuse.

Subclasses (``StratusApiClient``) layer the Stratus-specific dispatch
semantics on top of this.
"""
from __future__ import annotations

from typing import Any, Mapping

import requests

from framework.utils import get_logger

log = get_logger("api")


class BaseApiClient:
    def __init__(self, base_url: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

    def _url(self, path: str) -> str:
        if path.startswith("http"):
            return path
        return f"{self.base_url}{path if path.startswith('/') else '/' + path}"

    def get(self, path: str, **kw) -> requests.Response:
        url = self._url(path)
        log.info("GET  %s", url)
        r = self.session.get(url, timeout=self.timeout, **kw)
        log.info("  -> %s (%d bytes)", r.status_code, len(r.content))
        return r

    def post(
        self,
        path: str,
        data: Mapping[str, Any] | None = None,
        json: Any = None,
        **kw,
    ) -> requests.Response:
        url = self._url(path)
        log.info("POST %s", url)
        r = self.session.post(url, data=data, json=json, timeout=self.timeout, **kw)
        log.info("  -> %s (%d bytes)", r.status_code, len(r.content))
        return r

    def close(self) -> None:
        self.session.close()
