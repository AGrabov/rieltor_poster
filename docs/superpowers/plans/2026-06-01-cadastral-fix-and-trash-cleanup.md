# Cadastral Fix + Trash Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the dead `kadastr.live` cadastral source with the `zem.center` JSON API (primary) + `kadastrova-karta.com` (fallback), and add a one-click "clean trash" feature that bulk-deletes all listings from rieltor.ua's «Закрита база».

**Architecture:** Task 1 rewrites the source chain inside the existing `cadastral_lookup.py` keeping all public signatures intact, with a shared house-matching helper unit-tested against fixtures. Task 2 adds a new `ClosedBaseCleaner` Playwright module driven by a `clean-trash` CLI subcommand and a confirm-gated dashboard button; the deletion loop control-flow is unit-tested via an injectable fake page.

**Tech Stack:** Python 3.14, `requests`, `beautifulsoup4`, Playwright (sync), Streamlit, pytest + monkeypatch (no `responses` lib available).

---

## File Structure

- `crm_data_parser/cadastral_lookup.py` — **modify**: new `_pick_by_house`, `_search_zem_center`; rewrite `lookup_cadastral_number` and `_search_kadastrova_karta`; delete kadastr.live code.
- `tests/test_cadastral_lookup.py` — **create**: unit tests for matcher + source parsers (mocked `requests.get`).
- `rieltor_handler/closed_base_cleaner.py` — **create**: `ClosedBaseCleaner` (count + delete loop).
- `tests/test_closed_base_cleaner.py` — **create**: loop control-flow tests with fake page.
- `main.py` — **modify**: add `clean-trash` subcommand + `phase_clean_trash()`.
- `dashboard.py` — **modify**: update cadastral caption; add "Очистити сміття" section.

---

# Task 1 — Cadastral source chain (zem.center primary)

### Task 1.1: House-matching helper `_pick_by_house`

**Files:**
- Modify: `crm_data_parser/cadastral_lookup.py`
- Test: `tests/test_cadastral_lookup.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cadastral_lookup.py`:

```python
"""Tests for cadastral number lookup: house matching + source parsers."""

from __future__ import annotations

from crm_data_parser import cadastral_lookup as cl


def test_pick_by_house_prefers_exact_over_suffix():
    # API returns 19-а, 19, 19-і in this order; exact "19" must win
    candidates = [
        ("8000000000:75:214:0033", "м.Київ, вулиця Львівська, 19-а"),
        ("8000000000:75:214:0010", "м.Київ, вулиця Львівська, 19"),
        ("8000000000:75:214:0012", "м.Київ, вулиця Львівська, 19-і"),
    ]
    assert cl._pick_by_house(candidates, "19") == "8000000000:75:214:0010"


def test_pick_by_house_falls_back_to_suffix_when_no_exact():
    candidates = [
        ("8000000000:75:214:0033", "м.Київ, вулиця Львівська, 19-а"),
        ("8000000000:75:214:0012", "м.Київ, вулиця Львівська, 19-і"),
    ]
    # No bare "19" → first suffix match returned
    assert cl._pick_by_house(candidates, "19") == "8000000000:75:214:0033"


def test_pick_by_house_no_house_returns_first():
    candidates = [
        ("8000000000:75:214:0033", "м.Київ, вулиця Львівська, 19-а"),
        ("8000000000:75:214:0010", "м.Київ, вулиця Львівська, 19"),
    ]
    assert cl._pick_by_house(candidates, "") == "8000000000:75:214:0033"


def test_pick_by_house_empty_candidates_returns_none():
    assert cl._pick_by_house([], "19") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cadastral_lookup.py -v`
Expected: FAIL with `AttributeError: module ... has no attribute '_pick_by_house'`

- [ ] **Step 3: Implement `_pick_by_house`**

In `crm_data_parser/cadastral_lookup.py`, after `_strip_street_prefix`, add:

```python
def _pick_by_house(candidates: list[tuple[str, str]], house: str) -> str | None:
    """Обрати найкращий кадастровий номер зі списку (cadnum, address).

    Пріоритет:
      1. Точний збіг номера будинку (окремий токен, без літери/дефіса після).
      2. Збіг із суфіксом (``19-а``, ``19/3``, ``19а``).
      3. Перший валідний номер.
    """
    if not candidates:
        return None
    house_norm = house.strip().lower()
    if not house_norm:
        return candidates[0][0]

    h = re.escape(house_norm)
    exact_re = re.compile(rf"(?:^|[,\s])({h})(?:[,\s]|$)")
    for cadnum, addr in candidates:
        if exact_re.search(addr.lower()):
            return cadnum

    loose_re = re.compile(rf"(?:^|[,\s])({h})(?=[\s,/\-а-яіїєґa-z])", re.IGNORECASE)
    for cadnum, addr in candidates:
        if loose_re.search(addr.lower()):
            return cadnum

    return candidates[0][0]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cadastral_lookup.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add crm_data_parser/cadastral_lookup.py tests/test_cadastral_lookup.py
git commit -m "feat(cadastral): add _pick_by_house matcher with exact>suffix priority"
```

---

### Task 1.2: `_search_zem_center` (new primary source)

**Files:**
- Modify: `crm_data_parser/cadastral_lookup.py`
- Test: `tests/test_cadastral_lookup.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cadastral_lookup.py`:

```python
class _FakeResp:
    def __init__(self, json_data=None, status_code=200, text=""):
        self._json = json_data if json_data is not None else {}
        self.status_code = status_code
        self.text = text

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


_ZEM_SAMPLE = {
    "items": [
        {"cadnum": "8000000000:75:214:0033", "address": "м.Київ, вулиця Львівська, 19-а"},
        {"cadnum": "8000000000:75:214:0010", "address": "м.Київ, вулиця Львівська, 19"},
        {"cadnum": "not-a-cadnum", "address": "junk"},
    ]
}


def test_search_zem_center_picks_exact_house(monkeypatch):
    def fake_get(url, **kwargs):
        assert "api.zem.center" in url
        return _FakeResp(json_data=_ZEM_SAMPLE)

    monkeypatch.setattr(cl.requests, "get", fake_get)
    assert cl._search_zem_center("Київ Львівська 19", "19") == "8000000000:75:214:0010"


def test_search_zem_center_handles_error(monkeypatch):
    def fake_get(url, **kwargs):
        raise cl.requests.exceptions.Timeout("slow")

    monkeypatch.setattr(cl.requests, "get", fake_get)
    assert cl._search_zem_center("Київ Львівська 19", "19") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cadastral_lookup.py -v -k zem`
Expected: FAIL with `AttributeError: ... '_search_zem_center'`

- [ ] **Step 3: Implement `_search_zem_center` and constants**

In `crm_data_parser/cadastral_lookup.py`, add near the other URL constants (after `_KK_HEADERS`):

```python
_ZEM_SEARCH_URL = "https://api.zem.center/api/search"
_ZEM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}
```

Then add the function (after `_pick_by_house`):

```python
def _search_zem_center(query: str, house: str) -> str | None:
    """Пошук кадастрового номера через zem.center JSON API (основне джерело).

    GET https://api.zem.center/api/search?q=<query>&size=20 → {"items": [...]}.
    Кожен item має ``cadnum`` та ``address``. Повертає найкращий збіг за
    номером будинку або None.
    """
    try:
        resp = requests.get(
            _ZEM_SEARCH_URL,
            params={"q": query, "size": "20"},
            headers=_ZEM_HEADERS,
            timeout=(5, 12),  # (connect, read)
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()

        items = (resp.json() or {}).get("items") or []
        candidates: list[tuple[str, str]] = []
        for item in items:
            cadnum = (item.get("cadnum") or "").strip()
            if _CADNUM_RE.match(cadnum):
                candidates.append((cadnum, item.get("address") or ""))
        return _pick_by_house(candidates, house)
    except requests.exceptions.Timeout:
        logger.debug("Timeout zem.center для '%s'", query)
        return None
    except Exception:
        logger.warning("Помилка zem.center для '%s'", query, exc_info=True)
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cadastral_lookup.py -v -k zem`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add crm_data_parser/cadastral_lookup.py tests/test_cadastral_lookup.py
git commit -m "feat(cadastral): add zem.center JSON search source"
```

---

### Task 1.3: Rewrite `_search_kadastrova_karta` to use `_pick_by_house`

**Files:**
- Modify: `crm_data_parser/cadastral_lookup.py`
- Test: `tests/test_cadastral_lookup.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cadastral_lookup.py`:

```python
_KK_HTML = """
<a data-action="search#linkClicked">
  <div class="font-bold">8000000000:75:214:0033</div>
  <div class="text-gray-500">м.Київ, вулиця Львівська, 19-а</div>
</a>
<a data-action="search#linkClicked">
  <div class="font-bold">8000000000:75:214:0010</div>
  <div class="text-gray-500">м.Київ, вулиця Львівська, 19</div>
</a>
"""


def test_search_kadastrova_karta_picks_exact_house(monkeypatch):
    def fake_get(url, **kwargs):
        return _FakeResp(text=_KK_HTML)

    monkeypatch.setattr(cl.requests, "get", fake_get)
    assert cl._search_kadastrova_karta("Київ Львівська 19", "19") == "8000000000:75:214:0010"
```

- [ ] **Step 2: Run test to verify it fails or passes**

Run: `uv run pytest tests/test_cadastral_lookup.py -v -k kadastrova`
Expected: may FAIL (current code returns first valid `...0033` because its inline regex matches `19-а`). This is the bug we fix.

- [ ] **Step 3: Replace `_search_kadastrova_karta` body**

Replace the entire existing `_search_kadastrova_karta` function with:

```python
def _search_kadastrova_karta(query: str, house: str) -> str | None:
    """Пошук кадастрового номера через kadastrova-karta.com (fallback).

    Парсить Turbo Stream HTML відповідь — без Playwright.
    """
    try:
        resp = requests.get(
            _KK_SEARCH_URL,
            params={"q": query},
            headers=_KK_HEADERS,
            timeout=(5, 8),  # (connect, read)
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        candidates: list[tuple[str, str]] = []
        for a_tag in soup.select("a[data-action='search#linkClicked']"):
            cadnum_div = a_tag.select_one("div.font-bold")
            addr_div = a_tag.select_one("div.text-gray-500")
            if not cadnum_div:
                continue
            cadnum = cadnum_div.get_text(strip=True)
            if not _CADNUM_RE.match(cadnum):
                continue
            addr = addr_div.get_text(strip=True) if addr_div else ""
            candidates.append((cadnum, addr))
        return _pick_by_house(candidates, house)
    except requests.exceptions.Timeout:
        logger.debug("Timeout kadastrova-karta.com для '%s'", query)
        return None
    except Exception:
        logger.warning("Помилка kadastrova-karta.com для '%s'", query, exc_info=True)
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cadastral_lookup.py -v -k kadastrova`
Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
git add crm_data_parser/cadastral_lookup.py tests/test_cadastral_lookup.py
git commit -m "refactor(cadastral): kadastrova-karta uses shared _pick_by_house matcher"
```

---

### Task 1.4: Rewrite `lookup_cadastral_number` chain + delete kadastr.live

**Files:**
- Modify: `crm_data_parser/cadastral_lookup.py`
- Test: `tests/test_cadastral_lookup.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cadastral_lookup.py`:

```python
def test_lookup_uses_zem_first(monkeypatch):
    calls = []
    monkeypatch.setattr(cl, "_search_zem_center", lambda q, h: calls.append(("zem", q)) or "8000000000:75:214:0010")
    monkeypatch.setattr(cl, "_search_kadastrova_karta", lambda q, h: calls.append(("kk", q)) or None)
    result = cl.lookup_cadastral_number("Київ", "вул. Львівська", "19")
    assert result == "8000000000:75:214:0010"
    # zem.center called first, kadastrova-karta not reached
    assert calls[0][0] == "zem"
    assert all(c[0] != "kk" for c in calls)


def test_lookup_falls_back_to_kadastrova(monkeypatch):
    monkeypatch.setattr(cl, "_search_zem_center", lambda q, h: None)
    monkeypatch.setattr(cl, "_search_kadastrova_karta", lambda q, h: "8000000000:75:214:0099")
    result = cl.lookup_cadastral_number("Київ", "вул. Львівська", "19")
    assert result == "8000000000:75:214:0099"


def test_lookup_no_kadastr_live_references():
    # kadastr.live is dead — ensure it is fully removed from the module
    import inspect
    src = inspect.getsource(cl)
    assert "kadastr.live" not in src
    assert "_search_raw" not in src
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cadastral_lookup.py -v -k lookup`
Expected: FAIL — `test_lookup_no_kadastr_live_references` fails (kadastr.live still present); `test_lookup_uses_zem_first` fails (zem not in chain yet).

- [ ] **Step 3: Rewrite the chain and delete dead code**

In `crm_data_parser/cadastral_lookup.py`:

(a) **Delete** these now-unused kadastr.live items: the `_SEARCH_URL` constant, the `_HEADERS` constant, the `_search_raw` function, and the `_best_cadnum` function.

(b) **Replace** the entire body of `lookup_cadastral_number` (keep its signature and docstring intro, update the strategy text) with:

```python
def lookup_cadastral_number(city: str, street: str, house: str) -> str | None:
    """Знайти кадастровий номер ділянки за адресою.

    Стратегія (zem.center JSON API, потім kadastrova-karta.com як fallback):
      1. zem.center: місто + вулиця + будинок
      2. zem.center: місто + вулиця
      3. kadastrova-karta.com: місто + вулиця + будинок
      4. kadastrova-karta.com: місто + вулиця

    Returns:
        Рядок у форматі ``XXXXXXXXXX:XX:XXX:XXXX`` або ``None``, якщо не знайдено.
    """
    street_clean = _strip_street_prefix(street)
    city_clean = city.strip()
    house_orig = house.strip()

    full = " ".join(p for p in [city_clean, street_clean, house_orig] if p)
    short = " ".join(p for p in [city_clean, street_clean] if p)
    # Preserve order, drop empties and duplicates (full == short when no house)
    queries: list[str] = []
    for q in (full, short):
        if q and q not in queries:
            queries.append(q)
    if not queries:
        return None

    for q in queries:
        cadnum = _search_zem_center(q, house_orig)
        if cadnum:
            logger.debug("Знайдено zem.center: %s (запит '%s')", cadnum, q)
            return cadnum

    for q in queries:
        cadnum = _search_kadastrova_karta(q, house_orig)
        if cadnum:
            logger.debug("Знайдено kadastrova-karta.com: %s (запит '%s')", cadnum, q)
            return cadnum

    return None
```

(c) Update the module docstring (line 1) from `via kadastr.live та kadastrova-karta.com` to `via zem.center та kadastrova-karta.com`.

- [ ] **Step 4: Run the full cadastral test file**

Run: `uv run pytest tests/test_cadastral_lookup.py -v`
Expected: all passed (10 tests)

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check crm_data_parser/cadastral_lookup.py
git add crm_data_parser/cadastral_lookup.py tests/test_cadastral_lookup.py
git commit -m "feat(cadastral): zem.center primary chain, remove dead kadastr.live"
```

---

### Task 1.5: Live smoke check + dashboard caption

**Files:**
- Modify: `dashboard.py:352`

- [ ] **Step 1: Manual live smoke (optional, network)**

Run:
```bash
uv run python -c "from crm_data_parser.cadastral_lookup import lookup_cadastral_number as l; print(l('Київ','Львівська','19'))"
```
Expected: prints a cadnum like `8000000000:75:214:0010` (or another valid `XXXXXXXXXX:XX:XXX:XXXX`). If `None`, the live API may be rate-limiting — not a code failure.

- [ ] **Step 2: Update dashboard caption**

In `dashboard.py`, find the line (~352):
```python
            st.markdown("**Кадастрові номери** (БД → kadastr.live)")
```
Replace with:
```python
            st.markdown("**Кадастрові номери** (БД → zem.center)")
```

- [ ] **Step 3: Commit**

```bash
git add dashboard.py
git commit -m "docs(dashboard): cadastral source label kadastr.live → zem.center"
```

---

# Task 2 — Clean trash from rieltor.ua «Закрита база»

### Task 2.1: Discovery — capture the confirm-dialog selector

**Files:**
- Create (temporary): `_discover_closed_base.py` (deleted at end of task)

> **Why:** The list URL and row/delete/dialog selectors are known (from the spec). The
> only unknown is the **confirm button inside the reason dialog** and the post-delete
> settle signal. This task captures them on the live account, then bakes them in.

- [ ] **Step 1: Write the discovery script**

Create `_discover_closed_base.py`:

```python
"""One-off: open Закрита база, click delete on the first row, dump the dialog HTML."""
import os
from dotenv import load_dotenv
from rieltor_handler.rieltor_session import RieltorCredentials, RieltorSession

load_dotenv()
URL = "https://my.rieltor.ua/offers/management?page=1&limit=25&mode=-30"

with RieltorSession(
    RieltorCredentials(phone=os.environ["PHONE"], password=os.environ["PASSWORD"]),
    headless=False,
) as s:
    s.login()
    s.page.goto(URL, wait_until="domcontentloaded")
    radios = s.page.locator("td.MuiTableCell-paddingCheckbox .MuiRadio-root")
    print("rows:", radios.count())
    radios.first.click()
    s.page.get_by_role("button", name="Видалити").first.click()
    dialog = s.page.locator("div[role='dialog']")
    dialog.wait_for(state="visible")
    print("=== DIALOG HTML ===")
    print(dialog.inner_html())
    s.page.wait_for_timeout(8000)  # leave time to eyeball it
```

- [ ] **Step 2: Run it (headed) and read the dialog HTML**

Run: `uv run python _discover_closed_base.py`
Expected: prints `rows: N` and the dialog inner HTML. Note from the output:
- the **confirm button** (its text — likely «Видалити»/«Підтвердити» — and whether it sits inside `div[role='dialog']`);
- the **reason radios** container (confirm the first is selectable via `.MuiRadio-root`).

Record these two facts; they parameterize Task 2.2.

- [ ] **Step 3: Delete the discovery script**

```bash
rm _discover_closed_base.py
```

No commit (temporary artifact only).

---

### Task 2.2: `ClosedBaseCleaner` module

**Files:**
- Create: `rieltor_handler/closed_base_cleaner.py`
- Test: `tests/test_closed_base_cleaner.py`

- [ ] **Step 1: Write the failing loop-control test**

Create `tests/test_closed_base_cleaner.py`:

```python
"""Loop control-flow tests for ClosedBaseCleaner (no real browser)."""

from __future__ import annotations

from rieltor_handler.closed_base_cleaner import ClosedBaseCleaner


class _FakeCleaner(ClosedBaseCleaner):
    """Override browser-bound methods to simulate a shrinking list."""

    def __init__(self, initial_count: int):
        self._remaining = initial_count
        self.delete_calls = 0

    def count(self) -> int:
        return self._remaining

    def _delete_first(self) -> bool:
        if self._remaining <= 0:
            return False
        self._remaining -= 1
        self.delete_calls += 1
        return True


def test_clean_deletes_all_until_empty():
    c = _FakeCleaner(initial_count=5)
    deleted = c.clean()
    assert deleted == 5
    assert c.delete_calls == 5
    assert c.count() == 0


def test_clean_respects_max_count():
    c = _FakeCleaner(initial_count=10)
    deleted = c.clean(max_count=3)
    assert deleted == 3
    assert c.count() == 7


def test_clean_dry_run_deletes_nothing():
    c = _FakeCleaner(initial_count=4)
    would = c.clean(dry_run=True)
    assert would == 4
    assert c.delete_calls == 0
    assert c.count() == 4
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_closed_base_cleaner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rieltor_handler.closed_base_cleaner'`

- [ ] **Step 3: Implement the module**

Create `rieltor_handler/closed_base_cleaner.py`. Set `_DIALOG_CONFIRM` and the
post-delete settle from Task 2.1 findings (defaults below assume the confirm
button is labelled «Видалити» inside the dialog):

```python
"""Масове видалення об'єктів із «Закритої бази» rieltor.ua."""

from __future__ import annotations

from playwright.sync_api import Page

from setup_logger import setup_logger

logger = setup_logger(__name__)


class ClosedBaseCleaner:
    """Видаляє всі об'єкти із «Закритої бази» (mode=-30) по одному.

    Сайт не має масового видалення, тому цикл: вибрати radio рядка →
    «Видалити» → у діалозі обрати першу причину → підтвердити → повтор.
    """

    CLOSED_BASE_URL = "https://my.rieltor.ua/offers/management?page=1&limit=25&mode=-30"

    ROW_RADIO = "td.MuiTableCell-paddingCheckbox .MuiRadio-root"
    DELETE_BUTTON = "button:has-text('Видалити')"
    DIALOG = "div[role='dialog']"
    DIALOG_REASON_RADIO = "div[role='dialog'] .MuiRadio-root"
    DIALOG_CONFIRM = "div[role='dialog'] button:has-text('Видалити')"

    def __init__(self, page: Page) -> None:
        self.page = page

    def count(self) -> int:
        """Перейти на сторінку «Закритої бази» й порахувати рядки."""
        self.page.goto(self.CLOSED_BASE_URL, wait_until="domcontentloaded")
        self.page.wait_for_timeout(1500)  # MUI table renders after data fetch
        return self.page.locator(self.ROW_RADIO).count()

    def _delete_first(self) -> bool:
        """Видалити перший об'єкт у списку. Повертає False, якщо список порожній."""
        radios = self.page.locator(self.ROW_RADIO)
        if radios.count() == 0:
            return False

        radios.first.click()
        self.page.locator(self.DELETE_BUTTON).first.click()

        dialog = self.page.locator(self.DIALOG)
        dialog.wait_for(state="visible")
        # Перша причина — «Просто не хочу рекламувати»
        self.page.locator(self.DIALOG_REASON_RADIO).first.click()
        self.page.locator(self.DIALOG_CONFIRM).first.click()
        dialog.wait_for(state="detached")
        self.page.wait_for_timeout(1000)  # let the list reload
        return True

    def clean(self, max_count: int | None = None, dry_run: bool = False) -> int:
        """Видалити об'єкти із «Закритої бази».

        Args:
            max_count: Максимум видалень за прогін (None = всі).
            dry_run:   Якщо True — лише порахувати, нічого не видаляти.

        Returns:
            Кількість видалених (або кількість наявних при dry_run).
        """
        total = self.count()
        if dry_run:
            logger.info("[dry-run] У «Закритій базі» об'єктів: %d", total)
            return total

        logger.info("Початок очистки «Закритої бази»: %d об'єктів", total)
        deleted = 0
        while True:
            if max_count is not None and deleted >= max_count:
                logger.info("Досягнуто ліміту видалень: %d", max_count)
                break
            if not self._delete_first():
                logger.info("«Закрита база» порожня")
                break
            deleted += 1
            logger.info("Видалено %d об'єкт(ів)", deleted)
            # Re-navigate to refresh the list state before the next deletion
            self.page.goto(self.CLOSED_BASE_URL, wait_until="domcontentloaded")
            self.page.wait_for_timeout(1500)

        logger.info("Очистку завершено: видалено %d об'єкт(ів)", deleted)
        return deleted
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_closed_base_cleaner.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add rieltor_handler/closed_base_cleaner.py tests/test_closed_base_cleaner.py
git commit -m "feat(cleaner): ClosedBaseCleaner bulk-deletes Закрита база listings"
```

---

### Task 2.3: `clean-trash` CLI subcommand

**Files:**
- Modify: `main.py` (add `phase_clean_trash`, parser entry, dispatch)

- [ ] **Step 1: Add `phase_clean_trash` function**

In `main.py`, after `phase_cadastral` (around line 606), add:

```python
# ── clean-trash: bulk-delete rieltor.ua «Закрита база» ──────────────


def phase_clean_trash(
    max_count: int | None = None,
    dry_run: bool = False,
    headless: bool = True,
    debug: bool = False,
) -> int:
    """Масово видалити об'єкти із «Закритої бази» на rieltor.ua.

    Returns:
        Кількість видалених об'єктів (або наявних при dry_run).
    """
    from rieltor_handler.closed_base_cleaner import ClosedBaseCleaner
    from rieltor_handler.rieltor_session import RieltorCredentials, RieltorSession

    phone = os.environ.get("PHONE", "").strip()
    password = os.environ.get("PASSWORD", "").strip()
    if not phone or not password:
        logger.error("PHONE та PASSWORD повинні бути задані в .env")
        return 0

    with RieltorSession(
        RieltorCredentials(phone=phone, password=password),
        headless=headless,
        debug=debug,
    ) as session:
        session.login()
        cleaner = ClosedBaseCleaner(session.page)
        deleted = cleaner.clean(max_count=max_count, dry_run=dry_run)

    logger.info("clean-trash завершено: %d", deleted)
    return deleted
```

- [ ] **Step 2: Register the subcommand in `build_parser`**

In `build_parser`, after the `cadastral` block (around line 698), add:

```python
    # clean-trash
    p_clean = sub.add_parser("clean-trash", help="Bulk-delete rieltor.ua «Закрита база»")
    p_clean.add_argument("--max-count", type=int, help="Max offers to delete")
    p_clean.add_argument("--dry-run", action="store_true", help="Count only, delete nothing")
```

- [ ] **Step 3: Add dispatch in `main`**

In `main`, after the `elif args.command == "cadastral":` block (around line 743), add:

```python
        elif args.command == "clean-trash":
            phase_clean_trash(
                max_count=args.max_count,
                dry_run=args.dry_run,
                headless=headless,
                debug=args.debug,
            )
```

- [ ] **Step 4: Verify CLI wiring (no browser)**

Run: `uv run python main.py clean-trash --help`
Expected: help text showing `--max-count` and `--dry-run`.

- [ ] **Step 5: Commit**

```bash
git add main.py
git commit -m "feat(cli): add clean-trash subcommand for Закрита база cleanup"
```

---

### Task 2.4: Dashboard "Очистити сміття" section

**Files:**
- Modify: `dashboard.py` (session state + new bordered section)

- [ ] **Step 1: Add session-state key**

In `dashboard.py`, in the session-state block (after the `cadastral_proc` init, ~line 153), add:

```python
if "cleanup_proc" not in st.session_state:
    st.session_state.cleanup_proc = None
```

- [ ] **Step 2: Add the bordered section**

In `dashboard.py`, in the `with right:` column, immediately **after** the
«Кадастрові номери» `st.container(border=True)` block (ends ~line 389) and
**before** the «Схеми форм» block, insert:

```python
    # Очистка сміття на rieltor.ua
    with st.container(border=True):
        st.markdown("**🗑 Очистити сміття на rieltor.ua**")
        st.caption("Видаляє ВСІ об'єкти із «Закритої бази» (неудачні/неправильні чернетки). Незворотно!")
        tc1, tc2 = st.columns([3, 1])
        with tc1:
            confirm_cleanup = st.checkbox(
                "Я підтверджую видалення",
                value=False,
                key="confirm_cleanup",
            )
        with tc2:
            max_count_clean = st.number_input(
                "Макс.",
                min_value=0,
                value=0,
                key="max_count_cleanup",
                help="0 = без обмежень",
            )
        cleanup_btn = st.button(
            "🗑 Очистити «Закриту базу»",
            use_container_width=True,
            disabled=not confirm_cleanup or proc_is_running(st.session_state.cleanup_proc),
        )

        if cleanup_btn:
            cmd = ["uv", "run", "python", "main.py", "clean-trash"]
            if max_count_clean:
                cmd += ["--max-count", str(max_count_clean)]
            st.session_state.cleanup_proc = launch(cmd, log_level=st.session_state.log_level)
            st.toast("Очистку «Закритої бази» запущено!", icon="🗑")

        if proc_is_running(st.session_state.cleanup_proc):
            _col_info, _col_stop = st.columns([3, 1])
            with _col_info:
                st.info("⏳ Очистка виконується...")
            with _col_stop:
                if st.button("⏹ Зупинити", key="stop_cleanup", use_container_width=True):
                    stop_proc(st.session_state.cleanup_proc)
                    st.toast("Очистку зупинено", icon="⏹")
                    st.rerun()
        elif st.session_state.cleanup_proc is not None:
            rc = st.session_state.cleanup_proc.returncode
            if rc == 0:
                st.success("✅ Очистку завершено")
            else:
                st.error(f"❌ Очистка завершилась з кодом {rc}")
```

> Note: `clean-trash` honours the global `HEADLESS` env / `--headless` flag the
> same way other commands do. The dashboard's `launch()` does not pass headless
> for this command, so it runs headed by default (useful to watch deletions);
> the user's global headless toggle does not apply here intentionally.

- [ ] **Step 3: Manual dashboard smoke**

Run: `uv run streamlit run dashboard.py`
Expected: new "🗑 Очистити сміття на rieltor.ua" section appears; the button is
disabled until the "Я підтверджую видалення" checkbox is ticked.

- [ ] **Step 4: Commit**

```bash
git add dashboard.py
git commit -m "feat(dashboard): add confirm-gated Закрита база cleanup button"
```

---

### Task 2.5: End-to-end manual verification (live account)

**Files:** none (manual)

- [ ] **Step 1: Dry-run count**

Run: `uv run python main.py clean-trash --dry-run`
Expected: log line `[dry-run] У «Закритій базі» об'єктів: N`. Confirm N matches
what you see on the site.

- [ ] **Step 2: Delete a single item to validate the flow**

Run: `uv run python main.py clean-trash --max-count 1`
Expected: one object deleted; verify on the site it's gone and the reason was
«Просто не хочу рекламувати». If the confirm-button selector was wrong, fix
`DIALOG_CONFIRM` in `closed_base_cleaner.py` using Task 2.1 findings, re-run.

- [ ] **Step 3: Full cleanup**

Run: `uv run python main.py clean-trash`
Expected: loops until «Закрита база» is empty; final log `видалено N`.

- [ ] **Step 4: Commit any selector fixes**

```bash
git add rieltor_handler/closed_base_cleaner.py
git commit -m "fix(cleaner): correct Закрита база dialog selectors from live run"
```

---

## Final verification

- [ ] **Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: all tests pass (watermark + cadastral + cleaner).

- [ ] **Lint**

Run: `uv run ruff check .`
Expected: no new errors in `crm_data_parser/cadastral_lookup.py`,
`rieltor_handler/closed_base_cleaner.py`, `main.py`, `dashboard.py`.

---

## Self-Review Notes (spec coverage)

- ✅ zem.center primary JSON source → Task 1.2
- ✅ kadastrova-karta.com fallback via shared matcher → Task 1.3
- ✅ kadastr.live fully removed → Task 1.4 (incl. guard test)
- ✅ house exact>suffix matching → Task 1.1
- ✅ unit tests on fixtures, no live network in CI → Tasks 1.1–1.4
- ✅ dashboard caption update → Task 1.5
- ✅ ClosedBaseCleaner (count + delete loop, dry_run, max_count) → Task 2.2
- ✅ known selectors baked in, only confirm-button discovered → Tasks 2.1–2.2
- ✅ CLI `clean-trash --max-count --dry-run` → Task 2.3
- ✅ dashboard section with confirm checkbox → Task 2.4
- ✅ public signatures unchanged (`lookup_cadastral_number`,
  `enrich_offer_data_with_cadastral`, `fill_missing_cadastral_numbers`) → Task 1.4
