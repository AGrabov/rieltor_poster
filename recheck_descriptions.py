"""Повторно проаналізувати описи всіх оголошень у БД і виправити биті дані.

Проганяє збережений опис кожного оголошення через DescriptionAnalyzer (з
актуальними регулярками + санити-чеком площ) і застосовує те саме правило
злиття, що й парсер: значення з опису перезаписують поле, КРІМ структурних
(ціна/валюта/тип). Оновлюються лише поля, чиє значення реально змінилось.

За замовчуванням — dry-run (нічого не пише). Для запису додайте --apply
(перед записом створюється резервна копія offers.db).

    uv run python recheck_descriptions.py            # лише показати зміни
    uv run python recheck_descriptions.py --apply     # застосувати + бекап
    uv run python recheck_descriptions.py --limit 50  # тест на перших 50
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from pathlib import Path

from setup_logger import init_logging, setup_logger

init_logging(level="WARNING", filename="logs/recheck.log", clear_on_start=True)
logger = setup_logger(__name__)

DB_PATH = Path(__file__).parent / "offers.db"

# Поля площі, що постраждали від бага регулярки. Тільки їх і чіпаємо —
# повний ре-мердж усіх полів аналізатора вносить регресії (поверх, телефонні
# лінії тощо), бо це не наслідки бага, а перевилучення з тексту.
_TOTAL_FIELD = "Загальна площа, м²"
_AREA_FIELDS = (_TOTAL_FIELD, "Житлова площа, м²", "Площа кухні, м²")

# Будинок/квартира/комерція з загальною площею менше цього — неможливо,
# отже значення побите багом (фрагмент на кшталт "0.7" замість "180.7").
_MIN_PLAUSIBLE_TOTAL = 15.0


def _to_float(value) -> float | None:
    try:
        return float(str(value).replace(",", ".").strip())
    except (ValueError, TypeError):
        return None


def _sanitize_area(value) -> str | None:
    """Очистити число площі (прибрати хвостові '.'/',') → рядок або None."""
    f = _to_float(value)
    if f is None or f <= 0:
        return None
    return str(int(f)) if f == int(f) else str(f)


def _digits(value) -> str:
    return "".join(ch for ch in str(value) if ch.isdigit())


def _is_bug_fragment(old, new) -> bool:
    """Чи old — це обрізаний багом фрагмент new (напр. '0.7' від '180.7').

    Сигнатура бага: цифри old є суфіксом цифр new і new суттєво більше.
    Також ловимо неможливо малу загальну площу (old < мінімуму).
    """
    of, nf = _to_float(old), _to_float(new)
    if of is None or nf is None or nf <= of:
        return False
    od, nd = _digits(old), _digits(new)
    if od and nd and len(nd) > len(od) and nd.endswith(od):
        return True
    return of < _MIN_PLAUSIBLE_TOTAL


def _norm(value) -> tuple[str, object]:
    """Нормалізувати значення для семантичного порівняння (число vs рядок)."""
    s = str(value).replace(",", ".").strip()
    try:
        return ("num", float(s))
    except (ValueError, TypeError):
        return ("str", s.lower())


def _reanalyze_areas(offer_data: dict) -> dict[str, str]:
    """Повернути {поле_площі: очищене_нове_значення} з ре-аналізу опису.

    Аналізатору подається ЛИШЕ публічний опис (не personal_notes — там контакти).
    """
    from crm_data_parser.description_analyzer import DescriptionAnalyzer
    from schemas import load_offer_schema

    deal_type = offer_data.get("offer_type")
    property_type = offer_data.get("property_type")
    description = (offer_data.get("apartment") or {}).get("description") or ""
    if not deal_type or not property_type or not description.strip():
        return {}

    schema = load_offer_schema(deal_type, property_type)
    analyzer = DescriptionAnalyzer(schema["fields"])
    analyzed = analyzer.analyze(description, {})

    result: dict[str, str] = {}
    for field in _AREA_FIELDS:
        clean = _sanitize_area(analyzed.get(field))
        if clean is not None:
            result[field] = clean
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Ре-аналіз описів і виправлення битих площ у БД")
    parser.add_argument("--apply", action="store_true", help="Записати зміни (інакше dry-run)")
    parser.add_argument("--db", default=str(DB_PATH), help="Шлях до offers.db")
    parser.add_argument("--limit", type=int, help="Обробити лише перші N записів (для тесту)")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"БД не знайдено: {db_path}")
        return

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    query = "SELECT estate_id, status, offer_data FROM offers ORDER BY id"
    if args.limit:
        query += f" LIMIT {int(args.limit)}"
    rows = conn.execute(query).fetchall()

    scanned = 0
    fix_lines: list[str] = []  # биті багом — виправляємо
    disagreement_lines: list[str] = []  # опис ≠ CRM, але CRM правдоподібний — НЕ чіпаємо
    updates: list[tuple[int, str]] = []  # (estate_id, new_offer_data_json)

    for r in rows:
        if not r["offer_data"]:
            continue
        scanned += 1
        offer_data = json.loads(r["offer_data"])
        try:
            new_areas = _reanalyze_areas(offer_data)
        except Exception:
            logger.exception("Помилка ре-аналізу estate_id=%s", r["estate_id"])
            continue
        if not new_areas:
            continue

        old_total = offer_data.get(_TOTAL_FIELD)
        new_total = new_areas.get(_TOTAL_FIELD)
        # Жертва бага: збережена загальна площа — обрізаний фрагмент, а нова правдоподібна.
        is_victim = (
            old_total is not None
            and new_total is not None
            and _to_float(new_total) >= _MIN_PLAUSIBLE_TOTAL
            and _is_bug_fragment(old_total, new_total)
        )

        offer_changed = False
        for field in _AREA_FIELDS:
            if field not in new_areas or field not in offer_data:
                continue
            old, new = offer_data[field], new_areas[field]
            if _norm(old) == _norm(new):
                continue
            line = f"  [{r['estate_id']}/{r['status']}] {field}: {old!r} → {new!r}"
            if is_victim:
                offer_data[field] = new  # для жертви перевилучаємо всі площі
                fix_lines.append(line)
                offer_changed = True
            else:
                disagreement_lines.append(line)
        if offer_changed:
            updates.append((r["estate_id"], json.dumps(offer_data, ensure_ascii=False)))

    print(f"\nПереглянуто оголошень: {scanned}")
    print(f"Биті багом (виправляємо): {len(updates)} оголошень, {len(fix_lines)} полів")
    print(f"Розбіжності опис≠CRM (НЕ чіпаємо): {len(disagreement_lines)} полів\n")
    print("── Виправлення (биті багом площі) ──")
    print("\n".join(fix_lines) if fix_lines else "  (немає)")
    print("\n── Розбіжності опис≠CRM (лише інформація, не записуються) ──")
    print("\n".join(disagreement_lines) if disagreement_lines else "  (немає)")

    if not args.apply:
        print(f"\n[dry-run] Нічого не записано. Для запису: --apply  ({len(updates)} оголошень до оновлення)")
        conn.close()
        return

    if updates:
        backup = db_path.with_suffix(db_path.suffix + ".bak")
        shutil.copy2(db_path, backup)
        print(f"\nРезервна копія: {backup}")
        for estate_id, data_json in updates:
            conn.execute(
                "UPDATE offers SET offer_data = ?, updated_at = datetime('now','localtime') WHERE estate_id = ?",
                (data_json, estate_id),
            )
        conn.commit()
        print(f"Оновлено записів: {len(updates)}")
    else:
        print("\nБитих площ не знайдено — нічого записувати.")
    conn.close()


if __name__ == "__main__":
    main()
