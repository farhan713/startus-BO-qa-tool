"""Stratus REST-style API client — no browser required.

Uses the same JSON dispatch endpoint (``/stratus``) the SPA uses, but
talks to it directly via Python ``requests``. Much faster and far more
reliable than driving the JavaScript SPA in a browser.

Authentication flow (verified against a real Stratus 209.208.39.72 install):

    GET  /backoffice/login.jsp?mid=<MID>          → set JSESSIONID cookie
    POST /backoffice/UserAuthenticationServlet.do → returns JSON envelope
         body (form-encoded):
            userid, passwd, apps=WRMS, machineid, ...
    POST /backoffice/stratus                       → all subsequent ops
         body (form-encoded): json=<URL-encoded JSON payload>

The JSON payload for screen actions looks like::

    {
      "action": "Search",
      "screenName": "CustomerList",
      "screenType": "1",
      "attributes": [{"functionName": "cfSeachCustomer"}, ...],
      "components": [{"componentName": "Last+Name", "values": "SMITH", ...}],
      "data": [{"pageNum": 1}]
    }
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse, parse_qsl

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

log = logging.getLogger("stratus_qa.api")


# ============================================================ data classes

@dataclass
class AuthResult:
    success: bool
    employee_id: str | None = None
    employee_name: str | None = None
    store_id: str | None = None
    store_name: str | None = None
    enterprise_modules: dict[str, str] = field(default_factory=dict)
    raw: dict | None = None
    message: str | None = None


@dataclass
class CustomerSearchResult:
    success: bool
    row_count: int
    rows: list[dict] = field(default_factory=list)
    total_count: str | None = None
    raw: dict | None = None
    message: str | None = None


# ============================================================ client

class StratusAPI:
    """Talks to Stratus directly over HTTP, no browser."""

    def __init__(self, base_url: str, machine_id: str = "100", verify_ssl: bool = False):
        # base_url may include ?mid=... — strip query, we handle it
        p = urlparse(base_url)
        scheme = p.scheme or "https"
        netloc = p.netloc
        path = p.path or "/"
        if not path.endswith("/"):
            path += "/"
        self.base_root = f"{scheme}://{netloc}{path.rstrip('/')}"   # e.g. https://host/backoffice

        # If user pasted ?mid=100 in URL, prefer that over the explicit machine_id arg
        qs = dict(parse_qsl(p.query))
        self.machine_id = qs.get("mid") or machine_id

        self.session = requests.Session()
        self.session.verify = verify_ssl
        self._auth: AuthResult | None = None

    # ----- low-level helpers -----

    def _url(self, path: str) -> str:
        return f"{self.base_root}/{path.lstrip('/')}"

    def _post_json(self, endpoint: str, payload: dict) -> dict:
        """POST a JSON payload to /stratus the way the SPA does:
        form-encoded with a single 'json' field."""
        url = self._url(endpoint)
        body = {"json": json.dumps(payload)}
        log.info("POST %s action=%s screen=%s", url,
                 payload.get("action"), payload.get("screenName"))
        r = self.session.post(
            url,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
            timeout=60,
        )
        log.info("  -> %s (%d bytes)", r.status_code, len(r.content))
        r.raise_for_status()
        try:
            return r.json()
        except Exception:
            return {"_raw_text": r.text, "_status_code": r.status_code}

    # ----- authentication -----

    def login(self, username: str, password: str) -> AuthResult:
        """Authenticate against Stratus. Sets a session cookie used by all
        subsequent calls."""
        # Step 1: GET login page to initialize JSESSIONID
        self.session.get(self._url(f"login.jsp?mid={self.machine_id}"), timeout=30)

        # Step 2: POST credentials
        form = {
            "apps": "WRMS",
            "userid": username,
            "passwd": password,
            "isChangedPasswrd": "N",
            "isClearSession": "N",
            "isSameUserLoginAllowed": "N",
            "verificationData": "",
            "changepasswrdconfirm": "N",
            "machineid": self.machine_id,
            "browserTZ": "GMT+0:0",
        }
        log.info("POST UserAuthenticationServlet.do user=%s", username)
        r = self.session.post(
            self._url("UserAuthenticationServlet.do"),
            data=form, timeout=30, allow_redirects=False,
        )
        try:
            resp = r.json()
        except Exception:
            return AuthResult(success=False, message=f"non-JSON response (HTTP {r.status_code})", raw=None)

        status = (resp.get("status") or "").upper()
        if status != "SUCCESS":
            return AuthResult(success=False, message=resp.get("message") or "auth failed", raw=resp)

        # Extract useful bits from the enterprise data payload
        first_data = (resp.get("data") or [{}])[0] if resp.get("data") else {}
        modules: dict[str, str] = {}
        for dt in (resp.get("dataTables") or []):
            if dt.get("tableName") == "ENTERPRISEDATA":
                for row in dt.get("dataRow") or []:
                    if "MODULE_NAME" in row:
                        modules[row["MODULE_NAME"]] = row.get("IS_USED", "")

        self._auth = AuthResult(
            success=True,
            employee_id=first_data.get("EMPLOYEE_ID"),
            employee_name=first_data.get("FULL_NAME"),
            store_id=first_data.get("STORE_ID"),
            store_name=first_data.get("STORE_NAME"),
            enterprise_modules=modules,
            raw=resp,
        )
        return self._auth

    @property
    def is_authenticated(self) -> bool:
        return self._auth is not None and self._auth.success

    # ----- customer operations -----

    def search_customers(
        self,
        last_name: str = "",
        first_name: str = "",
        email: str = "",
        company: str = "",
        page: int = 1,
    ) -> CustomerSearchResult:
        """Search the Customer List. Pass any combination of filters; blanks
        are skipped. Returns the matching customer rows."""
        components = []
        if last_name:
            components.append(_field("Last+Name", "CU.LAST_NAME", last_name))
        if first_name:
            components.append(_field("First+Name", "CU.FIRST_NAME", first_name))
        if email:
            components.append(_field("Email", "CUAD.email1", email))
        if company:
            components.append(_field("Company", "CU.COMPANY", company))

        payload = {
            "action": "Search",
            "application": None,
            "attributes": [
                {"functionName": "cfSeachCustomer"},
                {"funcFunctionType": "@funcFunctionType@"},
                {"contentOnly": False},
            ],
            "buttonType": "StandardBTN",
            "id": "-1",
            "message": None,
            "screenName": "CustomerList",
            "screenType": "1",
            "status": "SUCCESS",
            "targetScreenName": "MainScreen",
            "targetScreenType": "7",
            "dataTables": None,
            "components": components,
            "data": [{"pageNum": page}],
        }
        resp = self._post_json("stratus", payload)
        if resp.get("status", "").upper() != "SUCCESS":
            return CustomerSearchResult(
                success=False, row_count=0,
                message=resp.get("message"), raw=resp,
            )
        # Pull the CUSTOMERLIST table out
        rows: list[dict] = []
        for dt in resp.get("dataTables") or []:
            if (dt.get("tableName") or "").upper() == "CUSTOMERLIST":
                rows = dt.get("dataRow") or []
                break
        total = None
        if resp.get("data") and resp["data"][0].get("THE_COUNT"):
            total = resp["data"][0]["THE_COUNT"]
        return CustomerSearchResult(
            success=True, row_count=len(rows), rows=rows,
            total_count=total, raw=resp,
        )

    def get_customer(self, customer_id: int) -> dict | None:
        """Fetch a single customer by ID. Returns the row dict or None."""
        payload = {
            "action": "OpenDetail",
            "application": None,
            "attributes": [],
            "buttonType": "StandardBTN",
            "id": str(customer_id),
            "message": None,
            "screenName": "CustomerEditDtl",
            "screenType": "2",
            "status": "SUCCESS",
            "targetScreenName": "CustomerList",
            "targetScreenType": "1",
            "dataTables": None,
            "components": [],
            "data": [{"CUSTOMER_ID": customer_id}],
        }
        resp = self._post_json("stratus", payload)
        if resp.get("status", "").upper() != "SUCCESS":
            return None
        for dt in resp.get("dataTables") or []:
            if dt.get("dataRow"):
                return dt["dataRow"][0]
        if resp.get("data"):
            return resp["data"][0]
        return None

    # ----- generic dispatch (for screens we don't have typed methods for yet) -----

    def dispatch(self, screen_name: str, action: str, **kwargs) -> dict:
        """Generic call for any screen action.

        Example:
            api.dispatch("CustomerList", "Search", components=[...], data=[{"pageNum":1}])
        """
        payload = {
            "action": action,
            "application": None,
            "attributes": kwargs.pop("attributes", []),
            "buttonType": kwargs.pop("buttonType", "StandardBTN"),
            "id": str(kwargs.pop("id", "-1")),
            "message": None,
            "screenName": screen_name,
            "screenType": str(kwargs.pop("screenType", "1")),
            "status": "SUCCESS",
            "targetScreenName": kwargs.pop("targetScreenName", "MainScreen"),
            "targetScreenType": str(kwargs.pop("targetScreenType", "7")),
            "dataTables": kwargs.pop("dataTables", None),
            "components": kwargs.pop("components", []),
            "data": kwargs.pop("data", [{}]),
        }
        return self._post_json("stratus", payload)


# ============================================================ helpers

def _field(component_name: str, alias: str, value: str,
           ct: str = "CT1", dtype: str = "String") -> dict:
    """Build one of the 'components' entries that go in the JSON payload."""
    return {
        "componentName": component_name,
        "values": value,
        "dataComponentType": ct,
        "operator": "=",
        "dataType": dtype,
        "alias": alias,
        "hidden": "N",
        "useIn": "0",
        "groupBy": "",
        "parameterValues": {},
        "componentUsage": "",
        "dataMask": "",
    }
