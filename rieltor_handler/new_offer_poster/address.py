from __future__ import annotations

import re
import time

from playwright.sync_api import Locator

from setup_logger import setup_logger

logger = setup_logger(__name__)

# Кадастровий номер України: XXXXXXXXXX:XX:XXX:XXXX
_CADNUM_RE = re.compile(r"^\d{10}:\d{2}:\d{3}:\d{4}$")
# Селектори поля кадастрового номера (порядок = пріоритет пошуку)
_CADASTRAL_SELECTORS = (
    "css=input[name='cadastralNumber']",
    "css=input[id*='cadastral' i]",
    "css=input[placeholder*='кадастр' i]",
    "css=input[aria-label*='кадастр' i]",
)


class AddressMixin:
    MAP_ERR_SUBSTR = "Мітка не вказує"
    MAP_WRONG_CITY_SUBSTR = "іншому місті"

    def _find_cadastral_input(self, sec: Locator | None = None) -> Locator | None:
        """Знайти input кадастрового номера: спочатку у секції адреси, потім на сторінці."""
        scopes = []
        if sec is not None:
            scopes.append(sec)
        scopes.append(self.page)
        for scope in scopes:
            for sel in _CADASTRAL_SELECTORS:
                candidate = scope.locator(sel).first
                try:
                    if candidate.count():
                        return candidate
                except Exception:
                    continue
        return None

    def _fill_cadastral(self, sec: Locator | None, cadastral: str) -> bool:
        """Заповнити поле кадастрового номера з верифікацією та одним повтором.

        Повертає True, якщо після заповнення значення дійсно стоїть у полі.
        Раніше заповнення робилось одним ``fill()`` без перевірки — якщо сайт
        скидав поле під час каскаду адреси, значення тихо губилось і валідація
        видавала «Необхідно заповнити поле», хоча кадастр був у даних.
        """
        cadnum_str = str(cadastral or "").strip()
        if not cadnum_str:
            return False
        if not _CADNUM_RE.match(cadnum_str):
            logger.warning(
                "Кадастровий номер '%s' не відповідає формату XXXXXXXXXX:XX:XXX:XXXX — пропуск",
                cadnum_str,
            )
            return False

        for attempt in range(1, 3):
            inp = self._find_cadastral_input(sec)
            if inp is None:
                logger.warning("Поле 'cadastralNumber' не знайдено (спроба %s/2)", attempt)
                return False
            try:
                inp.click()
                inp.fill(cadnum_str)
            except Exception:
                logger.exception("Помилка заповнення кадастрового номера '%s'", cadnum_str)
                return False

            try:
                current = (inp.input_value() or "").strip()
            except Exception:
                current = cadnum_str  # не змогли прочитати — вважаємо, що поставилось

            if current == cadnum_str:
                logger.info("Кадастровий номер заповнено: %s", cadnum_str)
                return True

            logger.warning(
                "Кадастровий номер не зафіксувався (поле='%s', очікувалось='%s') — повтор",
                current,
                cadnum_str,
            )
            try:
                self.page.wait_for_timeout(400)
            except Exception:
                time.sleep(0.4)

        logger.warning("Не вдалось зафіксувати кадастровий номер '%s' після повторів", cadnum_str)
        return False

    def _map_error_locator(self) -> Locator:
        """
        ВАЖЛИВО: помилка знаходиться у більш зовнішньому MuiBox-root, ніж найближчий ancestor карти.
        Тому:
            - беремо .mapboxgl-map
            - піднімаємось до такого MuiBox-root, який вже містить Mui-error
            - і шукаємо помилку всередині нього
        """
        mapbox = self.page.locator("css=.mapboxgl-map").first
        if mapbox.count() == 0:
            return self.page.locator("css=.MuiFormHelperText-root.Mui-error", has_text=self.MAP_ERR_SUBSTR).first

        container = mapbox.locator(
            "xpath=ancestor::div[contains(@class,'MuiBox-root') and "
            ".//*[contains(@class,'MuiFormHelperText-root') and contains(@class,'Mui-error')]][1]"
        ).first

        # если почему-то не нашли такой ancestor — fallback на просто внешний MuiBox-root
        if container.count() == 0:
            container = mapbox.locator("xpath=ancestor::div[contains(@class,'MuiBox-root')][1]").first

        return container.locator("css=.MuiFormHelperText-root.Mui-error", has_text=self.MAP_ERR_SUBSTR).first

    def _map_error_visible(self) -> bool:
        try:
            err = self._map_error_locator()
            if err.count() == 0:
                return False
            try:
                return err.is_visible()
            except Exception:
                return True
        except Exception:
            return False

    def _wait_map_error_state(self, *, want_visible: bool, timeout_ms: int = 5000) -> bool:
        deadline = time.time() + timeout_ms / 1000.0
        while time.time() < deadline:
            vis = self._map_error_visible()
            if vis == want_visible:
                return True
            try:
                self.page.wait_for_timeout(200)
            except Exception:
                time.sleep(0.2)
        return self._map_error_visible() == want_visible

    def _force_reselect_house_number(self, sec: Locator, desired: str | None, house_label: str = "Будинок") -> None:
        """Стабільна фіксація піна на карті.

        Те, що працює вручну: повторно вибрати номер будинку з autocomplete.
        Іноді UI не встигає обробити вибір з першого разу, тому робимо кілька спроб
        і чекаємо зникнення помилки карти.
        """
        house = (desired or "").strip()
        if not house:
            try:
                ctrl = self._find_control_by_label(sec, house_label)
                if ctrl:
                    inp = ctrl.locator("css=input").first if ctrl.locator("css=input").count() else ctrl
                    house = (inp.input_value() or "").strip()
            except Exception:
                house = ""

        if not house:
            logger.warning("Неможливо повторно вибрати номер будинку: значення порожнє")
            return

        for attempt in range(1, 4):
            logger.warning(
                "Повторний вибір номера будинку='%s' для прив'язки піна карти до будівлі (спроба %s/3)",
                house,
                attempt,
            )

            self._fill_autocomplete(sec, house_label, house, force=True)

            if self._wait_map_error_state(want_visible=False, timeout_ms=9000):
                return

            # иногда помогает клик по маркеру/карте, чтобы пересчитать здание
            try:
                marker = self.page.locator("css=.mapboxgl-marker[aria-label='Map marker'], .mapboxgl-marker").first
                if marker.count():
                    marker.click(force=True)
            except Exception:
                pass

            self._wait_map_error_state(want_visible=False, timeout_ms=7000)
            if not self._map_error_visible():
                return

            try:
                self.page.wait_for_timeout(400)
            except Exception:
                time.sleep(0.4)
