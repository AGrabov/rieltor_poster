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


class RieltorErrorPageException(Exception):
    """Виникає, коли сайт повертає сторінку з помилкою."""

    pass


@dataclass(frozen=True)
class RieltorCredentials:
    phone: str
    password: str


class RieltorSession:
    """Обгортка над Playwright-сесією.

    Відповідальності:
      - запуск/зупинка Playwright
      - створення browser/context/page
      - авторизація

    Утримує код заповнення форм ізольованим в окремому класі.
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

        # if debug:
        #     logger.setLevel("DEBUG")
        self.debug = debug

        self._p: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

    def __enter__(self) -> "RieltorSession":
        self._p = sync_playwright().start()
        self._browser = self._p.chromium.launch(
            headless=self.headless, slow_mo=self.slow_mo_ms
        )
        self._context = self._browser.new_context()
        self.page = self._context.new_page()
        self.page.set_default_timeout(self.default_timeout_ms)
        logger.debug("Playwright запущено")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.debug:
            logger.debug("Збереження HTML сторінки...")
            with open("offer_page.html", "w", encoding="utf-8") as f:
                f.write(self.page.content())
            logger.info("HTML сторінки збережено у offer_page.html")
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
        logger.debug("Playwright зупинено")

    def close_popup(self) -> None:
        # Ждём появления диалога
        self.page.wait_for_selector('div[role="dialog"]')
        logger.debug("Спливаюче вікно відкрито")

        # Кликаем по svg-крестику внутри диалога
        self.page.click('div[role="dialog"] svg.MuiSvgIcon-root')

        # Проверяем, что диалог закрылся
        self.page.wait_for_selector('div[role="dialog"]', state="detached")
        logger.info("Спливаюче вікно успішно закрито")

    def is_error_page(self) -> bool:
        """Перевіряє, чи поточна сторінка є сторінкою помилки rieltor.ua."""
        if not self.page:
            return False
        # Check for error page indicators: "Щось пішло не так" text or 404 bot image
        error_text = self.page.locator('text="Щось пішло не так"')
        error_bot = self.page.locator('img[alt="404 bot"]')
        return error_text.count() > 0 or error_bot.count() > 0

    def check_for_error_page(self, raise_exception: bool = True) -> bool:
        """Перевіряє, чи поточна сторінка є сторінкою помилки, і за потреби піднімає виняток.

        Args:
            raise_exception: Якщо True, піднімає RieltorErrorPageException при виявленні сторінки помилки.

        Returns:
            True якщо виявлено сторінку помилки, False — інакше.

        Raises:
            RieltorErrorPageException: Якщо виявлено сторінку помилки і raise_exception=True.
        """
        if self.is_error_page():
            current_url = self.page.url if self.page else "unknown"
            logger.error(f"Виявлено сторінку помилки за адресою: {current_url}")
            if raise_exception:
                raise RieltorErrorPageException(
                    f"Сайт повернув сторінку помилки за адресою {current_url}. "
                    "Сторінка може бути недоступна або сесія закінчилась."
                )
            return True
        return False

    def navigate_with_error_check(
        self, url: str, wait_until: str = "domcontentloaded"
    ) -> None:
        """Перехід за URL з перевіркою на сторінку помилки.

        Args:
            url: URL для переходу.
            wait_until: Коли вважати навігацію завершеною.

        Raises:
            RieltorErrorPageException: Якщо навігація призвела до сторінки помилки.
            RuntimeError: Якщо сесію не розпочато.
        """
        if not self.page:
            raise RuntimeError("Session not started")
        logger.debug(f"Перехід до: {url}")
        self.page.goto(url, wait_until=wait_until)
        self.check_for_error_page()

    def login(self) -> None:
        """Авторизація за номером телефону та паролем.

        Raises:
            RieltorErrorPageException: Якщо після входу відкрилась сторінка помилки.
            RuntimeError: Якщо сесію не розпочато.
        """
        if not self.page:
            raise RuntimeError("Session not started")

        p = self.page
        logger.info("Перехід до сторінки входу")
        p.goto(self.LOGIN_URL, wait_until="domcontentloaded")
        self.check_for_error_page()

        p.fill(
            "input[name='phone']",
            self.creds.phone
            if not self.creds.phone.startswith("+380")
            else self.creds.phone.lstrip("+380"),
        )
        p.fill("input[name='password']", self.creds.password)
        p.click("button[type='submit']")
        p.wait_for_load_state("networkidle")

        # Check for error page after login
        self.check_for_error_page()

        # check for any pop-up window and close it
        if p.locator('div[role="dialog"]').count() > 0:
            try:
                self.close_popup()
            except Exception:
                pass
        logger.info("Авторизація завершена")
