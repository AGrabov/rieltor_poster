from __future__ import annotations

import time

from playwright.sync_api import Locator

from setup_logger import setup_logger
from models.rieltor_dataclasses import Offer

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

    def _force_reselect_house_number(self, sec: Locator, desired: str | None) -> None:
        """
        Делает то же, что ты руками:
            - клик в house_number
            - повторно выбрать значение из autocomplete (force=True)
        """
        house = (desired or "").strip()
        if not house:
            # если desired не задан — берём текущее значение из поля
            try:
                label = self._expected_label("house_number") or "house_number"
                ctrl = self._find_control_by_label(sec, label)
                if ctrl:
                    inp = ctrl.locator("css=input").first if ctrl.locator("css=input").count() else ctrl
                    house = (inp.input_value() or "").strip()
            except Exception:
                house = ""

        if not house:
            logger.warning("Cannot reselect house_number: empty")
            return

        logger.warning("Reselect house_number='%s' to snap map pin to a building", house)
        self._fill_autocomplete(sec, "house_number", house, force=True)

        # часто ошибка появляется/исчезает с задержкой — ждём стабилизации
        self._wait_map_error_state(want_visible=False, timeout_ms=5000)

    def _fill_address(self, root: Locator, offer: Offer) -> None:
        sec = self._section(root, "Адреса об'єкта")
        a = offer.address
        if a is None:
            logger.warning("Offer has no address, skip")
            return

        # normalize
        try:
            if getattr(a, "street", None):
                s = a.street.strip()
                if s.startswith("вул.") or s.startswith("вулиця "):
                    s = s.replace("вул.", "").replace("вулиця ", "").strip()
                a.street = s

            if getattr(a, "condo_complex", None):
                cc = a.condo_complex.strip()
                if cc.startswith("ЖК "):
                    cc = cc.replace("ЖК ", "").strip()
                a.condo_complex = cc
        except Exception:
            pass

        logger.info(
            "Fill address: city=%s, condo=%s, district=%s, street=%s, house=%s",
            getattr(a, "city", None),
            getattr(a, "condo_complex", None),
            getattr(a, "district", None),
            getattr(a, "street", None),
            getattr(a, "house_number", None),
        )

        # 0) CITY
        if getattr(a, "city", None):
            next_key = "condo_complex" if getattr(a, "condo_complex", None) else "district"
            self._fill_autocomplete(sec, "city", a.city, next_key=next_key)

        # 1) CONDO COMPLEX
        condo_used = False
        if getattr(a, "condo_complex", None):
            self._fill_autocomplete(sec, "condo_complex", a.condo_complex)
            condo_used = True
            # даём UI дорисовать карту/ошибку
            try:
                self.page.wait_for_timeout(600)
            except Exception:
                time.sleep(0.6)

        # 2) REGION
        if getattr(a, "region", None):
            self._fill_autocomplete(sec, "region", a.region)

        # 3) DISTRICT
        district_label = self._expected_label("district") or "district"
        district_ctrl = self._find_control_by_label(sec, district_label)
        if district_ctrl and not self._control_has_value(district_ctrl):
            if getattr(a, "district", None):
                self._fill_autocomplete(sec, "district", a.district, next_key="street")

        # 4) STREET (если не автозаполнилось)
        street_label = self._expected_label("street") or "street"
        street_ctrl = self._find_control_by_label(sec, street_label)
        if street_ctrl and not self._control_has_value(street_ctrl):
            if getattr(a, "street", None):
                self._fill_autocomplete(sec, "street", a.street, next_key="house_number")

        # 5) HOUSE NUMBER (если не автозаполнилось)
        house_label = self._expected_label("house_number") or "house_number"
        house_ctrl = self._find_control_by_label(sec, house_label)
        if house_ctrl and not self._control_has_value(house_ctrl):
            if getattr(a, "house_number", None):
                self._fill_autocomplete(sec, "house_number", a.house_number)

        # === КЛЮЧЕВОЕ: если использовали ЖК — форсим повторный выбор house_number (как руками) ===
        # (даже если ошибка ещё не “успела” отрисоваться)
        if condo_used:
            self._force_reselect_house_number(sec, getattr(a, "house_number", None))
            # если ошибка всё ещё есть — ещё раз (иногда с первого раза список не успевает)
            if self._map_error_visible():
                self._force_reselect_house_number(sec, getattr(a, "house_number", None))

        # 6) multi
        if getattr(a, "subway", None):
            self._fill_autocomplete_multi(sec, "subway", list(a.subway))
        if getattr(a, "guide", None):
            self._fill_autocomplete_multi(sec, "guide", list(a.guide))
