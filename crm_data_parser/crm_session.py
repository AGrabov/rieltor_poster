"""Обгортка сесії CRM на основі Playwright.

Підключається до crm-primes.realtsoft.net, виконує вхід через email/пароль
та забезпечує навігацію сторінками з перевіркою помилок.

Використання:
    creds = CrmCredentials(email="...", password="...")
    with CrmSession(creds) as crm:
        crm.login()
        crm.page.goto(...)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    sync_playwright,
)

from setup_logger import setup_logger

logger = setup_logger(__name__)


@dataclass(frozen=True)
class CrmCredentials:
    email: str
    password: str


class CrmSession:
    """Обгортка сесії Playwright для CRM (realtsoft.net).

    Відповідальності:
      - запуск/зупинка Playwright
      - створення browser/context/page
      - вхід через email/пароль
      - навігація з перевіркою помилок
    """

    BASE_URL = "https://crm-capital.realtsoft.net"
    LOGIN_URL = "https://crm-capital.realtsoft.net/login"

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

        self._p: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self.page: Page | None = None

    def __enter__(self) -> CrmSession:
        self._p = sync_playwright().start()
        self._browser = self._p.chromium.launch(
            headless=self.headless,
            slow_mo=self.slow_mo_ms,
        )
        self._context = self._browser.new_context()
        self.page = self._context.new_page()
        self.page.set_default_timeout(self.default_timeout_ms)
        logger.debug("Playwright запущено (CRM сесія)")
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
        logger.debug("Playwright зупинено (CRM сесія)")

    def login(self) -> None:
        """Виконати вхід до CRM через email/пароль.

        Raises:
            RuntimeError: Якщо сесію не запущено або вхід не вдався.
        """
        if not self.page:
            raise RuntimeError("CRM session not started")

        p = self.page
        logger.info("Перехід до сторінки входу CRM: %s", self.LOGIN_URL)
        p.goto(self.LOGIN_URL, wait_until="domcontentloaded")

        # Fill login form (email + password)
        p.fill("input[name='LoginForm[username]']", self.creds.email)
        p.fill("input[name='LoginForm[password]']", self.creds.password)
        p.click("button[type='submit']")
        p.wait_for_load_state("networkidle")

        # Verify login succeeded — should redirect away from login page
        if "/login" in (p.url or ""):
            raise RuntimeError("CRM login failed — still on login page. Check email/password credentials.")

        logger.info("Вхід до CRM виконано, url: %s", p.url)

    def navigate(self, path: str, wait_until: str = "domcontentloaded") -> None:
        """Перейти до сторінки CRM за шляхом.

        Args:
            path: Відносний шлях (напр. "/estate/index") або повний URL.
            wait_until: Коли вважати навігацію завершеною.

        Raises:
            RuntimeError: Якщо сесію не запущено.
        """
        if not self.page:
            raise RuntimeError("CRM session not started")

        url = path if path.startswith("http") else f"{self.BASE_URL}{path}"
        logger.debug("Перехід до: %s", url)
        self.page.goto(url, wait_until=wait_until)
