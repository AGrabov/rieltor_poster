from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright

from setup_logger import setup_logger

logger = setup_logger(__name__)


@dataclass(frozen=True)
class RieltorCredentials:
    phone: str
    password: str


class RieltorSession:
    """Playwright session wrapper.

    Responsibilities:
      - start/stop Playwright
      - create browser/context/page
      - login

    This keeps the form-filling code isolated in a separate class.
    """

    LOGIN_URL = "https://my.rieltor.ua/login"

    def __init__(
        self,
        creds: RieltorCredentials,
        headless: bool = False,
        slow_mo_ms: int = 0,
        default_timeout_ms: int = 30_000,
        debug: bool = False,
    ) -> None:
        self.creds = creds
        self.headless = headless
        self.slow_mo_ms = slow_mo_ms
        self.default_timeout_ms = default_timeout_ms

        if debug:
            logger.setLevel("DEBUG")
        self.debug = debug

        self._p: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

    def __enter__(self) -> "RieltorSession":
        self._p = sync_playwright().start()
        self._browser = self._p.chromium.launch(headless=self.headless, slow_mo=self.slow_mo_ms)
        self._context = self._browser.new_context()
        self.page = self._context.new_page()
        self.page.set_default_timeout(self.default_timeout_ms)
        logger.debug("Playwright started")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.debug:
            logger.debug("Saving page html...")
            with open("offer_page.html", "w", encoding="utf-8") as f: f.write(self.page.content())
            logger.info("Page html saved to offer_page.html")
        try:
            if self._context:
                self._context.close()
        finally:
            try:
                if self._browser:
                    self._browser.close()
            finally:
                if self._p:
                    self._p.stop()
        logger.debug("Playwright stopped")

    def close_popup(self) -> None:
        # Ждём появления диалога
        self.page.wait_for_selector('div[role="dialog"]')
        logger.debug("Popup opened")

        # Кликаем по svg-крестику внутри диалога
        self.page.click('div[role="dialog"] svg.MuiSvgIcon-root')

        # Проверяем, что диалог закрылся
        self.page.wait_for_selector('div[role="dialog"]', state='detached')
        logger.info("Popup successfully closed")


    def login(self) -> None:
        """Login with phone/password."""
        if not self.page:
            raise RuntimeError("Session not started")

        p = self.page
        logger.info("Navigating to login")
        p.goto(self.LOGIN_URL, wait_until="domcontentloaded")
        p.fill("input[name='phone']", self.creds.phone if not self.creds.phone.startswith('+380') else self.creds.phone.lstrip("+380"))
        p.fill("input[name='password']", self.creds.password)
        p.click("button[type='submit']")
        p.wait_for_load_state("networkidle")
        # check for any pop-up window and close it
        if p.locator('div[role="dialog"]').count() > 0:
            try:
                self.close_popup()
            except Exception:
                pass
        logger.info("Login complete")
