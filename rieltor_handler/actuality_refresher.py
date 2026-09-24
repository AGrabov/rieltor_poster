"""Оновлення дати актуальності опублікованих оголошень rieltor.ua (вкладка mode=10).

Сайт краще ранжує оголошення зі свіжою датою актуальності, тож дату треба
піднімати регулярно — але лише тим об'єктам, які ще актуальні в CRM (закриті
спершу йдуть у «Мої угоди», див. `DealsMover`).

Це не лише про ранжування: без підтвердження сайт сам знімає оголошення в
чернетки. Строки різні — оренду знімає рівно через 30 днів (перевірено наживо
2026-09-24: 61 знятих оголошень, усі оренда, усі рівно 30 днів), продаж живе
довше (активні висять і 78 днів). Тому `refresh_in_priority_order` бере спершу
оренду (`operType=2`), а вже потім решту: у спільній черзі «найстаріші зверху»
попереду йшов би продаж, і оренда на 29-му дні могла б не влізти в денний
ліміт. Обсяги: ~795 оренди на 30 днів ≈ 27 підняттів на добу, тобто денного
ліміту вистачає з запасом.

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

Сайт обмежує темп підняттів: після ~200 поспіль API віддає
`{"error":"refresh_update_limit","status":"ERROR"}`, і клік у такому стані лише
вдає успіх — діалог підтверджується, а дата не рухається. Тому ліміт зупиняє
весь прохід, а не тягне фолбек.

Ліміт тимчасовий: о 03:47 сайт відмовляв, о 09:30 того ж дня знову приймав
(перевірено наживо 2026-09-24). Тому прогін із лімітом НЕ закриває добу —
наступний запуск добере решту. З тієї ж причини між запитами тримаємо паузу
`REQUEST_DELAY_SEC` (2 с): денна норма — десятки оголошень, поспішати нікуди,
а повільний темп рідше впирається в обмеження.

`refresh_all` повертає `RefreshOutcome(done, completed, limit_reached)`: `completed=False`
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


class ActualityLimitReached(Exception):
    """Сайт вичерпав свій ліміт підняттів дати — сьогодні більше не дасть."""


class RefreshOutcome(NamedTuple):
    """Результат проходу: скільки оновлено, чи дійшли до кінця, чи спинив ліміт сайту."""

    done: int
    completed: bool
    limit_reached: bool = False


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
    # Фільтр «Розділ» у шапці таблиці: operType=2 — оренда, 1 — продаж
    # (перевірено наживо; параметр лишається в URL сторінки).
    OPER_RENT = 2
    OPER_SALE = 1
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
    # Пауза між запитами. Сайт обмежує темп (`refresh_update_limit`), причому
    # ліміт тимчасовий — за кілька годин відпускає. Тому йдемо повільно: 2 с на
    # оголошення — це ~40 денної норми за півтори хвилини, поспішати нікуди.
    REQUEST_DELAY_SEC = 2.0
    LIMIT_MARKER = "refresh_update_limit"  # сайт тимчасово не дає піднімати далі
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

    def __init__(self, page: Page, request_delay_sec: float | None = None) -> None:
        self.page = page
        self.request_delay_sec = self.REQUEST_DELAY_SEC if request_delay_sec is None else request_delay_sec

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
    def _api_result(cls, status: int, body: str | None, key: str) -> str:
        """Розібрати відповідь: "ok" | "limit" | "fail".

        `limit` — сайт відмовив через свій ліміт на підняття дати
        (`{"error":"refresh_update_limit","status":"ERROR"}`, перевірено наживо
        2026-09-24 після ~200 оновлень поспіль). Це не проблема конкретного
        рядка, а стоп для всього проходу: решта запитів теж відмовить, а клік
        у такому стані лише вдає успіх — діалог підтверджується, дата не рухається.
        """
        if cls._api_ok(status, body, key):
            return "ok"
        if body and cls.LIMIT_MARKER in body:
            return "limit"
        return "fail"

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

    def refresh_in_priority_order(
        self,
        max_count: int | None = None,
        dry_run: bool = False,
        progress_cb: Callable[[int], None] | None = None,
    ) -> RefreshOutcome:
        """Пройти спершу оренду, потім усе інше.

        Строки автозняття різні: оренду сайт знімає рівно через 30 днів без
        підтвердження (перевірено наживо 2026-09-24 на 61 знятому оголошенні —
        усі оренда, усі рівно 30 днів), а продаж висить і 78 днів. Спільне
        сортування «найстаріші зверху» виносить наперед саме продаж, тож оренда
        на 29-му дні могла б не влізти в денний ліміт і поїхати в чернетки.

        Другий прохід іде без фільтра: оренда там уже свіжа («Вчора») і просто
        пропускається, тож повторної роботи немає.

        Args:
            max_count: спільний бюджет на обидва проходи.
        """
        done = 0
        completed = True
        for oper_type in (self.OPER_RENT, None):
            budget = None if max_count is None else max_count - done
            if budget is not None and budget <= 0:
                completed = False
                break
            outcome = self.refresh_all(
                max_count=budget,
                dry_run=dry_run,
                progress_cb=progress_cb,
                oper_type=oper_type,
            )
            done += outcome.done
            completed = completed and outcome.completed
            if outcome.limit_reached:
                return RefreshOutcome(done, False, limit_reached=True)
        return RefreshOutcome(done, completed)

    def refresh_all(
        self,
        max_count: int | None = None,
        dry_run: bool = False,
        progress_cb: Callable[[int], None] | None = None,
        oper_type: int | None = None,
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
        total = self.count(oper_type)
        scope = {self.OPER_RENT: "оренда", self.OPER_SALE: "продаж"}.get(oper_type, "усі")
        if total <= 0:
            logger.info("Опублікованих оголошень немає (%s) — нічого оновлювати", scope)
            return RefreshOutcome(0, True)

        logger.info(
            "Оновлення дати актуальності [%s]: %d оголошень на сайті (max_count=%s, dry_run=%s)",
            scope,
            total,
            max_count,
            dry_run,
        )

        if dry_run:
            return self._count_stale(total, max_count, oper_type)

        processed: set[str] = set()
        done = 0
        failures = 0
        while max_count is None or done < max_count:
            try:
                keys = [k for k in self._page_keys(oper_type=oper_type) if k not in processed]
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
                try:
                    refreshed_row = self._refresh_row(key)
                except ActualityLimitReached as e:
                    logger.warning(
                        "Денний ліміт сайту на підняття дати вичерпано (%s). Оновлено %d — решта чекає наступного дня",
                        e,
                        done,
                    )
                    return RefreshOutcome(done, False, limit_reached=True)
                if refreshed_row:
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

    def _count_stale(self, total: int, max_count: int | None, oper_type: int | None = None) -> RefreshOutcome:
        """Скільки рядків потребують оновлення (dry-run: обходимо сторінки, не клікаючи)."""
        seen: set[str] = set()
        for page_no in self._page_plan(total):
            if max_count is not None and len(seen) >= max_count:
                break
            try:
                keys = self._page_keys(page_no, oper_type)
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

    def _url(self, page_no: int, limit: int, oper_type: int | None = None) -> str:
        url = self.PUBLISHED_URL_TMPL.format(page=max(1, page_no), limit=max(1, limit))
        return f"{url}&operType={oper_type}" if oper_type else url

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

    def count(self, oper_type: int | None = None) -> int:
        """Скільки оголошень на вкладці «Опубліковані» (з MUI-пагінації)."""
        self._goto(self._url(1, self.COUNT_LIMIT, oper_type))
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
        self._goto(self._url(1, self.PAGE_LIMIT, oper_type))
        n = self.page.locator(self.ROW).count()
        logger.warning("Лічильник не зчитано — рахуємо видимі рядки: %d (може бути занижено)", n)
        return n

    def _row_locator(self, rieltor_offer_id: str):
        """Локатор рядка, що містить посилання редагування з цим ID."""
        return self.page.locator(f"tr:has(a[href*='/offers/edit/{rieltor_offer_id}'])").first

    def _page_keys(self, page_no: int = 1, oper_type: int | None = None) -> list[str]:
        """ID оголошень сторінки, чию дату актуальності ще треба підняти.

        Raises:
            ActualityPageError: таблиця не відрендерилась (таймаут, редірект на
                логін). Порожній список означає інше — «рядків немає», тобто
                чесно оброблену сторінку.
        """
        if not self._goto(self._url(page_no, self.PAGE_LIMIT, oper_type)):
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
        """Підняти дату одного оголошення: спершу запитом, інакше — кліком.

        Raises:
            ActualityLimitReached: сайт вичерпав денний ліміт підняттів.
        """
        result = self._refresh_via_api(key)
        if result == "ok":
            return True
        if result == "limit":
            raise ActualityLimitReached(f"сайт відмовив на оголошенні {key}")
        logger.info("Оголошення %s: запит не спрацював — пробуємо кнопку в рядку", key)
        return self._refresh_via_click(key)

    def _refresh_via_api(self, key: str) -> str:
        """Той самий запит, що шле кнопка підтвердження (куки беремо із сесії).

        Returns:
            "ok" | "limit" | "fail".
        """
        try:
            resp = self.page.request.get(self.API_REFRESH_TMPL.format(id=key))
            body = resp.text()
            result = self._api_result(resp.status, body, key)
            if result != "ok":
                logger.warning("Оголошення %s: відповідь %s %s", key, resp.status, body[:200])
        except Exception as e:
            logger.warning("Оголошення %s: запит не вдався: %s", key, e)
            result = "fail"
        # Пауза, щоб не лупити сайт чергою запитів упритул (і після збою теж —
        # якщо сайт відмовляє, поспіх лише погіршить справу).
        self.page.wait_for_timeout(int(self.request_delay_sec * 1000))
        return result

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
