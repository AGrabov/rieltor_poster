from __future__ import annotations

import time

from playwright.sync_api import Locator

from setup_logger import setup_logger

logger = setup_logger(__name__)


class AddressMixin:
    MAP_ERR_SUBSTR = "Мітка не вказує"

    def _map_error_locator(self) -> Locator:
        """
        ВАЖНО: ошибка лежит в более внешнем MuiBox-root, чем ближайший ancestor карты.
        Поэтому:
            - берем .mapboxgl-map
            - поднимаемся к такому MuiBox-root, который УЖЕ содержит Mui-error
            - и ищем ошибку внутри него
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

    def _force_reselect_house_number(
        self, sec: Locator, desired: str | None, house_label: str = "Будинок"
    ) -> None:
        """Стабильная фиксация пина на карте.

        То, что работает руками: повторно выбрать номер дома из autocomplete.
        Иногда UI не успевает обработать выбор с первого раза, поэтому делаем несколько попыток
        и ждём исчезновения ошибки карты.
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
            logger.warning("Cannot reselect house_number: empty")
            return

        for attempt in range(1, 4):
            logger.warning("Reselect house_number='%s' to snap map pin to a building (attempt %s/3)", house, attempt)

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


