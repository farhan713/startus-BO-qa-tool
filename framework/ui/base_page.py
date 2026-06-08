"""Page-Object base class. Every concrete page inherits from this."""
from __future__ import annotations

from playwright.sync_api import Locator, Page

from config import settings
from framework.utils import get_logger

log = get_logger("ui")


class BasePage:
    path: str = ""

    def __init__(self, page: Page) -> None:
        self.page = page
        self.page.set_default_timeout(settings.browser.timeout_ms)

    @property
    def url(self) -> str:
        return f"{settings.app.base_url.rstrip('/')}{self.path}"

    def open(self) -> "BasePage":
        log.info("opening %s", self.url)
        self.page.goto(self.url, wait_until="domcontentloaded")
        return self

    def locator(self, selector: str) -> Locator:
        return self.page.locator(selector)

    def wait_for_visible(self, selector: str, timeout_ms: int | None = None) -> Locator:
        loc = self.locator(selector)
        loc.wait_for(state="visible", timeout=timeout_ms)
        return loc

    def screenshot(self, name: str) -> bytes:
        path = settings.reports_dir / "screenshots" / f"{name}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        return self.page.screenshot(path=str(path), full_page=True)
