"""CRM session wrapper (Playwright-based).

Connects to crm-primes.realtsoft.net, handles login with email/password,
and provides page navigation with error checking.

Usage:
    creds = CrmCredentials(email="...", password="...")
    with CrmSession(creds) as crm:
        crm.login()
        crm.page.goto(...)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright

from setup_logger import setup_logger

logger = setup_logger(__name__)


@dataclass(frozen=True)
class CrmCredentials:
    email: str
    password: str


class CrmSession:
    """Playwright session wrapper for CRM (realtsoft.net).

    Responsibilities:
      - start/stop Playwright
      - create browser/context/page
      - login with email/password
      - navigate with error checking
    """

    BASE_URL = "https://crm-primes.realtsoft.net"
    LOGIN_URL = "https://crm-primes.realtsoft.net/login"

    def __init__(
        self,
        creds: CrmCredentials,
        headless: bool = False,
        slow_mo_ms: int = 0,
        default_timeout_ms: int = 30_000,
        debug: bool = False,
    ) -> None:
        self.creds = creds
        self.headless = headless
        self.slow_mo_ms = slow_mo_ms
        self.default_timeout_ms = default_timeout_ms
        self.debug = debug

        if debug:
            logger.setLevel("DEBUG")

        self._p: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

    def __enter__(self) -> "CrmSession":
        self._p = sync_playwright().start()
        self._browser = self._p.chromium.launch(
            headless=self.headless, slow_mo=self.slow_mo_ms,
        )
        self._context = self._browser.new_context()
        self.page = self._context.new_page()
        self.page.set_default_timeout(self.default_timeout_ms)
        logger.debug("Playwright started (CRM session)")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
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
        logger.debug("Playwright stopped (CRM session)")

    def login(self) -> None:
        """Login to CRM with email/password.

        Raises:
            RuntimeError: If session not started or login failed.
        """
        if not self.page:
            raise RuntimeError("CRM session not started")

        p = self.page
        logger.info("Navigating to CRM login: %s", self.LOGIN_URL)
        p.goto(self.LOGIN_URL, wait_until="domcontentloaded")

        # Fill login form (email + password)
        p.fill("input[name='LoginForm[username]']", self.creds.email)
        p.fill("input[name='LoginForm[password]']", self.creds.password)
        p.click("button[type='submit']")
        p.wait_for_load_state("networkidle")

        # Verify login succeeded — should redirect away from login page
        if "/login" in (p.url or ""):
            raise RuntimeError(
                "CRM login failed — still on login page. "
                "Check email/password credentials."
            )

        logger.info("CRM login complete, url: %s", p.url)

    def navigate(self, path: str, wait_until: str = "domcontentloaded") -> None:
        """Navigate to a CRM page by path.

        Args:
            path: Relative path (e.g. "/estate/index") or full URL.
            wait_until: When to consider navigation complete.

        Raises:
            RuntimeError: If session not started.
        """
        if not self.page:
            raise RuntimeError("CRM session not started")

        url = path if path.startswith("http") else f"{self.BASE_URL}{path}"
        logger.debug("Navigating to: %s", url)
        self.page.goto(url, wait_until=wait_until)
