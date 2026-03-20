from __future__ import annotations

from typing import Sequence

from playwright.sync_api import Locator

from schemas import ADDRESS_LABELS
from setup_logger import setup_logger

logger = setup_logger(__name__)

# House number label (for special digit-matching logic)
_HOUSE_LABEL = "будинок"

# Ukrainian + Latin vowels used for street stem extraction
_UA_VOWELS = frozenset("аеєиіїоуюяАЕЄИІЇОУЮЯaeiouyAEIOUY")


def _street_search_stem(name: str) -> str:
    """Return first-syllable stem: consonants + first vowel, stop before second vowel.

    Examples: "Малевича" → "Мале", "Болсунівська" → "Болсу", "Саксаганського" → "Сакса".
    Minimum 3 chars returned; result is capped at 6 chars.
    """
    s = (name or "").strip()
    vowel_count = 0
    for i, ch in enumerate(s):
        if ch in _UA_VOWELS:
            vowel_count += 1
            if vowel_count == 2:
                stem = s[:i]
                return stem if len(stem) >= 3 else s[:max(i, 3)]
    return s[:6]


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
        is_street: bool = False,
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
                const isStreet = params.isStreet;

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
                  // Allow dropdowns opening both below AND above the anchor input
                  const bandTop    = anchor.y - 430;
                  const bandBottom = anchor.y + anchor.height + 430;
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

                  // 2) Word-stem match: any word in desired shares ≥5-char prefix with
                  //    any word in the option (handles e.g. "Нова Дарниця" → "Дарницький")
                  const dWords = d.split(/\s+/).filter(w => w.length >= 5);
                  if (dWords.length) {
                    for (const o of opts) {
                      const oWords = o.n.split(/\s+/);
                      for (const dw of dWords) {
                        for (const ow of oWords) {
                          if (ow.length >= 5) {
                            const len = Math.min(dw.length, ow.length, 6);
                            if (dw.slice(0, len) === ow.slice(0, len)) {
                              return mkResult(o, 'word_stem', opts.length);
                            }
                          }
                        }
                      }
                    }
                  }

                  // 4) House-normalized match: "20а" = "20-а" = "20 а" = "20А"
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

                  // 5) Digits-only prefix fallback
                  if (dDigits) {
                    for (const o of opts) {
                      if (o.digits && o.digits.startsWith(dDigits)) {
                        return mkResult(o, 'digits_prefix', opts.length);
                      }
                    }
                  }

                  // 6) Fuzzy similarity — street fields only (prefix + char-bag)
                  if (isStreet) {
                    function fuzzyScore(a, b) {
                      let pLen = 0;
                      const minL = Math.min(a.length, b.length);
                      while (pLen < minL && a[pLen] === b[pLen]) pLen++;
                      const prefScore = pLen / Math.max(a.length, b.length, 1);
                      const cnt = {};
                      for (const c of a) cnt[c] = (cnt[c] || 0) + 1;
                      let common = 0;
                      for (const c of b) { if (cnt[c] > 0) { common++; cnt[c]--; } }
                      const bagScore = common / Math.max(a.length, b.length, 1);
                      return 0.6 * prefScore + 0.4 * bagScore;
                    }
                    let bestScore = 0, bestOpt = null;
                    for (const o of opts) {
                      const oFirstWord = o.n.split(/\s+/)[0];
                      const score = fuzzyScore(d, oFirstWord);
                      if (score > bestScore) { bestScore = score; bestOpt = o; }
                    }
                    if (bestScore >= 0.60 && bestOpt) {
                      return mkResult(bestOpt, 'fuzzy_' + bestScore.toFixed(2), opts.length);
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
                    setTimeout(tick, 50);
                  };
                  tick();
                });
            }""",
            {
                "desired": desired,
                "timeoutMs": timeout_ms,
                "allowSingle": allow_single_option,
                "anchor": anchor_box,
                "isHouse": is_house,
                "isStreet": is_street,
            },
        )

        if not res or not res.get("ok"):
            logger.warning(
                "Autocomplete: не вдалось вибрати опцію для '%s' (видимих=%s)",
                desired,
                (res or {}).get("count"),
            )
            return False

        x, y = float(res["x"]), float(res["y"])
        logger.debug(
            "Autocomplete: клік мишею на опцію (%.1f, %.1f), режим=%s, текст='%s'",
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
            logger.debug("Autocomplete: випадаючий список закрито")
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
        is_address: bool = False,
        is_street: bool = False,
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
            is_street=is_street,
        )

        if not picked:
            if allow_free_text:
                try:
                    cur = (inp.input_value() or "").strip()
                except Exception:
                    cur = ""
                if cur:
                    logger.debug(
                        "Autocomplete прийнято як вільний текст. desired='%s' current='%s'",
                        desired,
                        cur,
                    )
                    try:
                        inp.press("Escape")
                    except Exception:
                        pass
                    try:
                        inp.press("Enter")
                    except Exception:
                        pass
                    try:
                        self.page.evaluate("() => { const el = document.activeElement; if (el) el.blur(); }")
                    except Exception:
                        pass
                    return True
            return False

        closed = self._wait_dropdown_closed(timeout_ms=2500)

        if section is not None and next_key:
            if self._wait_next_field_visible(section, next_key, timeout_ms=5000):
                logger.debug("Autocomplete підтверджено наступним полем: %s", next_key)
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
                    "Autocomplete підтверджено значенням поля. closed=%s desired='%s' current='%s'",
                    closed,
                    desired,
                    cur,
                )
                return True

        if allow_free_text and closed:
            logger.debug(
                "Autocomplete вважається успішним через закриття списку (вільний текст). desired='%s' current='%s'",
                desired,
                cur,
            )
            return True

        # For address fields: if the dropdown closed after our mouse-click, accept the
        # selection even when the input value differs from desired (e.g. "Болсунівська" →
        # site shows "Болсуновська вул." — slightly different spelling but valid selection).
        if is_address and closed:
            logger.debug(
                "Autocomplete прийнято через закриття списку (адресне поле). "
                "closed=True desired='%s' current='%s'",
                desired,
                cur,
            )
            return True

        logger.warning(
            "Autocomplete: не підтверджено. closed=%s desired='%s' current='%s'",
            closed,
            desired,
            cur,
        )
        return False

    # -------- debug helpers --------
    _DEBUG_LOG_KEYS = frozenset({"вулиця", "район"})

    def _debug_log_dropdown_options(self, key: str, stage: str) -> None:
        """Log visible dropdown options for Вулиця and Район fields (debug only)."""
        if key.lower().strip() not in self._DEBUG_LOG_KEYS:
            return
        try:
            opts = self.page.evaluate(
                """() => {
                    const seen = new Set();
                    const result = [];
                    for (const sel of ['[role="option"]', '.MuiAutocomplete-option', '[role="listbox"] li']) {
                        for (const el of document.querySelectorAll(sel)) {
                            if (seen.has(el)) continue;
                            seen.add(el);
                            const cs = getComputedStyle(el);
                            if (cs.display === 'none' || cs.visibility === 'hidden') continue;
                            const r = el.getBoundingClientRect();
                            if (r.width < 5 || r.height < 5) continue;
                            const txt = (el.innerText || '').trim();
                            if (txt) result.push(txt);
                        }
                    }
                    return result;
                }"""
            )
            if opts:
                logger.debug("'%s' [%s] опції (%d): %s", key, stage, len(opts), opts)
            else:
                logger.debug("'%s' [%s] — список порожній або ще не відкрито", key, stage)
        except Exception as exc:
            logger.debug("'%s' [%s] — не вдалося зчитати опції: %s", key, stage, exc)

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
            logger.warning("Autocomplete: елемент керування не знайдено для key='%s' label='%s'", key, label)
            return

        # Prefer visible input: MUI Autocomplete may contain an aria-hidden hidden input
        # that comes first in DOM order — typing into it produces no visible result.
        _visible = ctrl.locator("css=input:not([aria-hidden='true'])").first
        if _visible.count():
            inp = _visible
        elif ctrl.locator("css=input").count():
            inp = ctrl.locator("css=input").first
        else:
            inp = ctrl
        desired = ("" if value is None else str(value)).strip()
        if not desired:
            logger.info("Autocomplete пропуск '%s': бажане значення порожнє", key)
            return

        key_lower = key.lower().strip()
        is_address = key_lower in ADDRESS_LABELS
        is_house = key_lower == _HOUSE_LABEL

        # Detect readonly inputs (e.g. "Район" — click-to-open dropdown, no typing)
        try:
            _is_readonly = bool(inp.evaluate("el => !!el.readOnly"))
        except Exception:
            _is_readonly = False

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
                logger.info("Autocomplete пропуск '%s': вже заповнено '%s'", key, cur_input)
                return

        logger.info("Autocomplete заповнення '%s' = '%s'%s", key, desired, " (примусово)" if force else "")

        # Split house number into digit prefix and the rest (e.g. "20а" → "20", "а")
        import re as _re

        _house_digit_prefix = ""
        _house_rest = desired
        if is_house:
            _m = _re.match(r"\d+", desired)
            if _m:
                _house_digit_prefix = _m.group(0)
                _house_rest = desired[len(_house_digit_prefix) :]

        def _clear_and_type(text: str | None = None) -> None:
            """Очищає поле введення та вводить текст. Якщо text=None, використовує повне бажане значення."""
            to_type = text if text is not None else desired

            # Click to focus / open dropdown
            try:
                inp.click()
            except Exception:
                pass

            # Read-only fields (e.g. "Район") open a pre-loaded dropdown on click.
            # Typing/filling is not possible — just wait for the dropdown to appear.
            if _is_readonly:
                try:
                    self.page.wait_for_timeout(600)
                except Exception:
                    pass
                return

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
                logger.debug("inp.type() не вдалось для '%s': %s", to_type, e)
                try:
                    inp.fill(to_type)
                except Exception as e2:
                    logger.warning("inp.fill() також не вдалось для '%s': %s", to_type, e2)

            # Verify text was actually typed
            try:
                cur = (inp.input_value() or "").strip()
                if not cur:
                    logger.warning("Поле порожнє після введення '%s', повторна спроба через fill()", to_type)
                    try:
                        inp.fill(to_type)
                    except Exception:
                        pass
            except Exception:
                pass

        is_street = key_lower == "вулиця"

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
                is_address=is_address,
                is_street=is_street,
            )

        # For street fields: first try a short stem to get broader server suggestions,
        # then fall through to full-name attempt if stem pick fails.
        if is_street and not _is_readonly:
            stem = _street_search_stem(desired)
            if stem and stem != desired:
                logger.debug("Вулиця: введення стему '%s' замість повного '%s'", stem, desired)
                try:
                    inp.click()
                    inp.fill("")
                    inp.type(stem, delay=30)
                    self.page.wait_for_timeout(1000)
                except Exception:
                    pass
                if _try_pick():
                    self._mark_touched(inp)
                    return
                # Stem pick failed — clear before full-name attempt below

        # First attempt: type digits only (for house) or full text
        _clear_and_type()
        self._debug_log_dropdown_options(key, "до вибору")
        if _try_pick():
            self._mark_touched(inp)
            self._debug_log_dropdown_options(key, "після вибору")
            return

        # For house: if digits-only didn't match, type full value and retry
        if is_house and _house_rest:
            logger.debug("Будинок: збіг тільки за цифрами не знайдено, вводимо повне значення '%s'", desired)
            _clear_and_type(desired)
            if _try_pick():
                self._mark_touched(inp)
                return

        logger.debug("Повторна спроба вибору autocomplete (мишею) для '%s' = '%s'", key, desired)
        if not _is_readonly:
            try:
                inp.press("End")
                inp.type(" ")
                self.page.wait_for_timeout(120)
                inp.press("Backspace")
            except Exception:
                pass
        else:
            # Re-click to re-open the dropdown for another pick attempt
            try:
                inp.click()
                self.page.wait_for_timeout(400)
            except Exception:
                pass

        self._debug_log_dropdown_options(key, "повторна спроба")
        if _try_pick():
            self._mark_touched(inp)
            self._debug_log_dropdown_options(key, "після вибору")
            return

        # Extra retry for "район": type a short stem (first 4 chars of first word)
        # to broaden the API suggestion (e.g. "Оболонь" → "обол" → "Оболонський")
        if key_lower == "район" and not _is_readonly:
            first_word = desired.split()[0] if desired.split() else desired
            stem = first_word[:4]
            if stem and stem.lower() != desired[:4].lower() or len(desired) > 4:
                logger.debug("Район: коротка основа '%s' для пошуку замість '%s'", stem, desired)
                try:
                    inp.click()
                    inp.fill("")
                    inp.type(stem, delay=25)
                    self.page.wait_for_timeout(600)
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
            "Autocomplete не зміг вибрати '%s' для key='%s' next_key='%s' (current='%s')",
            desired,
            key,
            next_key,
            cur,
        )
        # For address/house fields: commit whatever is typed as free text.
        # NOTE: do NOT press Escape for street fields — MUI Autocomplete clears the
        # input on Escape when no option was confirmed, losing the typed street name.
        # Instead: Enter confirms the typed value (freeSolo), then blur finalises it.
        if is_address or is_house:
            if not is_street:
                # For non-street address fields (Район, Місто, Будинок) Escape is safe:
                # it closes the dropdown without clearing a confirmed selection.
                try:
                    inp.press("Escape")
                except Exception:
                    pass
            try:
                inp.press("Enter")
            except Exception:
                pass
            try:
                # Blur via JS — triggers MUI's onBlur which commits typed value
                self.page.evaluate("() => { const el = document.activeElement; if (el) el.blur(); }")
            except Exception:
                pass
            try:
                # Fallback: click the form heading to physically move focus away
                h5 = self.page.locator("h5").first
                if h5.count():
                    h5.click()
            except Exception:
                pass

    def _fill_autocomplete_multi(self, section: Locator, key: str, values: Sequence[str]) -> None:
        label = self._expected_label(key) or key
        ctrl = self._find_control_by_label(section, label)
        if not ctrl:
            logger.warning(
                "Autocomplete(multi): елемент керування не знайдено для key=%s (label=%s)",
                key,
                label,
            )
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
                logger.info("Autocomplete multi пропуск %s: вже є '%s'", key, s)
                continue
            desired.append(s)

        if not desired:
            logger.info("Autocomplete multi пропуск %s: нічого додавати (вже заповнено)", key)
            return

        _visible = ctrl.locator("css=input:not([aria-hidden='true'])").first
        if _visible.count():
            inp = _visible
        elif ctrl.locator("css=input").count():
            inp = ctrl.locator("css=input").first
        else:
            inp = ctrl

        for v in desired:
            logger.debug("Autocomplete multi додавання %s -> %s", key, v)
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
                logger.warning("Autocomplete multi не вдалось додати %s -> %s", key, v)

        self._mark_touched(inp)
