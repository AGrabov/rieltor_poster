"""Backward-compatible facade.

Split into:
    - rieltor_session.py: Playwright lifecycle + login
    - new_offer_filler.py: create-offer page filling + validation report

Keep importing `RieltorOfferPoster` from this module if you don't want to update imports.
"""

from __future__ import annotations

from typing import List, Optional

from playwright.sync_api import Page

# from new_offer_filler import NewOfferFormFiller
from new_offer.filler import NewOfferFormFiller
# from rieltor_dataclasses_01 import Offer, PhotoBlock, Address, OfferType, WithoutPowerSupply, PropertyType, Currency, RoomLayout, Condition
from models.rieltor_dataclasses import AdditionalParams, ApartmentLayout, ApartmentType, Bathroom, EntranceDoor, HeatingType, HotWaterType, InternetType, KitchenStove, Offer, PhotoBlock, Address, OfferType, WindowsCondition, WindowsType, WithoutPowerSupply, PropertyType, Currency, RoomLayout, Condition, DealOptions, BuildingOptions, Nearby, InApartment, WindowView
from setup_logger import setup_logger
logger = setup_logger(__name__)
from rieltor_session import RieltorCredentials, RieltorSession


class RieltorOfferPoster:
    """High-level helper that manages browser session and fills the offer form."""

    CREATE_URL = "https://my.rieltor.ua/offers/create"

    def __init__(
        self,
        phone: str,
        password: str,
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
        self.page: Optional[Page] = None
        self.filler: Optional[NewOfferFormFiller] = None

        if debug:
            logger.setLevel("DEBUG")
        self.debug = debug

    def __enter__(self) -> "RieltorOfferPoster":
        self._session.__enter__()
        self.page = self._session.page
        if not self.page:
            raise RuntimeError("Failed to create Playwright page")
        self.filler = NewOfferFormFiller(self.page, self.debug)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._session.__exit__(exc_type, exc, tb)

    def login(self) -> None:
        self._session.login()

    def create_offer_draft(self, offer: Offer) -> None:
        if not self.filler:
            raise RuntimeError("Poster not started")
        # NewOfferFormFiller already knows the URL and will open the page.
        self.filler.create_offer_draft(offer)

    def save(self) -> None:
        if not self.filler:
            raise RuntimeError("Poster not started")
        self.filler.save()

    def save_and_get_report(self) -> List[dict]:
        if not self.filler:
            raise RuntimeError("Poster not started")
        return self.filler.save_and_get_report()

    def collect_validation_report(self) -> List[dict]:
        if not self.filler:
            raise RuntimeError("Poster not started")
        return self.filler.collect_validation_report()


# /html/body/div[4]/div[3]/div/div[1]/h2/div/div
# body > div.MuiDialog-root.jss235 > div.MuiDialog-container.MuiDialog-scrollPaper > div > div.MuiDialogTitle-root > h2 > div > div


def main():
    pass

if __name__ == '__main__':
    from dotenv import load_dotenv
    import os

    load_dotenv()

    new_offer = Offer(
        offer_type=OfferType.SALE,
        property_type=PropertyType.APARTMENT,
        address=Address(
            # region="Київська",
            city="Київ",
            district="Шевченківський",
            street="вул. Дегтярівська",
            house_number="17",
            condo_complex="ЖК Creator City",
        ),
        price=182000,
        currency=Currency.USD,
        room_layout=RoomLayout.STUDIO,
        rooms=1,
        floor=3,
        floors_total=25,
        condition=Condition.RENOVATED,
        total_area=45,
        living_area=17,
        kitchen_area=15,
        without_power_supply=WithoutPowerSupply(
            gas=False,
            water=False,
        ),
        in_apartment=InApartment(
            tv=True,
            cable_tv=True,
            satellite_tv=True,
            wardrobe=True,
            bed=True,
            floor_heating=True,
            air_conditioner=True,
            washing_machine=True,
            drying_machine=True,
            shower=True,
            fridge=True,
            microwave=True,
            dishwasher=True,
            alarms=True,
            counters=True),
        additional_params=AdditionalParams(
            heating=True,
            heating_type=HeatingType.CENTRAL,
            hot_water=True,
            hot_water_type=HotWaterType.CENTRAL,
            gas=True,
            internet=True,
            internet_type=InternetType.CABLE,
            ceiling_height=3.0,
            nearby=Nearby(
                school=True,
                kindergarten=True,
                stops=True,
                entertainment_venues=True,
                supermarket=True,
                park=True
            ),
            apartment_type=ApartmentType.SEPARATED,
            apartment_layout=ApartmentLayout.STANDARD,
            kitchen_stove=KitchenStove.GAS,
            bathroom=Bathroom.SEPARATE,
            plumbing=True,
            entrance_door=EntranceDoor.ARMORED,
            windows_view=WindowView(
                to_yard=True,
                to_city=True,
                ),
            balconies=1,
            windows_type=WindowsType.METAL_PLASTIC,
            windows_condition=WindowsCondition.NEW
        ),
    #     apartment=PhotoBlock(
    #         description="""Пропонується ексклюзивна однокімнатна квартира з авторським ремонтом та повним меблюванням у сучасному житловому комплексі Бізнес-класу Creator City - символі нового рівня комфорту та стилю в серці Шевченківського району. Квартира створена для тих, хто цінує простір, естетику та технологічність. Тут продумана кожна деталь - від планування до інженерних рішень. За адресою Дегтярівська вул., 17.
    #         - Авторський ремонт 2026 року
    #         - В квартирі ніхто не проживав
    #         - Загальна площа 45 м.кв.
    #         - Безпечний 3 поверх / 25 (з видом у двір)
    #         - Чудовий інвестиційний варіант

    #         Повністю укомплектована меблями та всією необхідною технікою для життя без зайвих турбот: вбудований холодильник, індукційна плита, духова шафа, мікрохвильова піч, посудомийна машина, пральна та сушильна машини, телевізор, витяжка, бойлер. Додатково встановлені система очищення води та центральне кондиціонування, що забезпечує комфорт у будь-яку пору року.

    #         ЖК Creator City є концепція «місто в місті» - вся необхідна для життя інфраструктура знаходиться на території комплексу. Для безпеки майбутніх мешканців в громадських місцях встановлять камери відеоспостереження, внутрішні двори огородять парканом, а увійти в під'їзд та ліфт можна буде тільки з картою-пропуском. Для дітей різного віку розмістять кілька ігрових комплексів, для спортсменів — вуличні тренажери і футбольне поле, а родзинкою комплексу стане власний ландшафтний парк площею 2 га з водоймою. Щоб комфортному відпочинку не заважали автомобілі, забудовник передбачив підземний дворівневий паркінг з ліфтом.

    #         Локація - ще одна сильна сторона. Поруч зелений парк імені Івана Багряного, Київський зоопарк, метро Лук’янівська та Шулявська, КПІ, інноваційний простір Unit City, житлові комплекси Crystal Park Tower та інші знакові об’єкти району. Тут зручно жити, працювати й відпочивати.

    #         Це не просто нерухомість - це готовий простір для життя, куди можна заїхати з валізою і відразу відчути себе вдома.
    #         Запрошую на перегляд, щоб ви змогли відчути цю атмосферу особисто.""",
    #         # photos=['offers/pics/photo_2025-12-09_02-43-25.jpg', 'offers/pics/photo_2025-12-09_02-44-14.jpg'],
    #     ),
    )

    with RieltorOfferPoster(phone=os.getenv("PHONE"), password=os.getenv("PASSWORD"), headless=False, debug=True) as poster:
        poster.login()
        poster.create_offer_draft(new_offer)
        report = poster.save_and_get_report()
        if report:
            print("Ошибки:", report)
