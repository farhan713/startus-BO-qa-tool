"""Central configuration loaded from .env / environment variables.

All test code reads config through ``settings`` — never via ``os.environ``
directly. This keeps the framework portable across DEV / UAT / CI without
code edits.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / ".env", override=False)


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "y"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw else default


@dataclass(frozen=True)
class AppConfig:
    base_url: str
    login_path: str
    auth_servlet: str
    dispatcher: str
    app_name: str

    @property
    def login_url(self) -> str:
        return f"{self.base_url.rstrip('/')}{self.login_path}"

    @property
    def auth_url(self) -> str:
        return f"{self.base_url.rstrip('/')}{self.auth_servlet}"

    @property
    def dispatcher_url(self) -> str:
        return f"{self.base_url.rstrip('/')}{self.dispatcher}"


@dataclass(frozen=True)
class CredentialConfig:
    user: str
    password: str
    invalid_user: str
    invalid_password: str


@dataclass(frozen=True)
class BrowserConfig:
    name: str
    headless: bool
    timeout_ms: int
    slow_mo_ms: int
    video_on_failure: bool
    screenshot_on_failure: bool


@dataclass(frozen=True)
class DbConfig:
    driver: str
    host: str
    port: int
    name: str
    user: str
    password: str
    trust_server_cert: str
    encrypt: str

    def odbc_conn_str(self) -> str:
        return (
            f"DRIVER={{{self.driver}}};"
            f"SERVER={self.host},{self.port};"
            f"DATABASE={self.name};"
            f"UID={self.user};"
            f"PWD={self.password};"
            f"Encrypt={self.encrypt};"
            f"TrustServerCertificate={self.trust_server_cert};"
        )


@dataclass(frozen=True)
class Settings:
    app: AppConfig
    creds: CredentialConfig
    browser: BrowserConfig
    db: DbConfig
    env_name: str
    run_id: str
    project_root: Path

    @property
    def reports_dir(self) -> Path:
        d = self.project_root / "reports"
        d.mkdir(parents=True, exist_ok=True)
        return d


def _load() -> Settings:
    return Settings(
        app=AppConfig(
            base_url=os.getenv("APP_BASE_URL", "http://localhost:8080/backoffice"),
            login_path=os.getenv("APP_LOGIN_PATH", "/login.jsp"),
            auth_servlet=os.getenv("APP_AUTH_SERVLET", "/UserAuthenticationServlet.do"),
            dispatcher=os.getenv("APP_DISPATCHER", "/stratus"),
            app_name=os.getenv("APP_NAME", "WRMS"),
        ),
        creds=CredentialConfig(
            user=os.getenv("TEST_USER", ""),
            password=os.getenv("TEST_PASSWORD", ""),
            invalid_user=os.getenv("TEST_INVALID_USER", "does_not_exist"),
            invalid_password=os.getenv("TEST_INVALID_PASSWORD", "wrong"),
        ),
        browser=BrowserConfig(
            name=os.getenv("BROWSER", "chromium"),
            headless=_bool("HEADLESS", True),
            timeout_ms=_int("BROWSER_TIMEOUT_MS", 30000),
            slow_mo_ms=_int("SLOW_MO_MS", 0),
            video_on_failure=_bool("VIDEO_ON_FAILURE", True),
            screenshot_on_failure=_bool("SCREENSHOT_ON_FAILURE", True),
        ),
        db=DbConfig(
            driver=os.getenv("DB_DRIVER", "ODBC Driver 18 for SQL Server"),
            host=os.getenv("DB_HOST", "localhost"),
            port=_int("DB_PORT", 1433),
            name=os.getenv("DB_NAME", "stratus"),
            user=os.getenv("DB_USER", "sa"),
            password=os.getenv("DB_PASSWORD", ""),
            trust_server_cert=os.getenv("DB_TRUST_SERVER_CERT", "yes"),
            encrypt=os.getenv("DB_ENCRYPT", "no"),
        ),
        env_name=os.getenv("ENV_NAME", "DEV"),
        run_id=os.getenv("RUN_ID", "local"),
        project_root=_ROOT,
    )


settings = _load()
