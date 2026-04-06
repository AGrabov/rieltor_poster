"""SQLite база даних для відстеження стану обробки оголошень.

Зберігає розпарсені дані оголошень з CRM та відстежує статус публікації на Rieltor.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from setup_logger import setup_logger

logger = setup_logger(__name__)

DB_PATH = Path(__file__).parent / "offers.db"


@dataclass
class OfferRecord:
    """Один запис оголошення з бази даних."""

    id: int
    estate_id: int
    article: str | None
    property_type: str | None
    deal_type: str | None
    title: str | None
    status: str
    offer_data: dict
    rieltor_offer_id: str | None
    errors: list | None
    created_at: str
    updated_at: str


def _row_to_record(row: sqlite3.Row) -> OfferRecord:
    return OfferRecord(
        id=row["id"],
        estate_id=row["estate_id"],
        article=row["article"],
        property_type=row["property_type"],
        deal_type=row["deal_type"],
        title=row["title"],
        status=row["status"],
        offer_data=json.loads(row["offer_data"]) if row["offer_data"] else {},
        rieltor_offer_id=row["rieltor_offer_id"],
        errors=json.loads(row["errors"]) if row["errors"] else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class OfferDB:
    """Тонка обгортка навколо SQLite для відстеження оголошень."""

    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path
        self.conn: sqlite3.Connection | None = None

    def __enter__(self) -> OfferDB:
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()
        logger.debug("Відкрито БД: %s", self.db_path)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.conn:
            self.conn.close()
            logger.debug("БД закрито")

    def _create_tables(self) -> None:
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS offers (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                estate_id       INTEGER NOT NULL UNIQUE,
                article         TEXT,
                property_type   TEXT,
                deal_type       TEXT,
                title           TEXT,
                status          TEXT NOT NULL DEFAULT 'new'
                                CHECK(status IN ('new', 'posted', 'failed', 'skipped')),
                offer_data      TEXT NOT NULL DEFAULT '{}',
                rieltor_offer_id TEXT,
                errors          TEXT,
                created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                updated_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            )
        """)
        self.conn.commit()

    def estate_exists(self, estate_id: int) -> bool:
        row = self.conn.execute("SELECT 1 FROM offers WHERE estate_id = ?", (estate_id,)).fetchone()
        return row is not None

    def insert_offer(
        self,
        estate_id: int,
        offer_data: dict,
        article: str | None = None,
        property_type: str | None = None,
        deal_type: str | None = None,
        title: str | None = None,
        status: str = "new",
    ) -> int | None:
        """Вставити нове оголошення. Повертає rowid або None, якщо estate_id вже існує."""
        try:
            cur = self.conn.execute(
                """INSERT OR IGNORE INTO offers
                   (estate_id, article, property_type, deal_type, title, status, offer_data)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    estate_id,
                    article,
                    property_type,
                    deal_type,
                    title,
                    status,
                    json.dumps(offer_data, ensure_ascii=False),
                ),
            )
            self.conn.commit()
            if cur.rowcount == 0:
                return None
            logger.debug("Вставлено оголошення estate_id=%d status=%s", estate_id, status)
            return cur.lastrowid
        except Exception:
            logger.exception("Помилка вставки оголошення estate_id=%d", estate_id)
            return None

    def get_unprocessed(
        self,
        deal_type: str | None = None,
        property_type: str | list[str] | None = None,
        max_count: int | None = None,
    ) -> list[OfferRecord]:
        """Повернути оголошення зі статусом 'new', з необов'язковими фільтрами."""
        query = "SELECT * FROM offers WHERE status = 'new'"
        params: list = []

        if deal_type:
            query += " AND LOWER(deal_type) = LOWER(?)"
            params.append(deal_type)

        if property_type:
            types = [property_type] if isinstance(property_type, str) else property_type
            clauses = []
            for pt in types:
                if pt.lower() == "паркомісце":
                    # DB stores "Паркомісце_garage" / "Паркомісце_parking"
                    clauses.append("LOWER(property_type) LIKE 'паркомісце%'")
                else:
                    clauses.append("LOWER(property_type) = LOWER(?)")
                    params.append(pt)
            query += " AND (" + " OR ".join(clauses) + ")"

        query += " ORDER BY id"

        if max_count:
            query += " LIMIT ?"
            params.append(max_count)

        rows = self.conn.execute(query, params).fetchall()
        return [_row_to_record(r) for r in rows]

    def mark_posted(self, estate_id: int, rieltor_offer_id: str) -> None:
        self.conn.execute(
            """UPDATE offers
               SET status = 'posted', rieltor_offer_id = ?,
                   updated_at = datetime('now', 'localtime')
               WHERE estate_id = ?""",
            (rieltor_offer_id, estate_id),
        )
        self.conn.commit()
        logger.info("Об'єкт %d позначено як опублікований (rieltor_id=%s)", estate_id, rieltor_offer_id)

    def mark_failed(self, estate_id: int, errors: Any) -> None:
        self.conn.execute(
            """UPDATE offers
               SET status = 'failed', errors = ?,
                   updated_at = datetime('now', 'localtime')
               WHERE estate_id = ?""",
            (json.dumps(errors, ensure_ascii=False, default=str), estate_id),
        )
        self.conn.commit()
        logger.warning("Об'єкт %d позначено як помилковий", estate_id)

    def mark_skipped(self, estate_id: int, reason: str) -> None:
        self.conn.execute(
            """UPDATE offers
               SET status = 'skipped', errors = ?,
                   updated_at = datetime('now', 'localtime')
               WHERE estate_id = ?""",
            (json.dumps({"reason": reason}, ensure_ascii=False), estate_id),
        )
        self.conn.commit()
        logger.info("Об'єкт %d позначено як пропущений: %s", estate_id, reason)

    def update_offer_data(self, estate_id: int, offer_data: dict) -> None:
        """Оновити поле offer_data (JSON) для існуючого запису."""
        self.conn.execute(
            """UPDATE offers
               SET offer_data = ?, updated_at = datetime('now', 'localtime')
               WHERE estate_id = ?""",
            (json.dumps(offer_data, ensure_ascii=False), estate_id),
        )
        self.conn.commit()
        logger.debug("offer_data оновлено для estate_id=%d", estate_id)

    def get_without_cadastral(
        self,
        property_types: list[str] | None = None,
    ) -> list[OfferRecord]:
        """Повернути оголошення, у яких кадастровий номер відсутній або порожній.

        Args:
            property_types: Список типів об'єктів (нижній регістр) для фільтрації.
                            None = усі типи.
        """
        query = """
            SELECT * FROM offers
            WHERE (
                json_extract(offer_data, '$.address."Кадастровий номер"') IS NULL
                OR json_extract(offer_data, '$.address."Кадастровий номер"') = ''
            )
        """
        params: list = []
        if property_types:
            placeholders = ", ".join("?" * len(property_types))
            # SQLite LOWER() is ASCII-only — Cyrillic stays uppercase.
            # Compare as-is (property_type stores title-case schema names).
            query += f" AND property_type IN ({placeholders})"
            params.extend(property_types)
        query += " ORDER BY id"
        rows = self.conn.execute(query, params).fetchall()
        return [_row_to_record(r) for r in rows]

    def summary(self) -> dict[str, int]:
        rows = self.conn.execute("SELECT status, COUNT(*) as cnt FROM offers GROUP BY status").fetchall()
        return {r["status"]: r["cnt"] for r in rows}
