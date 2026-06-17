from __future__ import annotations

from setup_logger import setup_logger

from .helpers import _cf, _norm, _xpath_literal

logger = setup_logger(__name__)


class _ParkingTypeMixin:
    """Мікін для вибору підтипу паркінгу (Гараж/Паркомісце)."""

    PARKING_TYPE_GARAGE = "Гараж"
    PARKING_TYPE_PARKING = "Паркомісце"

    def select_parking_type(self, parking_type: str) -> None:
        """
        Вибрати підтип паркінгу: 'Гараж' або 'Паркомісце'.

        Підтримує і стару розмітку (radiogroup), і нову (сайт замінив radio на
        MUI ToggleButtonGroup / кнопки).

        Args:
            parking_type: 'Гараж'/'garage' або 'Паркомісце'/'parking'
        """
        # Normalize parking type name
        parking_type_map = {
            "garage": self.PARKING_TYPE_GARAGE,
            "гараж": self.PARKING_TYPE_GARAGE,
            "parking": self.PARKING_TYPE_PARKING,
            "паркомісце": self.PARKING_TYPE_PARKING,
        }
        ui_text = parking_type_map.get(parking_type.lower(), parking_type)

        logger.info("Вибір типу паркінгу: %s", ui_text)
        root = self._root()

        if self._select_parking_in_radiogroup(root, ui_text):
            logger.debug("Тип паркінгу вибрано через radiogroup")
        elif self._select_parking_in_toggle_or_buttons(root, ui_text):
            logger.debug("Тип паркінгу вибрано через toggle/кнопки")
        else:
            logger.warning("Селектор типу паркінгу не знайдено (ні radiogroup, ні toggle/кнопки)")
            return

        self._wait_ready()
        try:
            self.page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:
            pass
        self.page.wait_for_timeout(self.ui_delay_ms + 450)

        self._epoch += 1
        self.open_all_blocks_sticky()
        logger.info("Тип паркінгу вибрано: %s (epoch=%s)", ui_text, self._epoch)

    def _select_parking_in_radiogroup(self, root, ui_text: str) -> bool:
        """Стара розмітка: radiogroup з мітками 'Гараж'/'Паркомісце'."""
        target_cf = _cf(ui_text)
        rgs = root.locator("xpath=.//*[@role='radiogroup']")
        for i in range(rgs.count()):
            rg = rgs.nth(i)
            labels = rg.locator("xpath=.//label[contains(@class,'MuiFormControlLabel-root')]")
            by_text: dict[str, object] = {}
            for j in range(labels.count()):
                lab = labels.nth(j)
                try:
                    txt = _norm(lab.locator("css=span.MuiFormControlLabel-label").inner_text() or "")
                except Exception:
                    txt = ""
                if txt:
                    by_text[_cf(txt)] = lab
            if _cf(self.PARKING_TYPE_GARAGE) in by_text and _cf(self.PARKING_TYPE_PARKING) in by_text:
                target = by_text.get(target_cf)
                if target is None:
                    return False
                try:
                    span = target.locator("css=span.MuiFormControlLabel-label").first
                    self._click_best_effort(span if span.count() else target)
                except Exception as e:
                    logger.warning("Не вдалося клікнути radio типу паркінгу: %s", e)
                    return False
                return True
        return False

    def _select_parking_in_toggle_or_buttons(self, root, ui_text: str) -> bool:
        """Нова розмітка: MUI ToggleButtonGroup або звичайні кнопки 'Гараж'/'Паркомісце'."""
        target_cf = _cf(ui_text)
        g_cf = _cf(self.PARKING_TYPE_GARAGE)
        p_cf = _cf(self.PARKING_TYPE_PARKING)

        # 1) ToggleButtonGroup, що містить обидві опції
        tgs = root.locator("css=div.MuiToggleButtonGroup-root")
        for i in range(tgs.count()):
            tg = tgs.nth(i)
            btns = tg.locator("css=button.MuiToggleButton-root, button")
            by_text: dict[str, object] = {}
            for j in range(btns.count()):
                b = btns.nth(j)
                try:
                    t = _norm(b.inner_text() or "")
                except Exception:
                    t = ""
                if t:
                    by_text[_cf(t)] = b
            if g_cf in by_text and p_cf in by_text and target_cf in by_text:
                try:
                    self._click_best_effort(by_text[target_cf])
                    return True
                except Exception as e:
                    logger.warning("Не вдалося клікнути toggle типу паркінгу: %s", e)
                    return False

        # 2) Будь-які клікабельні елементи (button/role=button) з точним текстом,
        #    за умови що поряд існує й друга опція (щоб не клікнути щось випадкове).
        lit = _xpath_literal(ui_text)
        other_lit = _xpath_literal(self.PARKING_TYPE_PARKING if target_cf == g_cf else self.PARKING_TYPE_GARAGE)
        target_nodes = root.locator(
            f"xpath=.//*[self::button or @role='button'][normalize-space(.)={lit}]"
        )
        other_exists = (
            root.locator(f"xpath=.//*[self::button or @role='button'][normalize-space(.)={other_lit}]").count() > 0
        )
        if target_nodes.count() and other_exists:
            try:
                self._click_best_effort(target_nodes.first)
                return True
            except Exception as e:
                logger.warning("Не вдалося клікнути кнопку типу паркінгу: %s", e)
                return False
        return False

    def get_current_parking_type(self) -> str | None:
        """Отримати поточний вибраний тип паркінгу."""
        root = self._root()

        # Нова розмітка: ToggleButtonGroup з натиснутою кнопкою (aria-pressed='true').
        tgs = root.locator("css=div.MuiToggleButtonGroup-root")
        for i in range(tgs.count()):
            tg = tgs.nth(i)
            btns = tg.locator("css=button.MuiToggleButton-root, button")
            texts = set()
            pressed_text = None
            for j in range(btns.count()):
                b = btns.nth(j)
                try:
                    t = _norm(b.inner_text() or "")
                except Exception:
                    t = ""
                if not t:
                    continue
                texts.add(_cf(t))
                try:
                    is_pressed = (b.get_attribute("aria-pressed") == "true") or (
                        "Mui-selected" in (b.get_attribute("class") or "")
                    )
                except Exception:
                    is_pressed = False
                if is_pressed:
                    pressed_text = t
            if _cf(self.PARKING_TYPE_GARAGE) in texts and _cf(self.PARKING_TYPE_PARKING) in texts:
                return pressed_text

        rgs = root.locator("xpath=.//*[@role='radiogroup']")

        for i in range(rgs.count()):
            rg = rgs.nth(i)
            labels = rg.locator("xpath=.//label[contains(@class,'MuiFormControlLabel-root')]")

            # Check if this is the parking type radiogroup
            has_garage = False
            has_parking = False

            for j in range(labels.count()):
                lab = labels.nth(j)
                try:
                    txt = _norm(lab.locator("css=span.MuiFormControlLabel-label").inner_text() or "")
                except Exception:
                    txt = ""

                txt_cf = _cf(txt)
                if txt_cf == _cf(self.PARKING_TYPE_GARAGE):
                    has_garage = True
                if txt_cf == _cf(self.PARKING_TYPE_PARKING):
                    has_parking = True

            if has_garage and has_parking:
                # Found the radiogroup, now find selected option
                for j in range(labels.count()):
                    lab = labels.nth(j)
                    try:
                        inp = lab.locator("css=input[type='radio']").first
                        if inp.count() and inp.is_checked():
                            return _norm(lab.locator("css=span.MuiFormControlLabel-label").inner_text() or "")
                    except Exception:
                        continue

        return None
