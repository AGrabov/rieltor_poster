"""Backward-compatible facade.

Split into:
    - rieltor_session.py: Playwright lifecycle + login
    - new_offer_poster/dict_filler.py: dict-based, schema-driven form filling

Keep importing `RieltorOfferPoster` from this module if you don't want to update imports.
"""

from __future__ import annotations

from typing import List, Optional

from playwright.sync_api import Page

from new_offer_poster import DictOfferFormFiller
from setup_logger import setup_logger
logger = setup_logger(__name__)
from rieltor_session import RieltorCredentials, RieltorSession


class RieltorOfferPoster:
    """High-level helper that manages browser session and fills the offer form.

    Uses DictOfferFormFiller under the hood — all offer data is passed as dicts
    with Ukrainian label keys from the schema.
    """

    CREATE_URL = "https://my.rieltor.ua/offers/create"

    def __init__(
        self,
        phone: str,
        password: str,
        property_type: str = "Квартира",
        deal_type: str = "Продаж",
        headless: bool = False,
        slow_mo_ms: int = 0,
        default_timeout_ms: int = 30_000,
        debug: bool = False,
    ) -> None:
        self.creds = RieltorCredentials(phone=phone, password=password)
        self._session = RieltorSession(
            creds=self.creds,
            headless=headless,
            slow_mo_ms=slow_mo_ms,
            default_timeout_ms=default_timeout_ms,
            debug=debug,
        )
        self.property_type = property_type
        self.deal_type = deal_type
        self.page: Optional[Page] = None
        self.filler: Optional[DictOfferFormFiller] = None

        if debug:
            logger.setLevel("DEBUG")
        self.debug = debug

    def __enter__(self) -> "RieltorOfferPoster":
        self._session.__enter__()
        self.page = self._session.page
        if not self.page:
            raise RuntimeError("Failed to create Playwright page")
        self.filler = DictOfferFormFiller(
            self.page,
            property_type=self.property_type,
            deal_type=self.deal_type,
            debug=self.debug,
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._session.__exit__(exc_type, exc, tb)

    def login(self) -> None:
        self._session.login()

    def create_offer_draft(self, offer_data: dict) -> None:
        """Fill offer form from a dict with Ukrainian label keys.

        Args:
            offer_data: Dict with keys like "Число кімнат", "Ціна", "address", etc.
        """
        if not self.filler:
            raise RuntimeError("Poster not started")
        self.filler.create_offer_draft(offer_data)

    def save(self) -> None:
        if not self.filler:
            raise RuntimeError("Poster not started")
        self.filler.save()

    def save_and_get_report(self) -> List[dict]:
        if not self.filler:
            raise RuntimeError("Poster not started")
        return self.filler.save_and_get_report()

    def publish(self) -> None:
        if not self.filler:
            raise RuntimeError("Poster not started")
        self.filler.publish()

    def publish_and_get_report(self) -> List[dict]:
        if not self.filler:
            raise RuntimeError("Poster not started")
        return self.filler.publish_and_get_report()

    def collect_validation_report(self) -> List[dict]:
        if not self.filler:
            raise RuntimeError("Poster not started")
        root = self.filler._new_offer_root()
        return self.filler.collect_validation_report(root)

    @property
    def last_saved_offer_id(self) -> str | int | None:
        if self.filler:
            return self.filler.last_saved_offer_id
        return None


def main():
    pass

if __name__ == '__main__':
    from dotenv import load_dotenv
    import os

    load_dotenv()

    # Example: dict-based offer data with Ukrainian label keys
    offer_data = {
        "offer_type": "Продаж",
        "property_type": "Квартира",
        "address": {
            "Місто": "Київ",
            "Район": "Шевченківський",
            "Вулиця": "Дегтярівська",
            "Будинок": "17",
            "Новобудова": "Creator City",
        },
        "Ціна": "182000",
        "Валюта": "USD",
        "Планування": "студія",
        "Число кімнат": "1",
        "Поверх": "3",
        "Поверховість": "25",
        "Стан": "З ремонтом",
        "Загальна площа": "45",
        "Житлова площа": "17",
        "Площа кухні": "15",
        "apartment": {
            "description": "Пропонується ексклюзивна однокімнатна квартира з авторським ремонтом.",
            "photos": [
                "offers/pics/photo_2025-12-09_02-43-25.jpg",
                "offers/pics/photo_2025-12-09_02-44-14.jpg",
            ],
        },
        "interior": {
            "description": "Повністю укомплектована меблями та технікою.",
        },
        "yard": {
            "description": "ЖК Creator City — концепція «місто в місті».",
        },
        "infrastructure": {
            "description": "Поруч парк ім. Івана Багряного, зоопарк, метро Лук'янівська.",
        },
    }

    with RieltorOfferPoster(
        phone=os.getenv("PHONE"),
        password=os.getenv("PASSWORD"),
        property_type="Квартира",
        deal_type="Продаж",
        headless=False,
        debug=True,
    ) as poster:
        poster.login()
        poster.create_offer_draft(offer_data)
        report = poster.save_and_get_report()
        if report:
            print("Ошибки:", report)
