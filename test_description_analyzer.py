"""Тест якості вилучення полів із описів оголошень.

Бере N записів із бази, запускає DescriptionAnalyzer і зберігає результат у лог-файл.

Запуск:
    python test_description_analyzer.py [кількість] [property_type]
    python test_description_analyzer.py 10 Квартира
    python test_description_analyzer.py 5 Комерційна
"""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
from pathlib import Path

from setup_logger import init_logging

# ── Конфігурація ─────────────────────────────────────────────────────────────

ROOT = Path(__file__).parent
DB_PATH = ROOT / "offers.db"
SCHEMA_DIR = ROOT / "schemas" / "schema_dump"
LOG_PATH = ROOT / "logs" / "description_analyzer_test.log"

N = int(sys.argv[1]) if len(sys.argv) > 1 else 10
FILTER_TYPE = sys.argv[2] if len(sys.argv) > 2 else None

# Службові ключі offer_data, які не аналізуються
SKIP_KEYS = {
    "offer_type",
    "property_type",
    "article",
    "public_link",
    "responsible_person",
    "advertising",
    "photo_download_link",
    "address",
    "apartment",
    "personal_notes",
}

COL_KEY = 35
COL_VAL = 55

# ── Логування ─────────────────────────────────────────────────────────────────

init_logging(level="DEBUG", filename=str(LOG_PATH), clear_on_start=True)
log = logging.getLogger("description_analyzer_test")


# ── Схема ────────────────────────────────────────────────────────────────────


def load_schema(deal_type: str, property_type: str) -> list[dict]:
    deal_dir = "sell" if deal_type == "Продаж" else "lease"
    path = SCHEMA_DIR / deal_dir / f"{property_type}.json"
    if not path.exists():
        for p in (SCHEMA_DIR / deal_dir).glob(f"{property_type}*.json"):
            path = p
            break
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw["fields"] if isinstance(raw, dict) else raw
    return []


# ── База ─────────────────────────────────────────────────────────────────────


def fetch_offers(n: int, property_type: str | None) -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    if property_type:
        rows = conn.execute(
            "SELECT * FROM offers WHERE property_type = ? LIMIT ?",
            (property_type, n),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM offers LIMIT ?", (n,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Допоміжні функції ────────────────────────────────────────────────────────


def _fmt(v: object, max_len: int = 70) -> str:
    if isinstance(v, list):
        s = "[" + ", ".join(str(x) for x in v) + "]"
    else:
        s = str(v)
    return s if len(s) <= max_len else s[:max_len] + "…"


def _values_match(crm_v: object, ext_v: object) -> bool:
    def norm(v: object) -> str:
        if isinstance(v, list):
            return ", ".join(sorted(str(x).lower().strip() for x in v))
        return str(v).lower().strip()

    return norm(crm_v) == norm(ext_v)


def _row(label: str, value: object, prefix: str = "  ") -> str:
    return f"{prefix}{label:<{COL_KEY}} {_fmt(value):<{COL_VAL}}"


# ── Основна логіка ───────────────────────────────────────────────────────────


def run() -> None:
    from crm_data_parser.description_analyzer import DescriptionAnalyzer

    offers = fetch_offers(N, FILTER_TYPE)
    if not offers:
        log.warning("Немає записів у базі.")
        return

    total_correct = 0
    total_with_desc = 0
    field_hit_count: dict[str, int] = {}

    header = f"ТЕСТ АНАЛІЗАТОРА ОПИСІВ  |  записів: {len(offers)}" + (f"  |  тип: {FILTER_TYPE}" if FILTER_TYPE else "")
    log.info("=" * 100)
    log.info(header)
    log.info("=" * 100)

    for rec in offers:
        offer_data: dict = json.loads(rec["offer_data"]) if rec["offer_data"] else {}
        apartment: dict = offer_data.get("apartment") or {}
        description: str = (apartment.get("description") or "").strip()
        article = rec.get("article") or str(rec["estate_id"])
        deal_type = offer_data.get("offer_type") or "Продаж"
        prop_type = rec.get("property_type") or "Квартира"

        log.info("")
        log.info("─" * 100)
        log.info("  %s  |  %s  |  %s  |  статус: %s", article, prop_type, deal_type, rec["status"])
        log.info("─" * 100)

        if not description:
            log.info("  [опис відсутній — пропускаємо]")
            continue

        total_with_desc += 1

        log.info("  ОПИС (%d симв.):", len(description))
        for line in description[:400].replace("\n", " ").split(". "):
            if line.strip():
                log.info("    %s", line.strip()[:120])
        if len(description) > 400:
            log.info("    …(ще %d симв.)", len(description) - 400)

        schema = load_schema(deal_type, prop_type)
        if not schema:
            log.warning("  [схему не знайдено для %s/%s]", deal_type, prop_type)
            continue

        analyzer = DescriptionAnalyzer(schema=schema, debug=False)
        crm = {k: v for k, v in offer_data.items() if k not in SKIP_KEYS}
        extracted = analyzer.analyze(description, existing_data={})

        schema_labels = {f["label"] for f in schema if f.get("label")} - SKIP_KEYS

        correct: dict[str, tuple] = {}
        wrong: dict[str, tuple] = {}
        only_extracted: dict[str, object] = {}
        only_crm: dict[str, object] = {}

        for k, ext_v in extracted.items():
            if k in crm:
                (correct if _values_match(crm[k], ext_v) else wrong)[k] = (crm[k], ext_v)
            else:
                only_extracted[k] = ext_v

        for k, crm_v in crm.items():
            if k not in extracted:
                only_crm[k] = crm_v

        nowhere = schema_labels - set(crm.keys()) - set(extracted.keys())

        if correct:
            log.info("")
            log.info("  ПРАВИЛЬНО ЗНАЙДЕНО В ОПИСІ (%d):", len(correct))
            for k, (cv, _) in sorted(correct.items()):
                log.info("    + %s", _row(k, cv, prefix=""))
            for k in correct:
                field_hit_count[k] = field_hit_count.get(k, 0) + 1
            total_correct += len(correct)
        else:
            log.info("  -- нових полів не знайдено")

        if wrong:
            log.info("")
            log.info("  ЗНАЙДЕНО З ВІДХИЛЕННЯМ (%d):", len(wrong))
            for k, (cv, ev) in sorted(wrong.items()):
                log.info("    != %-35s  CRM=%-35s  опис=%s", k, _fmt(cv), _fmt(ev))

        if only_extracted:
            log.info("")
            log.info("  ТІЛЬКИ В ОПИСІ, НЕМАЄ В CRM (%d):", len(only_extracted))
            for k, v in sorted(only_extracted.items()):
                log.info("    %s", _row(k, v, prefix="+ "))

        if only_crm:
            log.info("")
            log.info("  ТІЛЬКИ В CRM, АНАЛІЗАТОР НЕ ЗНАЙШОВ (%d):", len(only_crm))
            for k, v in sorted(only_crm.items()):
                log.info("    %s", _row(k, v, prefix=". "))

        nowhere_filtered = {m for m in nowhere if not m.startswith("Особисті")}
        if nowhere_filtered:
            log.info("")
            log.info("  ПОРОЖНЬО — НЕМАЄ НI В CRM, НI В ОПИСІ (%d):", len(nowhere_filtered))
            log.info("    %s", ", ".join(sorted(nowhere_filtered)))

    # ── Підсумок ──────────────────────────────────────────────────────────────
    log.info("")
    log.info("=" * 100)
    log.info("ПІДСУМОК")
    log.info("  Оголошень з описом:         %d / %d", total_with_desc, len(offers))
    log.info("  Правильно знайдених полів:  %d", total_correct)
    if field_hit_count:
        log.info("  Топ полів, знайдених в описах:")
        for k, cnt in sorted(field_hit_count.items(), key=lambda x: -x[1])[:15]:
            bar = "█" * cnt
            log.info("    %-40s %s (%d)", k, bar, cnt)
    log.info("=" * 100)
    log.info("Лог збережено: %s", LOG_PATH)


if __name__ == "__main__":
    run()
