"""SQLite database for tracking offer processing status.

Stores parsed offer data from CRM and tracks posting status to Rieltor.
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
    """Single offer row from the database."""
    id: int
    estate_id: int
    article: Optional[str]
    property_type: Optional[str]
    deal_type: Optional[str]
    title: Optional[str]
    status: str
    offer_data: dict
    rieltor_offer_id: Optional[str]
    errors: Optional[list]
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
    """Thin wrapper around SQLite for offer tracking."""

    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None

    def __enter__(self) -> OfferDB:
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()
        logger.debug("Opened DB: %s", self.db_path)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.conn:
            self.conn.close()
            logger.debug("Closed DB")

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
        row = self.conn.execute(
            "SELECT 1 FROM offers WHERE estate_id = ?", (estate_id,)
        ).fetchone()
        return row is not None

    def insert_offer(
        self,
        estate_id: int,
        offer_data: dict,
        article: Optional[str] = None,
        property_type: Optional[str] = None,
        deal_type: Optional[str] = None,
        title: Optional[str] = None,
        status: str = "new",
    ) -> Optional[int]:
        """Insert a new offer. Returns rowid, or None if estate_id already exists."""
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
            logger.debug("Inserted offer estate_id=%d status=%s", estate_id, status)
            return cur.lastrowid
        except Exception:
            logger.exception("Failed to insert offer estate_id=%d", estate_id)
            return None

    def get_unprocessed(
        self,
        deal_type: Optional[str] = None,
        property_type: Optional[str] = None,
        max_count: Optional[int] = None,
    ) -> List[OfferRecord]:
        """Return offers with status='new', with optional filters."""
        query = "SELECT * FROM offers WHERE status = 'new'"
        params: list = []

        if deal_type:
            query += " AND LOWER(deal_type) = LOWER(?)"
            params.append(deal_type)

        if property_type:
            query += " AND LOWER(property_type) = LOWER(?)"
            params.append(property_type)

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
        logger.info("Marked estate %d as posted (rieltor_id=%s)", estate_id, rieltor_offer_id)

    def mark_failed(self, estate_id: int, errors: Any) -> None:
        self.conn.execute(
            """UPDATE offers
               SET status = 'failed', errors = ?,
                   updated_at = datetime('now', 'localtime')
               WHERE estate_id = ?""",
            (json.dumps(errors, ensure_ascii=False, default=str), estate_id),
        )
        self.conn.commit()
        logger.warning("Marked estate %d as failed", estate_id)

    def mark_skipped(self, estate_id: int, reason: str) -> None:
        self.conn.execute(
            """UPDATE offers
               SET status = 'skipped', errors = ?,
                   updated_at = datetime('now', 'localtime')
               WHERE estate_id = ?""",
            (json.dumps({"reason": reason}, ensure_ascii=False), estate_id),
        )
        self.conn.commit()
        logger.info("Marked estate %d as skipped: %s", estate_id, reason)

    def summary(self) -> Dict[str, int]:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) as cnt FROM offers GROUP BY status"
        ).fetchall()
        return {r["status"]: r["cnt"] for r in rows}
