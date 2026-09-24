"""Оновлення дати актуальності опублікованих оголошень rieltor.ua (вкладка mode=10).

Сайт краще ранжує оголошення зі свіжою датою актуальності, тож дату треба
піднімати регулярно — але лише тим об'єктам, які ще актуальні в CRM (закриті
спершу йдуть у «Мої угоди», див. `DealsMover`).

Розмітка перевірена наживо (2026-09-24): масового виділення на вкладці немає —
у рядках стоїть radio (`name="asd"`), тож вибрати можна лише один рядок, і
кнопка «Оновити дату актуальності» в тулбарі працює так само по одному. Тому
йдемо рядками.

Саме підняття робимо тим запитом, який шле кнопка підтвердження
(`item-action?action=refresh`) — це ~1 с на оголошення проти ~6 с на клік, а на
1744 оголошення різниця між півгодиною і трьома годинами. Якщо запит не
спрацював, лишається фолбек кліком: зелена іконка-годинник у комірці з датою →
діалог «Підтвердження актуальності оголошення» → OK. Іконку шукаємо за
унікальним `path`, а не за кольором: колір залежить від давності дати.

Сортуємо за `sort=updatedAt` (найстаріші зверху) і працюємо лише з першою
сторінкою: підтверджений рядок отримує дату «Вчора» (саме «Вчора», не «Сьог.»)
і їде вниз, звільняючи місце наступному найстарішому. Рядки «Сьог.» і «Вчора»
пропускаємо: сайт приймає повторний клік і відповідає «ok», але дату не рухає.

`refresh_all` повертає `RefreshOutcome(done, completed)`: `completed=False`
означає обірваний прохід (ліміт, помилка сторінки, серія невдач) — після такого
добу закривати не можна, інакше решта оголошень лишиться до завтра.

Чиста логіка (план сторінок, лічильник, цикл) тестується юніт-тестами;
браузерні методи перевіряються на живому сайті.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import NamedTuple

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PWTimeout

from setup_logger import setup_logger

logger = setup_logger(__name__)


class ActualityPageError(Exception):
    """Сторінку зі списком не вдалося прочитати (таймаут, редірект на логін тощо).

    Відрізняє «таблиця є, несвіжих рядків немає» від «таблиці немає»: перше —
    успішне завершення, друге — обірваний прохід, після якого добу закривати
    не можна.
    """


class RefreshOutcome(NamedTuple):
    """Результат проходу: скільки оновлено і чи дійшли до кінця."""

    done: int
    completed: bool


class ActualityRefresher:
    """Піднімає дату актуальності опублікованих оголошень (рядок за рядком)."""

    # sort=updatedAt — найстаріші зверху (перевірено наживо: клік по заголовку
    # «Онов» дає -updatedAt, повторний — updatedAt, і параметр лишається в URL).
    # Завдяки цьому працюємо лише з першою сторінкою: оновлений рядок стає
    # «Вчора» і їде вниз, а на його місце підтягується наступний найстаріший.
    PUBLISHED_URL_TMPL = (
        "https://my.rieltor.ua/offers/management?page={page}&limit={limit}&mode=10&status=10&sort=updatedAt"
    )
    # select «На сторінці» дає максимум 200 → більше рядків на одну сторінку сайт не віддає.
    PAGE_LIMIT = 200
    COUNT_LIMIT = 25  # для читання лічильника достатньо малого ліміту
    TABLE = "table"
    ROW = "table tbody tr"
    # Іконка «оновити дату актуальності»: годинник зі стрілкою. Шлях унікальний
    # у рядку — кнопку «Підняти» (платна послуга) він не зачіпає.
    ICON_PATH_PREFIX = "M12 20C9.76667 20 7.875 19.225"
    ICON = f"svg:has(path[d^='{ICON_PATH_PREFIX}'])"
    # Запит, який шле сама кнопка підтвердження (перевірено наживо): відповідь
    # `{"data":{"<id>":"ok"},"status":"OK"}`. Через нього оновлення йде ~0.4 с
    # замість ~6 с на клік, а кліки лишаються фолбеком.
    API_REFRESH_TMPL = "https://rieltor.ua/api/offers/item-action/?id={id}&action=refresh"
    REQUEST_DELAY_SEC = 0.3
    DIALOG = "div[role='dialog']"
    DIALOG_CONFIRM = "div[role='dialog'] button:has-text('OK')"
    PAGINATION_TOOLBAR = "[class*='MuiTablePagination-toolbar']"
    # «X - Y з Z» / «X–Y of Z» — Z (остання група) є повною кількістю.
    _RANGE_RE = re.compile(
        r"\d+\s*[–-]\s*\d+\s+(?:з|із|of)\s+(?:понад\s+|більш(?:е)?\s+ніж\s+)?(\d+)",
        re.IGNORECASE,
    )
    _EDIT_HREF_RE = re.compile(r"/offers/edit/(\d+)")
    RENDER_TIMEOUT_MS = 15_000
    DIALOG_TIMEOUT_MS = 10_000
    # Протухла сесія або 403/429 від сайту: після такої серії підряд немає сенсу
    # годину лупити таймаутами по решті бази — виходимо з ознакою «не завершено».
    MAX_CONSECUTIVE_FAILURES = 10

    def __init__(self, page: Page) -> None:
        self.page = page

    # ── чиста логіка (юніт-тести) ────────────────────────────────────

    @staticmethod
    def _api_ok(status: int, body: str | None, key: str) -> bool:
        """Чи підтвердив сайт оновлення саме цього оголошення.

        Відповідь кнопки: `{"data":{"13065703":"ok"},"status":"OK"}`.
        """
        if status != 200 or not body:
            return False
        try:
            data = json.loads(body).get("data") or {}
        except (ValueError, AttributeError):
            return False
        return data.get(str(key)) == "ok"

    @classmethod
    def _parse_total(cls, text: str | None) -> int | None:
        """Повна кількість рядків із тексту MUI-пагінації. None, якщо не розпізнано."""
        if not text:
            return None
        m = cls._RANGE_RE.search(text)
        return int(m.group(1)) if m else None

    # Підтвердження актуальності ставить дату «Вчора» (перевірено наживо
    # 2026-09-24: рядок «22.09» після OK став «Вчора»), а не «Сьог.». Тому
    # свіжими вважаємо обидва варіанти: сайт прийме повторний клік і відповість
    # «ok», але дату не зрушить — це були б марні кліки.
    _FRESH_PREFIXES = ("сьог", "вчора")

    @classmethod
    def _is_fresh(cls, cell_text: str | None) -> bool:
        """Чи оновлювали оголошення сьогодні або вчора (тоді клікати нема сенсу)."""
        if not cell_text:
            return False
        text = cell_text.strip().lower()
        return any(text.startswith(p) for p in cls._FRESH_PREFIXES)

    def _page_plan(self, total: int, limit: int | None = None) -> list[int]:
        """Номери сторінок, які треба обійти, щоб зачепити всі `total` рядків."""
        limit = limit or self.PAGE_LIMIT
        if total <= 0:
            return []
        return list(range(1, (total + limit - 1) // limit + 1))

    def _keys_from_pairs(self, pairs) -> tuple[list[str], int, int]:
        """Розібрати пари (href, текст комірки) у ключі до оновлення.

        Returns:
            (ключі несвіжих рядків, скільки вже свіжих, скільки без комірки).
            Третє число — сигнал, що іконку не знайдено: якщо сайт змінить
            svg-шлях, усі рядки стануть «без комірки», і мовчазний нуль
            оновлень треба відрізняти від чесного «все свіже».
        """
        keys: list[str] = []
        fresh = unknown = 0
        for href, cell_text in pairs:
            m = self._EDIT_HREF_RE.search(href or "")
            if not m:
                continue
            if not cell_text:
                unknown += 1
                continue
            if self._is_fresh(cell_text):
                fresh += 1
                continue
            keys.append(m.group(1))
        return keys, fresh, unknown

    def refresh_all(
        self,
        max_count: int | None = None,
        dry_run: bool = False,
        progress_cb: Callable[[int], None] | None = None,
    ) -> RefreshOutcome:
        """Підняти дату актуальності всім опублікованим оголошенням.

        Сторінку перечитуємо, доки на ній лишаються несвіжі рядки: після
        оновлення список пересортовується, тож на її місце можуть підтягнутись
        інші. Уже оброблені ключі запам'ятовуємо — так цикл не зациклиться на
        рядках, які оновити не вдалося.

        Args:
            max_count: скільки максимум оновити (0 — нічого).
            dry_run: лише порахувати рядки, які потребують оновлення (оцінка
                оптимістична: можливі невдачі не враховуються).
            progress_cb: викликається після кожного успішного оновлення —
                дашборд так бачить, що прогін живий.

        Returns:
            RefreshOutcome(done, completed). `completed=False` означає, що
            прохід обірвався (ліміт, помилка сторінки, серія невдач), тож
            добу закривати не можна.
        """
        total = self.count()
        if total <= 0:
            logger.info("Опублікованих оголошень немає — нічого оновлювати")
            return RefreshOutcome(0, True)

        logger.info(
            "Оновлення дати актуальності: %d оголошень на сайті (max_count=%s, dry_run=%s)",
            total,
            max_count,
            dry_run,
        )

        if dry_run:
            return self._count_stale(total, max_count)

        processed: set[str] = set()
        done = 0
        failures = 0
        while max_count is None or done < max_count:
            try:
                keys = [k for k in self._page_keys() if k not in processed]
            except ActualityPageError as e:
                logger.error("Прохід обірвано: %s", e)
                return RefreshOutcome(done, False)
            if not keys:
                logger.info("Несвіжих рядків більше немає")
                return RefreshOutcome(done, True)
            for key in keys:
                if max_count is not None and done >= max_count:
                    logger.info("Досягнуто ліміту %d", max_count)
                    break
                processed.add(key)
                if self._refresh_row(key):
                    done += 1
                    failures = 0
                    logger.info("Оновлено %s (%d)", key, done)
                    if progress_cb is not None:
                        progress_cb(done)
                    continue
                failures += 1
                logger.warning("Не вдалося оновити дату актуальності оголошення %s", key)
                if failures >= self.MAX_CONSECUTIVE_FAILURES:
                    logger.error(
                        "%d невдач поспіль — схоже, сесія протухла або сайт відмовляє; зупиняємось",
                        failures,
                    )
                    return RefreshOutcome(done, False)

        logger.info("Оновлення дати актуальності завершено: %d оголошень", done)
        return RefreshOutcome(done, max_count is None)

    def _count_stale(self, total: int, max_count: int | None) -> RefreshOutcome:
        """Скільки рядків потребують оновлення (dry-run: обходимо сторінки, не клікаючи)."""
        seen: set[str] = set()
        for page_no in self._page_plan(total):
            if max_count is not None and len(seen) >= max_count:
                break
            try:
                keys = self._page_keys(page_no)
            except ActualityPageError as e:
                logger.error("Сторінку %d не прочитано: %s", page_no, e)
                return RefreshOutcome(len(seen), False)
            for key in keys:
                seen.add(key)
                logger.debug("[dry-run] Оновив би дату актуальності оголошення %s", key)
                if max_count is not None and len(seen) >= max_count:
                    break
        logger.info("[dry-run] Оновив би %d оголошень", len(seen))
        return RefreshOutcome(len(seen), max_count is None)

    # ── браузерні методи (перевіряються на живому сайті) ─────────────

    def _url(self, page_no: int, limit: int) -> str:
        return self.PUBLISHED_URL_TMPL.format(page=max(1, page_no), limit=max(1, limit))

    def _goto(self, url: str) -> bool:
        """Відкрити сторінку списку. False — таблиця так і не відрендерилась."""
        logger.debug("Перехід: %s", url)
        try:
            self.page.goto(url, wait_until="networkidle")
        except PWTimeout:
            logger.debug("networkidle не настав — продовжуємо")
        except Exception as e:  # net::ERR_*, закритий контекст тощо
            logger.warning("Навігація не вдалася: %s", e)
            return False
        try:
            self.page.wait_for_selector(self.TABLE, timeout=self.RENDER_TIMEOUT_MS)
            return True
        except Exception:
            logger.warning("Таблиця «Опубліковані» не з'явилася за %d мс", self.RENDER_TIMEOUT_MS)
            return False

    def count(self) -> int:
        """Скільки оголошень на вкладці «Опубліковані» (з MUI-пагінації)."""
        self._goto(self._url(1, self.COUNT_LIMIT))
        try:
            toolbar = self.page.locator(self.PAGINATION_TOOLBAR).first
            toolbar.wait_for(state="visible", timeout=self.RENDER_TIMEOUT_MS)
            total = self._parse_total(toolbar.inner_text())
            if total is not None:
                logger.info("Опублікованих оголошень: %d", total)
                return total
            logger.debug("Шаблон «X - Y з Z» у пагінації не знайдено")
        except Exception as e:
            logger.debug("Пагінацію прочитати не вдалося: %s", e)

        # Фолбек: рахуємо видимі рядки — але на повній сторінці, інакше вийде
        # щонайбільше COUNT_LIMIT і dry-run недорахує сторінок.
        self._goto(self._url(1, self.PAGE_LIMIT))
        n = self.page.locator(self.ROW).count()
        logger.warning("Лічильник не зчитано — рахуємо видимі рядки: %d (може бути занижено)", n)
        return n

    def _row_locator(self, rieltor_offer_id: str):
        """Локатор рядка, що містить посилання редагування з цим ID."""
        return self.page.locator(f"tr:has(a[href*='/offers/edit/{rieltor_offer_id}'])").first

    def _page_keys(self, page_no: int = 1) -> list[str]:
        """ID оголошень сторінки, чию дату актуальності ще треба підняти.

        Raises:
            ActualityPageError: таблиця не відрендерилась (таймаут, редірект на
                логін). Порожній список означає інше — «рядків немає», тобто
                чесно оброблену сторінку.
        """
        if not self._goto(self._url(page_no, self.PAGE_LIMIT)):
            raise ActualityPageError(f"сторінка {page_no} не відрендерилась")

        # Читаємо всі рядки одним проходом у браузері: по локатору на рядок
        # виходило ~10 с на 200 рядків, а сторінку ми перечитуємо багато разів.
        try:
            pairs = self.page.locator(self.ROW).evaluate_all(
                """(trs, iconPath) => trs.map(tr => {
                    const a = tr.querySelector("a[href*='/offers/edit/']");
                    const cell = Array.from(tr.querySelectorAll('td')).find(
                        td => td.querySelector(`svg path[d^="${iconPath}"]`)
                    );
                    return [a ? a.getAttribute('href') : '', cell ? cell.innerText : ''];
                })""",
                self.ICON_PATH_PREFIX,
            )
        except Exception as e:
            raise ActualityPageError(f"рядки сторінки {page_no} не прочитано: {e}") from e

        keys, fresh, unknown = self._keys_from_pairs(pairs)
        logger.info(
            "Сторінка %d: рядків %d, до оновлення %d, уже свіжих %d",
            page_no,
            len(pairs),
            len(keys),
            fresh,
        )
        if unknown:
            logger.warning(
                "Сторінка %d: у %d рядках не знайдено комірку з датою актуальності — "
                "схоже, сайт змінив іконку (ICON_PATH_PREFIX)",
                page_no,
                unknown,
            )
        return keys

    def _refresh_row(self, key: str) -> bool:
        """Підняти дату одного оголошення: спершу запитом, інакше — кліком."""
        if self._refresh_via_api(key):
            return True
        logger.info("Оголошення %s: запит не спрацював — пробуємо кнопку в рядку", key)
        return self._refresh_via_click(key)

    def _refresh_via_api(self, key: str) -> bool:
        """Той самий запит, що шле кнопка підтвердження (куки беремо із сесії)."""
        try:
            resp = self.page.request.get(self.API_REFRESH_TMPL.format(id=key))
            body = resp.text()
            ok = self._api_ok(resp.status, body, key)
            if not ok:
                logger.warning("Оголошення %s: відповідь %s %s", key, resp.status, body[:200])
        except Exception as e:
            logger.warning("Оголошення %s: запит не вдався: %s", key, e)
            ok = False
        # Пауза, щоб не лупити сайт чергою запитів упритул (і після збою теж —
        # якщо сайт відмовляє, поспіх лише погіршить справу).
        self.page.wait_for_timeout(int(self.REQUEST_DELAY_SEC * 1000))
        return ok

    def _refresh_via_click(self, key: str) -> bool:
        """Клік по іконці актуальності в рядку + підтвердження діалогу."""
        try:
            row = self._row_locator(key)
            row.wait_for(state="visible", timeout=self.RENDER_TIMEOUT_MS)
            row.locator(f"td:has({self.ICON}) {self.ICON}").first.click()

            dialog = self.page.locator(self.DIALOG).first
            dialog.wait_for(state="visible", timeout=self.DIALOG_TIMEOUT_MS)
            self.page.locator(self.DIALOG_CONFIRM).first.click()
            dialog.wait_for(state="detached", timeout=self.RENDER_TIMEOUT_MS)
            return True
        except Exception as e:
            logger.warning("Оновлення %s не вдалося: %s", key, e)
            return False
