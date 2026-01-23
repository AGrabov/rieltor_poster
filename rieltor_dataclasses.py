from dataclasses import dataclass, field
from typing import Optional, List
from enum import Enum


# --- Enums ---
class OfferType(Enum):
    SALE = "Продаж"
    RENT = "Оренда"


class PropertyType(Enum):
    APARTMENT = "Квартира"
    ROOM = "Кімната"
    HOUSE = "Будинок"
    LAND = "Ділянка"
    PARKING = "Паркомісце"
    COMMERCIAL = "Комерційна нерухомість"


class RoomLayout(Enum):
    ADJACENT = "Суміжна"
    SEPARATE = "Роздільне"
    MIXED = "Суміжно-роздільна"
    STUDIO = "Студія"
    PENTHOUSE = "Пентхаус"
    KITCHEN_LIVING = "Кухня-вітальня"
    MULTILEVEL = "Багаторівнева"


class Currency(Enum):
    UAH = "гривень"
    USD = "доларів"
    EUR = "євро"
    UAH_PER_SQUARE_METER = "грн. / м²"
    USD_PER_SQUARE_METER = "дол. / м²"
    EUR_PER_SQUARE_METER = "євро / м²"


class CommissionUnit(Enum):
    PERCENT = "%"
    UAH = "гривнях"
    USD = "доларах"


class Condition(Enum):
    NO_RENOVATION = "Без ремонту"
    PARTIAL_RENOVATION = "Частковий ремонт"
    RENOVATED = "З ремонтом"


# --- Вложенные dataclass ---
@dataclass
class Address:
    region: str
    city: str
    district: str
    street: str
    house_number: str
    subway: List[str] = field(default_factory=list)
    guide: List[str] = field(default_factory=list)
    condo_complex: Optional[str] = None


@dataclass
class WithoutPowerSupply:
    water: Optional[bool] = None
    gas: Optional[bool] = None
    heating: Optional[bool] = None
    internet: Optional[bool] = None
    elevator: Optional[bool] = None
    backup_power: Optional[bool] = None


@dataclass
class PhotoBlock:
    description: str
    photos: List[str] = field(default_factory=list)


# --- Основной dataclass ---
@dataclass
class Offer:
    """
    Модель оголошення нерухомості для сайту rieltor.ua.

    Клас описує всі основні та додаткові параметри об'єкта нерухомості,
    включаючи адресу, технічні характеристики, стан, інфраструктуру та медіа-матеріали.
    Використовується для автоматизованого заповнення форм або інтеграції з API.

    Атрибути:
        offer_type (OfferType): Тип угоди (Продаж або Оренда).
        property_type (PropertyType): Тип нерухомості (Квартира, Кімната, Будинок, Ділянка, Паркомісце, Комерційна).
        address (Address): Адреса об'єкта (область, місто, район, вулиця, ЖК).
        price (float): Ціна об'єкта.
        currency (Currency): Валюта ціни (гривні, долари, євро, або за м²).
        assignment (bool): Чи є переуступка прав.
        pay_commission (Optional[float]): Комісія, якщо застосовується.

        room_layout (RoomLayout): Планування кімнат (Роздільне, Студія, Пентхаус тощо).
        rooms (int): Кількість кімнат.
        floor (int): Поверх.
        floors_total (int): Загальна кількість поверхів у будинку.
        condition (Condition): Стан об'єкта (Без ремонту, Частковий ремонт, З ремонтом).
        building_type (Optional[str]): Тип будівлі.
        construction_technology (Optional[str]): Технологія будівництва.
        special_conditions (List[str]): Особливі умови продажу/оренди.
        construction_stage (Optional[str]): Стадія будівництва.

        total_area (float): Загальна площа.
        living_area (float): Житлова площа.
        kitchen_area (float): Площа кухні.
        year_built (Optional[int]): Рік побудови.
        renewal_program (Optional[bool]): Чи входить у програму оновлення.
        without_power_supply (WithoutPowerSupply): Комунікації, які відсутні.
        accessibility (List[str]): Параметри доступності.

        heating, hot_water, gas, internet (Optional[bool]): Наявність комунікацій.
        nearby (List[str]): Об'єкти поруч (школи, магазини, транспорт).
        apartment_type (Optional[str]): Тип квартири.
        ceiling_height (Optional[float]): Висота стелі.
        windows_view (Optional[str]): Вид з вікон.
        replaned (bool): Чи була перепланована.
        bathroom (Optional[str]): Тип санвузла.
        plumbing (Optional[bool]): Наявність сантехніки.
        entrance_door (Optional[str]): Тип вхідних дверей.
        floor_covering (Optional[str]): Покриття підлоги.
        balconies (Optional[int]): Кількість балконів.
        windows_type (Optional[str]): Тип вікон.
        windows_condition (Optional[str]): Стан вікон.
        additional_features (List[str]): Додаткові характеристики.

        apartment, interior, layout, yard, infrastructure (PhotoBlock):
            Опис та фото для відповідних блоків оголошення.

        personal_notes (Optional[str]): Особисті нотатки, не відображаються у публічному оголошенні.
    """

    offer_type: OfferType
    property_type: PropertyType

    # Адреса об'єкта
    address: Address

    price: float
    currency: Currency = Currency.UAH

    room_layout: RoomLayout = RoomLayout.SEPARATE
    rooms: int
    floor: int
    floors_total: int
    condition: Condition = Condition.NO_RENOVATION

    total_area: float
    living_area: float
    kitchen_area: float

    special_conditions: List[str] = field(default_factory=list)
    without_power_supply: WithoutPowerSupply = field(default_factory=WithoutPowerSupply)
    accessibility: List[str] = field(default_factory=list)

    nearby: List[str] = field(default_factory=list)
    replaned: bool = False

    # Основні параметри
    # price: float
    # currency: Currency = Currency.UAH
    assignment: Optional[bool] = None
    buyer_commission: Optional[bool] = None
    commission: Optional[float] = None
    commission_unit: Optional[CommissionUnit] = None



    # Інформація про об'єкт
    # room_layout: RoomLayout = RoomLayout.SEPARATE
    # rooms: int
    # floor: int
    # floors_total: int
    # condition: Condition = Condition.NO_RENOVATION
    building_type: Optional[str] = None
    construction_technology: Optional[str] = None
    # special_conditions: List[str] = field(default_factory=list)
    construction_stage: Optional[str] = None

    # total_area: float
    # living_area: float
    # kitchen_area: float
    year_built: Optional[int] = None
    renewal_program: Optional[bool] = None
    # without_power_supply: WithoutPowerSupply = field(default_factory=WithoutPowerSupply)
    # accessibility: List[str] = field(default_factory=list)

    # Додаткові параметри
    heating: Optional[bool] = None
    hot_water: Optional[bool] = None
    gas: Optional[bool] = None
    internet: Optional[bool] = None
    # nearby: List[str] = field(default_factory=list)
    apartment_type: Optional[str] = None
    ceiling_height: Optional[float] = None
    windows_view: Optional[str] = None
    # replaned: bool = False
    bathroom: Optional[str] = None
    plumbing: Optional[bool] = None
    entrance_door: Optional[str] = None
    floor_covering: Optional[str] = None
    balconies: Optional[int] = None
    windows_type: Optional[str] = None
    windows_condition: Optional[str] = None
    additional_features: List[str] = field(default_factory=list)

    # Блоки фото/описів
    apartment: PhotoBlock = field(default_factory=PhotoBlock)
    interior: PhotoBlock = field(default_factory=PhotoBlock)
    layout: PhotoBlock = field(default_factory=PhotoBlock)
    yard: PhotoBlock = field(default_factory=PhotoBlock)
    infrastructure: PhotoBlock = field(default_factory=PhotoBlock)

    exclusive: Optional[bool] = False

    personal_notes: Optional[str] = None
