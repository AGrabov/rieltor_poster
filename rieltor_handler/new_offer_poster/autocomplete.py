from __future__ import annotations

from typing import Sequence

from playwright.sync_api import Locator

from schemas import ADDRESS_LABELS
from setup_logger import setup_logger

logger = setup_logger(__name__)

# House number label (for special digit-matching logic)
_HOUSE_LABEL = "будинок"


class AutocompleteMixin:
    # -------- value detection (skip if already filled) --------
    def _normalize_autocomplete_root(self, ctrl: Locator) -> Locator:
        # если пришёл input — поднимаемся к корню автокомплита
        try:
            tag = ctrl.evaluate("el => el.tagName.toLowerCase()")
        except Exception:
            tag = ""
        if tag in ("input", "textarea"):
            root = ctrl.locator("xpath=ancestor::*[contains(@class,'MuiAutocomplete-root')][1]").first
            if root.count():
                return root
            root = ctrl.locator("xpath=ancestor::*[contains(@class,'MuiFormControl-root')][1]").first
            if root.count():
                return root
        return ctrl

    def _control_has_value(self, ctrl: Locator) -> bool:
        # нормализуем: если пришёл input -> поднимаемся к MuiAutocomplete-root / FormControl
        ctrl = self._normalize_autocomplete_root(ctrl)

        # 1) input.value
        try:
            inp = ctrl.locator("css=input").first
            if inp.count():
                v = (inp.input_value() or "").strip()
                if v:
                    return True
        except Exception:
            pass

        # 2) chips (multi)
        try:
            if ctrl.locator(".MuiChip-label").count():
                return True
        except Exception:
            pass

        # 3) rendered selected value inside inputRoot (НЕ учитывать label/placeholder)
        try:
            input_root = ctrl.locator(".MuiAutocomplete-inputRoot").first
            if input_root.count():
                txt = " ".join(t.strip() for t in input_root.all_inner_texts() if t.strip()).strip()
                if txt:
                    try:
                        label_txt = ""
                        lbl = ctrl.locator("css=label").first
                        if lbl.count():
                            label_txt = (lbl.inner_text() or "").strip()
                        if label_txt and txt == label_txt:
                            return False
                    except Exception:
                        pass
                    return True
        except Exception:
            pass

        return False

    # -------- option picking (mouse) --------
    def _mouse_click_visible_option_by_text(
        self,
        desired: str,
        timeout_ms: int = 6000,
        *,
        allow_single_option: bool = False,
        anchor_box: dict | None = None,
        is_house: bool = False,
    ) -> bool:
        desired = (desired or "").strip()
        if not desired:
            return False

        self.page.wait_for_timeout(150)

        res = self.page.evaluate(
            """(params) => {
                const desired = params.desired;
                const timeoutMs = params.timeoutMs;
                const allowSingle = params.allowSingle;
                const anchor = params.anchor;
                const isHouse = params.isHouse;

                const start = Date.now();
                const norm = (s) => (s || '').replace(/\\s+/g,' ').trim().toLowerCase();
                const onlyDigits = (s) => (s || '').replace(/\\D+/g,'');
                // Strip hyphens + spaces + lowercase for house number comparison
                // "20-а" / "20 а" / "20А" → "20а"
                const normHouse = (s) => (s || '').replace(/[\\s\\-]+/g, '').toLowerCase();
                const d = norm(desired);
                const dDigits = onlyDigits(desired);
                const dHouse = normHouse(desired);

                const isVisible = (el) => {
                  if (!el) return false;
                  const cs = getComputedStyle(el);
                  if (cs.display === 'none' || cs.visibility === 'hidden' || Number(cs.opacity) === 0) return false;
                  const r = el.getBoundingClientRect();
                  if (r.width < 5 || r.height < 5) return false;
                  if (r.bottom < 0 || r.right < 0 || r.top > innerHeight || r.left > innerWidth) return false;
                  return true;
                };

                const inAnchorBand = (r) => {
                  if (!anchor) return true;
                  const bandTop = anchor.y + anchor.height - 6;
                  const bandBottom = bandTop + 420;
                  const cx = r.left + r.width / 2;
                  const ax = anchor.x + anchor.width / 2;
                  return r.top >= bandTop && r.top <= bandBottom && Math.abs(cx - ax) <= 520;
                };

                const selectors = [
                  '[role="option"]',
                  '[data-option-index]',
                  '.MuiAutocomplete-option',
                  '[role="listbox"] [role="option"]',
                  '[role="listbox"] li',
                  'li',
                  'div'
                ];

                function collect() {
                  const out = [];
                  const seen = new Set();
                  for (const sel of selectors) {
                    for (const el of document.querySelectorAll(sel)) {
                      if (seen.has(el)) continue;
                      seen.add(el);
                      if (!isVisible(el)) continue;

                      const tag = (el.tagName || '').toLowerCase();
                      if (['input','textarea','label','button'].includes(tag)) continue;

                      const r = el.getBoundingClientRect();
                      if (!inAnchorBand(r)) continue;

                      if (r.height > 260 && r.width > 600) continue;

                      const txt = (el.innerText || '').trim();
                      if (!txt) continue;

                      out.push({ txt, n: norm(txt), digits: onlyDigits(txt), h: normHouse(txt), r });
                    }
                  }
                  out.sort((a,b) => a.r.top - b.r.top);
                  return out;
                }

                const mkResult = (o, mode, count) => ({
                  ok:true, x:o.r.left + o.r.width/2,
                  y:o.r.top + Math.min(18, o.r.height/2),
                  text:o.txt, mode, count
                });

                function pick(opts) {
                  // 1) Standard text match
                  for (const o of opts) {
                    if (o.n === d || o.n.startsWith(d) || o.n.includes(d)) {
                      return mkResult(o, 'match', opts.length);
                    }
                  }

                  // 2) House-normalized match: "20а" = "20-а" = "20 а" = "20А"
                  if (isHouse && dHouse) {
                    for (const o of opts) {
                      if (o.h === dHouse) {
                        return mkResult(o, 'house_exact', opts.length);
                      }
                    }
                    for (const o of opts) {
                      if (o.h.startsWith(dHouse)) {
                        return mkResult(o, 'house_prefix', opts.length);
                      }
                    }
                  }

                  // 3) Digits-only prefix fallback
                  if (dDigits) {
                    for (const o of opts) {
                      if (o.digits && o.digits.startsWith(dDigits)) {
                        return mkResult(o, 'digits_prefix', opts.length);
                      }
                    }
                  }

                  if (allowSingle && opts.length === 1) {
                    return mkResult(opts[0], 'single', 1);
                  }

                  return null;
                }

                return new Promise((resolve) => {
                  const tick = () => {
                    const opts = collect();
                    const got = pick(opts);
                    if (got) return resolve(got);
                    if (Date.now() - start > timeoutMs) return resolve({ ok:false, count: opts.length });
                    requestAnimationFrame(tick);
                  };
                  tick();
                });
            }""",
            {"desired": desired, "timeoutMs": timeout_ms, "allowSingle": allow_single_option, "anchor": anchor_box, "isHouse": is_house},
        )

        if not res or not res.get("ok"):
            logger.warning(
                "Autocomplete: could not pick option for '%s' (visible=%s)",
                desired,
                (res or {}).get("count"),
            )
            return False

        x, y = float(res["x"]), float(res["y"])
        logger.debug(
            "Autocomplete: mouse click option at (%.1f, %.1f), mode=%s, text='%s'",
            x,
            y,
            res.get("mode"),
            (res.get("text") or "").strip(),
        )
        self.page.mouse.move(x, y)
        self.page.mouse.click(x, y)
        return True

    # -------- confirmation helpers --------
    def _wait_dropdown_closed(self, timeout_ms: int = 2500) -> bool:
        try:
            self.page.wait_for_function(
                """() => {
                    const isVisible = (el) => {
                        if (!el) return false;
                        const cs = getComputedStyle(el);
                        if (cs.display === 'none' || cs.visibility === 'hidden' || Number(cs.opacity) === 0) return false;
                        const r = el.getBoundingClientRect();
                        return r.width > 5 && r.height > 5 && r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth;
                    };
                    const candidates = Array.from(
                        document.querySelectorAll('[role="option"], [data-option-index], .MuiAutocomplete-option, [role="listbox"] [role="option"], [role="listbox"] li')
                    );
                    return !candidates.some(isVisible);
                }""",
                timeout=timeout_ms,
            )
            logger.debug("Autocomplete: dropdown closed")
            return True
        except Exception:
            return False

    def _wait_next_field_visible(self, section: Locator, next_key: str, timeout_ms: int = 5000) -> bool:
        next_label = self._expected_label(next_key) or next_key
        lit = self._xpath_literal(next_label)
        try:
            section.locator(f"xpath=.//label[contains(normalize-space(.), {lit})]").first.wait_for(
                state="visible",
                timeout=timeout_ms,
            )
            return True
        except Exception:
            return False

    # -------- pick + verify --------
    def _pick_autocomplete_option_and_verify(
        self,
        inp: Locator,
        desired_text: str,
        timeout_ms: int = 9000,
        *,
        section: Locator | None = None,
        next_key: str | None = None,
        allow_single_option: bool = False,
        allow_free_text: bool = False,
        is_house: bool = False,
    ) -> bool:
        desired = (desired_text or "").strip()

        try:
            inp.press("ArrowDown")
        except Exception:
            pass

        anchor = None
        try:
            anchor = inp.bounding_box()
        except Exception:
            anchor = None

        picked = self._mouse_click_visible_option_by_text(
            desired,
            timeout_ms=timeout_ms,
            allow_single_option=allow_single_option,
            anchor_box=anchor,
            is_house=is_house,
        )

        if not picked:
            if allow_free_text:
                try:
                    cur = (inp.input_value() or "").strip()
                except Exception:
                    cur = ""
                if cur:
                    logger.debug("Autocomplete accepted as free-text. desired='%s' current='%s'", desired, cur)
                    return True
            return False

        closed = self._wait_dropdown_closed(timeout_ms=2500)

        if section is not None and next_key:
            if self._wait_next_field_visible(section, next_key, timeout_ms=5000):
                logger.debug("Autocomplete confirmed by next field: %s", next_key)
                return True

        self.page.wait_for_timeout(150)
        try:
            cur = (inp.input_value() or "").strip()
        except Exception:
            cur = ""

        if cur:
            cur_l = cur.lower()
            des_l = desired.lower()
            if cur_l == des_l or cur_l.startswith(des_l) or des_l in cur_l:
                logger.debug(
                    "Autocomplete confirmed by input value. closed=%s desired='%s' current='%s'",
                    closed,
                    desired,
                    cur,
                )
                return True

        if allow_free_text and closed:
            logger.debug("Autocomplete treated as success by dropdown close (free-text). desired='%s' current='%s'", desired, cur)
            return True

        logger.warning("Autocomplete: not confirmed. closed=%s desired='%s' current='%s'", closed, desired, cur)
        return False

    # -------- fill wrappers --------
    def _fill_autocomplete(
        self,
        section: Locator,
        key: str,
        value: str,
        *,
        next_key: str | None = None,
        force: bool = False,
    ) -> None:
        label = self._expected_label(key) or str(value)
        ctrl = self._find_control_by_label(section, label)
        if not ctrl:
            logger.warning("Autocomplete control not found for key='%s' label='%s'", key, label)
            return

        inp = ctrl.locator("css=input").first if ctrl.locator("css=input").count() else ctrl
        desired = ("" if value is None else str(value)).strip()
        if not desired:
            logger.info("Autocomplete skip '%s': empty desired value", key)
            return

        key_lower = key.lower().strip()
        is_address = key_lower in ADDRESS_LABELS
        is_house = key_lower == _HOUSE_LABEL

        def _matches(cur: str) -> bool:
            cur = (cur or "").strip()
            if not cur:
                return False

            c = cur.casefold()
            d = desired.casefold()

            if is_house:
                import re
                cd = re.sub(r"\D+", "", c)
                dd = re.sub(r"\D+", "", d)
                if dd and cd.startswith(dd):
                    return True
                return c == d or c.startswith(d) or d in c

            if is_address:
                return c == d or c.startswith(d)

            return c == d or c.startswith(d) or d in c

        # текущее значение в input
        try:
            cur_input = (inp.input_value() or "").strip()
        except Exception:
            cur_input = ""

        # SKIP только если НЕ force и текущее значение совпадает с desired
        if (not force) and cur_input and _matches(cur_input):
            if not next_key or self._wait_next_field_visible(section, next_key, timeout_ms=1200):
                logger.info("Autocomplete skip '%s': already '%s'", key, cur_input)
                return

        logger.info("Autocomplete fill '%s' = '%s'%s", key, desired, " (force)" if force else "")

        # Split house number into digit prefix and the rest (e.g. "20а" → "20", "а")
        import re as _re
        _house_digit_prefix = ""
        _house_rest = desired
        if is_house:
            _m = _re.match(r'\d+', desired)
            if _m:
                _house_digit_prefix = _m.group(0)
                _house_rest = desired[len(_house_digit_prefix):]

        def _clear_and_type(text: str | None = None) -> None:
            """Clear input and type text. If text is None, uses full desired value."""
            to_type = text if text is not None else desired

            # Click to focus (needed before fill/type)
            try:
                inp.click()
            except Exception:
                pass

            # Clear via fill("") — dispatches proper React onChange events
            try:
                inp.fill("")
            except Exception:
                try:
                    inp.press("Control+A")
                    inp.press("Backspace")
                except Exception:
                    pass

            if is_house and text is None and _house_digit_prefix:
                # Type digits one-by-one to let dropdown populate progressively
                for ch in _house_digit_prefix:
                    try:
                        inp.type(ch, delay=0)
                        self.page.wait_for_timeout(150)
                    except Exception:
                        pass
                # Wait for dropdown to load after final digit
                try:
                    self.page.wait_for_timeout(800)
                except Exception:
                    pass
                return

            # Type character-by-character to trigger API search
            try:
                inp.type(to_type, delay=25)
            except Exception as e:
                logger.debug("inp.type() failed for '%s': %s", to_type, e)
                try:
                    inp.fill(to_type)
                except Exception as e2:
                    logger.warning("inp.fill() also failed for '%s': %s", to_type, e2)

            # Verify text was actually typed
            try:
                cur = (inp.input_value() or "").strip()
                if not cur:
                    logger.warning("Input empty after typing '%s', retrying with fill()", to_type)
                    try:
                        inp.fill(to_type)
                    except Exception:
                        pass
            except Exception:
                pass

        def _try_pick() -> bool:
            allow_single = is_address
            allow_free = is_house
            return self._pick_autocomplete_option_and_verify(
                inp,
                desired,
                section=section,
                next_key=next_key,
                allow_single_option=allow_single,
                allow_free_text=allow_free,
                is_house=is_house,
            )

        # First attempt: type digits only (for house) or full text
        _clear_and_type()
        if _try_pick():
            self._mark_touched(inp)
            return

        # For house: if digits-only didn't match, type full value and retry
        if is_house and _house_rest:
            logger.debug("House: digits-only didn't match, typing full value '%s'", desired)
            _clear_and_type(desired)
            if _try_pick():
                self._mark_touched(inp)
                return

        logger.debug("Retry autocomplete selection (mouse) for '%s' = '%s'", key, desired)
        try:
            inp.press("End")
            inp.type(" ")
            self.page.wait_for_timeout(120)
            inp.press("Backspace")
        except Exception:
            pass

        if _try_pick():
            self._mark_touched(inp)
            return

        try:
            cur = (inp.input_value() or "").strip()
        except Exception:
            cur = ""

        self._mark_touched(inp)
        logger.warning(
            "Autocomplete failed to select '%s' for key='%s' next_key='%s' (current='%s')",
            desired,
            key,
            next_key,
            cur,
        )


    def _fill_autocomplete_multi(self, section: Locator, key: str, values: Sequence[str]) -> None:
        label = self._expected_label(key) or key
        ctrl = self._find_control_by_label(section, label)
        if not ctrl:
            logger.warning("Autocomplete(multi) control not found for key=%s (label=%s)", key, label)
            return

        existing: set[str] = set()
        try:
            chips = ctrl.locator(".MuiChip-label")
            for i in range(chips.count()):
                t = (chips.nth(i).inner_text() or "").strip()
                if t:
                    existing.add(t.casefold())
        except Exception:
            pass

        desired: list[str] = []
        for v in values:
            s = ("" if v is None else str(v)).strip()
            if not s:
                continue
            if s.casefold() in existing:
                logger.info("Autocomplete multi skip %s: already has '%s'", key, s)
                continue
            desired.append(s)

        if not desired:
            logger.info("Autocomplete multi skip %s: nothing to add (already filled)", key)
            return

        inp = ctrl.locator("css=input").first if ctrl.locator("css=input").count() else ctrl

        for v in desired:
            logger.debug("Autocomplete multi add %s -> %s", key, v)
            try:
                inp.click()
            except Exception:
                pass
            try:
                inp.fill("")
            except Exception:
                pass
            try:
                inp.type(v, delay=20)
            except Exception:
                pass

            ok = self._pick_autocomplete_option_and_verify(inp, v, timeout_ms=7000)
            if ok:
                existing.add(v.casefold())
            else:
                logger.warning("Autocomplete multi failed to add %s -> %s", key, v)

        self._mark_touched(inp)
