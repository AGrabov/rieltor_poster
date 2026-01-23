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


class ConstructionStatus(Enum):
    READY = "Введено в експлуатацію"
    NOT_READY = "Не введенно в експлуатацію"
    CONSTRACTION = "Будується"


class ApartmentType(Enum):
    SEPARATED = "Окрема квартира"
    COMMUNAL = "Комунальна квартира"

class ApartmentLayout(Enum):
    STANDARD = "Стандартна"
    REPLANED = "Перепланування"


class Bathroom(Enum):
    SHARED = "Суміщений"
    SEPARATE = "Роздільний"
    NONE = "Немає"


class WindowsType(Enum):
    WOOD = "Дерев'яні"
    METAL_PLASTIC = "Металопластикові"
    ALUMINIUM = "Алюмінієві"


class WindowsCondition(Enum):
    NORMAL = "Нормальний"
    NEW = "Нові"
    NEED_REPAIR = "Потребують ремонту"


class BuildingType(Enum):
    CHESKYI_PROEKT = "Чеський проект"
    STALINKA = "Сталінка"
    SPEC_PROEKT = "Спец. проект"
    HRUSCHIVKA = "Хрущівка"
    HOSTYNKA = "Гостинка"
    DOREVOLUCIONNYI = "Дореволюційний"


class ConstructionType(Enum):
    CEGLYANA = "Цегляна"
    PANELNA = "Панельна"
    UTEPLENA_PANEL = "Утеплена панель"
    MONOLITNO_KARKASNA = "Монолітно-каркасна"
    BLOCHNA = "Блочна"


class KitchenStove(Enum):
    NO = "Ні"
    ELECTRIC = "Електрична"
    GAS = "Газова"
    COMBINED = "Комбінована"


class EntranceDoor(Enum):
    WOOD = "Дерев'яні"
    IRON = "Залізні"
    ARMORED = "Броньовані"


class FloorCovering(Enum):
    YES = "Є"
    NO = "Немає"
    STRAINER = "Стяжка"





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
class Nearby:
    school: Optional[bool] = None
    kindergarden: Optional[bool] = None
    stops: Optional[bool] = None
    malls: Optional[bool] = None
    supermarket: Optional[bool] = None
    park: Optional[bool] = None
    river_lake: Optional[bool] = None
    sea: Optional[bool] = None
    wood: Optional[bool] = None


@dataclass
class WindowView:
    to_yard: Optional[bool] = None            # У двір
    to_sea: Optional[bool] = None             # На море
    to_river: Optional[bool] = None           # На ріку
    to_park: Optional[bool] = None            # На парк
    to_roadway: Optional[bool] = None         # На проїжджу частину
    to_industrial_zone: Optional[bool] = None # На промислову зону
    to_wall: Optional[bool] = None            # На стіну
    to_city: Optional[bool] = None            # На місто


@dataclass
class BuildingOptions:
    under_office: Optional[bool] = None        # Під офіс
    parking: Optional[bool] = None             # Автопарковка
    registration_possible: Optional[bool] = None  # Можливість прописки
    security: Optional[bool] = None            # Охорона
    concierge: Optional[bool] = None           # Консьєрж
    storage_room: Optional[bool] = None        # Комора


@dataclass
class InApartment:
    tv: Optional[bool] = None                 # Телевізор
    cable_tv: Optional[bool] = None           # Кабельне ТБ
    satellite_tv: Optional[bool] = None       # Супутникове ТБ
    wardrobe: Optional[bool] = None           # Шафа
    bed: Optional[bool] = None                # Ліжко
    floor_heating: Optional[bool] = None      # Підігрів підлоги
    fireplace: Optional[bool] = None          # Kaмін
    air_conditioner: Optional[bool] = None    # Кондиціонер
    washing_machine: Optional[bool] = None    # Пральна машина
    drying_machine: Optional[bool] = None     # Сушильна машина
    shower: Optional[bool] = None             # Душова кабіна
    jacuzzi: Optional[bool] = None            # Джакузі
    fridge: Optional[bool] = None             # Холодильник
    microvawe: Optional[bool] = None          # Мікрохвильовка
    dishwasher: Optional[bool] = None         # Посудомийна машина
    alarms: Optional[bool] = None             # Сигналізація
    counters: Optional[bool] = None           # Лічильники
    safe: Optional[bool] = None               # Сейф


@dataclass
class DealOptions:
    bargaining_possible: Optional[bool] = None     # Торг доречний
    urgent: Optional[bool] = None                  # Терміново
    direct_sale: Optional[bool] = None             # Прямий продаж
    available: Optional[bool] = None               # Вільна
    exclusive_contract: Optional[bool] = None      # Ексклюзивний договір
    installment_possible: Optional[bool] = None    # Можливе розтермінування




@dataclass
class PhotoBlock:
    description: Optional[str] = None
    photos: List[str] = field(default_factory=list)



@dataclass
class AdditionalParams:
    heating: Optional[bool] = None
    heating_type: Optional[str] = None
    hot_water: Optional[bool] = None
    hot_water_type: Optional[str] = None
    gas: Optional[bool] = None
    internet: Optional[bool] = None
    internet_type: Optional[str] = None
    nearby: Nearby = field(default_factory=Nearby)
    apartment_type: ApartmentType = ApartmentType.SEPARATED
    ceiling_height: Optional[float] = None
    windows_view: WindowView = field(default_factory=WindowView)
    apartment_layout: ApartmentLayout = ApartmentLayout.STANDARD
    kitchen_stove: Optional[KitchenStove] = None
    bathroom: Optional[Bathroom] = None
    plumbing: Optional[bool] = None
    entrance_door: Optional[EntranceDoor] = None
    floor_covering: Optional[FloorCovering] = None
    balconies: Optional[int] = None
    windows_type: Optional[WindowsType] = None
    windows_condition: Optional[WindowsCondition] = None
    additional: BuildingOptions = field(default_factory=BuildingOptions)


# # --- Основной dataclass ---
# @dataclass
# class Offer:
#     """
#     Модель оголошення нерухомості для сайту rieltor.ua.

#     Клас описує всі основні та додаткові параметри об'єкта нерухомості,
#     включаючи адресу, технічні характеристики, стан, інфраструктуру та медіа-матеріали.
#     Використовується для автоматизованого заповнення форм або інтеграції з API.

#     Атрибути:
#         offer_type (OfferType): Тип угоди (Продаж або Оренда).
#         property_type (PropertyType): Тип нерухомості (Квартира, Кімната, Будинок, Ділянка, Паркомісце, Комерційна).
#         address (Address): Адреса об'єкта (область, місто, район, вулиця, ЖК).
#         price (float): Ціна об'єкта.
#         currency (Currency): Валюта ціни (гривні, долари, євро, або за м²).
#         assignment (bool): Чи є переуступка прав.
#         pay_commission (Optional[float]): Комісія, якщо застосовується.

#         room_layout (RoomLayout): Планування кімнат (Роздільне, Студія, Пентхаус тощо).
#         rooms (int): Кількість кімнат.
#         floor (int): Поверх.
#         floors_total (int): Загальна кількість поверхів у будинку.
#         condition (Condition): Стан об'єкта (Без ремонту, Частковий ремонт, З ремонтом).
#         building_type (Optional[str]): Тип будівлі.
#         construction_technology (Optional[str]): Технологія будівництва.
#         special_conditions (List[str]): Особливі умови продажу/оренди.
#         construction_stage (Optional[str]): Стадія будівництва.

#         total_area (float): Загальна площа.
#         living_area (float): Житлова площа.
#         kitchen_area (float): Площа кухні.
#         year_built (Optional[int]): Рік побудови.
#         renewal_program (Optional[bool]): Чи входить у програму оновлення.
#         without_power_supply (WithoutPowerSupply): Комунікації, які відсутні.
#         accessibility (List[str]): Параметри доступності.

#         heating, hot_water, gas, internet (Optional[bool]): Наявність комунікацій.
#         nearby (List[str]): Об'єкти поруч (школи, магазини, транспорт).
#         apartment_type (Optional[str]): Тип квартири.
#         ceiling_height (Optional[float]): Висота стелі.
#         windows_view (Optional[str]): Вид з вікон.
#         replaned (bool): Чи була перепланована.
#         bathroom (Optional[str]): Тип санвузла.
#         plumbing (Optional[bool]): Наявність сантехніки.
#         entrance_door (Optional[str]): Тип вхідних дверей.
#         floor_covering (Optional[str]): Покриття підлоги.
#         balconies (Optional[int]): Кількість балконів.
#         windows_type (Optional[str]): Тип вікон.
#         windows_condition (Optional[str]): Стан вікон.
#         additional_features (List[str]): Додаткові характеристики.

#         apartment, interior, layout, yard, infrastructure (PhotoBlock):
#             Опис та фото для відповідних блоків оголошення.

#         personal_notes (Optional[str]): Особисті нотатки, не відображаються у публічному оголошенні.
#     """

#     rooms: int
#     floor: int
#     floors_total: int
#     offer_type: OfferType
#     property_type: PropertyType
#     price: float
#     total_area: float
#     living_area: float
#     kitchen_area: float

#     # Адреса об'єкта
#     address: Address


#     currency: Currency = Currency.UAH

#     room_layout: RoomLayout = RoomLayout.SEPARATE

#     condition: Condition = Condition.RENOVATED


#     # торг/терміново/прямий продаж/вільна/ексклюзивний договір/розтермінування
#     special_conditions: DealOptions = field(default_factory=DealOptions)

#     without_power_supply: WithoutPowerSupply = field(default_factory=WithoutPowerSupply)
#     accessibility: List[str] = field(default_factory=list)

#     nearby: Nearby = field(default_factory=Nearby)
#     replaned: bool = False
#     building_features: BuildingOptions = field(default_factory=BuildingOptions)

#     apartment: PhotoBlock = field(default_factory=PhotoBlock)
#     interior: PhotoBlock = field(default_factory=PhotoBlock)
#     layout: PhotoBlock = field(default_factory=PhotoBlock)
#     yard: PhotoBlock = field(default_factory=PhotoBlock)
#     infrastructure: PhotoBlock = field(default_factory=PhotoBlock)

#     # Основні параметри
#     # price: float
#     # currency: Currency = Currency.UAH
#     assignment: Optional[bool] = None
#     buyer_commission: Optional[bool] = None
#     commission: Optional[float] = None
#     commission_unit: Optional[CommissionUnit] = None



#     # Інформація про об'єкт
#     # room_layout: RoomLayout = RoomLayout.SEPARATE
#     # rooms: int
#     # floor: int
#     # floors_total: int
#     # condition: Condition = Condition.NO_RENOVATION
#     building_type: Optional[str] = None
#     construction_technology: Optional[str] = None
#     # special_conditions: List[str] = field(default_factory=list)
#     construction_stage: Optional[ConstructionStatus] = None

#     # total_area: float
#     # living_area: float
#     # kitchen_area: float
#     year_built: Optional[int] = None
#     renewal_program: Optional[bool] = None
#     # without_power_supply: WithoutPowerSupply = field(default_factory=WithoutPowerSupply)
#     # accessibility: List[str] = field(default_factory=list)

#     # Додаткові параметри
#     heating: Optional[bool] = None
#     hot_water: Optional[bool] = None
#     gas: Optional[bool] = None
#     internet: Optional[bool] = None
#     # nearby: List[str] = field(default_factory=list)
#     apartment_type: ApartmentType = ApartmentType.SEPARATED
#     ceiling_height: Optional[float] = None
#     windows_view: WindowView = field(default_factory=WindowView)
#     # replaned: bool = False
#     bathroom: Optional[Bathroom] = None
#     plumbing: Optional[bool] = None
#     entrance_door: Optional[str] = None
#     floor_covering: Optional[str] = None
#     balconies: Optional[int] = None
#     windows_type: Optional[WindowsType] = None
#     windows_condition: Optional[WindowsCondition] = None
#     apartment_has: ApartmentOptions = field(default_factory=ApartmentOptions)
#     # additional_features: List[str] = field(default_factory=list)

#     # Блоки фото/описів
#     # apartment: PhotoBlock = field(default_factory=PhotoBlock)
#     # interior: PhotoBlock = field(default_factory=PhotoBlock)
#     # layout: PhotoBlock = field(default_factory=PhotoBlock)
#     # yard: PhotoBlock = field(default_factory=PhotoBlock)
#     # infrastructure: PhotoBlock = field(default_factory=PhotoBlock)

#     exclusive: Optional[bool] = None

#     personal_notes: Optional[str] = None

@dataclass
class Offer:
    # --- Основные параметры ---
    rooms: int
    floor: int
    floors_total: int
    offer_type: OfferType
    property_type: PropertyType
    price: float
    total_area: float
    living_area: float
    kitchen_area: float
    address: Address

    # --- Дополнительные ---
    currency: Currency = Currency.UAH
    room_layout: RoomLayout = RoomLayout.SEPARATE
    condition: Condition = Condition.RENOVATED
    special_conditions: DealOptions = field(default_factory=DealOptions)
    building_features: BuildingOptions = field(default_factory=BuildingOptions)
    nearby: Nearby = field(default_factory=Nearby)
    replaned: bool = False
    without_power_supply: WithoutPowerSupply = field(default_factory=WithoutPowerSupply)

    # --- Фото ---
    apartment: PhotoBlock = field(default_factory=PhotoBlock)
    interior: PhotoBlock = field(default_factory=PhotoBlock)
    layout: PhotoBlock = field(default_factory=PhotoBlock)
    yard: PhotoBlock = field(default_factory=PhotoBlock)
    infrastructure: PhotoBlock = field(default_factory=PhotoBlock)

    # --- Комиссии и прочее ---
    assignment: Optional[bool] = None
    buyer_commission: Optional[bool] = None
    commission: Optional[float] = None
    commission_unit: Optional[CommissionUnit] = None

    # --- Строительство ---
    building_type: Optional[str] = None
    construction_technology: Optional[str] = None
    construction_stage: Optional[ConstructionStatus] = None
    year_built: Optional[int] = None
    renewal_program: Optional[bool] = None

    # --- Удобства ---
    heating: Optional[bool] = None
    hot_water: Optional[bool] = None
    gas: Optional[bool] = None
    internet: Optional[bool] = None
    apartment_type: ApartmentType = ApartmentType.SEPARATED
    ceiling_height: Optional[float] = None
    windows_view: WindowView = field(default_factory=WindowView)
    bathroom: Optional[Bathroom] = None
    plumbing: Optional[bool] = None
    entrance_door: Optional[str] = None
    floor_covering: Optional[str] = None
    balconies: Optional[int] = None
    windows_type: Optional[WindowsType] = None
    windows_condition: Optional[WindowsCondition] = None
    apartment_has: InApartment = field(default_factory=InApartment)

    exclusive: Optional[bool] = None
    personal_notes: Optional[str] = None

    # --- Удобный конструктор ---
    @classmethod
    def basic_offer(cls, rooms: int, floor: int, floors_total: int,
                    offer_type: OfferType, property_type: PropertyType,
                    price: float, total_area: float,
                    living_area: float, kitchen_area: float,
                    address: Address) -> "Offer":
        """Создаёт объект Offer только с базовыми параметрами,
        остальные поля выставляются по умолчанию."""
        return cls(
            rooms=rooms,
            floor=floor,
            floors_total=floors_total,
            offer_type=offer_type,
            property_type=property_type,
            price=price,
            total_area=total_area,
            living_area=living_area,
            kitchen_area=kitchen_area,
            address=address
        )