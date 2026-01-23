from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import datetime
from enum import Enum
from typing import List, Optional


# ---------- Base helpers ----------
@dataclass
class BoolGroup:
    """Base for checkbox-like groups (bool fields)."""

    def selected_keys(self) -> List[str]:
        out: List[str] = []
        for f in fields(self):
            if bool(getattr(self, f.name, False)):
                out.append(f.name)
        return out


# ---------- Enums ----------
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
    # site typo is intentional (copied from UI)
    NOT_READY = "Не введенно в експлуатацію"
    CONSTRUCTION = "Будується"   # fixed key name only


class ApartmentType(Enum):
    SEPARATED = "Окрема квартира"
    COMMUNAL = "Комунальна квартира"


class ApartmentLayout(Enum):
    STANDARD = "Стандартна"
    REPLANNED = "Перепланування"  # fixed key name only


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

class InternetType(Enum):
    CABLE = "Дротовий (кабельний)"
    FIBER_OPTIC = "Оптоволоконний"
    WIRELESS = "Бездротовий"

class HotWaterType(Enum):
    GAS_COLUMN = "Газова колонка"
    CENTRAL = "Централізована"
    GAS_BOILER = "Газовий котел"
    ELECTRIC_BOILER = "Електричний бойлер"


class HeatingType(Enum):
    CENTRAL = "Центральне"
    AUTONOMOUS = "Автономне"
    INDIVIDUAL = "Індивідуальне"


# ---------- Nested dataclasses ----------
@dataclass
class Address:
    city: str
    district: str  # REQUIRED on site (as you said)
    street: str
    house_number: str
    subway: List[str] = field(default_factory=list)
    guide: List[str] = field(default_factory=list)
    condo_complex: Optional[str] = None
    region: Optional[str] = None


@dataclass
class WithoutPowerSupply(BoolGroup):
    water: bool = False
    gas: bool = False
    heating: bool = False
    internet: bool = False
    elevator: bool = False
    backup_power: bool = False


@dataclass
class Accessibility(BoolGroup):
    ramp: bool = False
    ground_level_entrance: bool = False
    wide_doorways: bool = False
    disabled_parking: bool = False
    accessible_elevator: bool = False


@dataclass
class Nearby(BoolGroup):
    school: bool = False
    kindergarten: bool = False  # fixed key name
    stops: bool = False
    entertainment_venues: bool = False
    supermarket: bool = False
    park: bool = False
    river_lake: bool = False
    sea: bool = False
    wood: bool = False


@dataclass
class WindowView(BoolGroup):
    to_yard: bool = False
    to_sea: bool = False
    to_river: bool = False
    to_park: bool = False
    to_roadway: bool = False
    to_industrial_zone: bool = False
    to_wall: bool = False
    to_city: bool = False


@dataclass
class BuildingOptions(BoolGroup):
    under_office: bool = False
    parking: bool = False
    registration_possible: bool = False
    security: bool = False
    concierge: bool = False
    storage_room: bool = False


@dataclass
class InApartment(BoolGroup):
    tv: bool = False
    cable_tv: bool = False
    satellite_tv: bool = False
    wardrobe: bool = False
    bed: bool = False
    floor_heating: bool = False
    fireplace: bool = False
    air_conditioner: bool = False
    washing_machine: bool = False
    drying_machine: bool = False
    shower: bool = False
    jacuzzi: bool = False
    fridge: bool = False
    microwave: bool = False  # fixed key name
    dishwasher: bool = False
    alarms: bool = False
    counters: bool = False
    safe: bool = False


@dataclass
class DealOptions(BoolGroup):
    bargaining_possible: bool = False
    urgent: bool = False
    direct_sale: bool = False
    available: bool = False
    exclusive_contract: bool = False
    installment_possible: bool = False


@dataclass
class PhotoBlock:
    description: Optional[str] = None
    video_url: Optional[str] = None
    photos: List[str] = field(default_factory=list)


@dataclass
class AdditionalParams:
    heating: Optional[bool] = None
    heating_type: Optional[HeatingType] = None
    hot_water: Optional[bool] = None
    hot_water_type: Optional[HotWaterType] = None
    gas: Optional[bool] = None
    internet: Optional[bool] = None
    internet_type: Optional[InternetType] = None
    nearby: Nearby = field(default_factory=Nearby)
    apartment_type: Optional[ApartmentType] = None
    ceiling_height: Optional[float] = None
    windows_view: WindowView = field(default_factory=WindowView)
    apartment_layout: Optional[ApartmentLayout] = None
    kitchen_stove: Optional[KitchenStove] = None
    bathroom: Optional[Bathroom] = None
    plumbing: Optional[bool] = None
    entrance_door: Optional[EntranceDoor] = None
    floor_covering: Optional[FloorCovering] = None
    balconies: Optional[int] = None
    windows_type: Optional[WindowsType] = None
    windows_condition: Optional[WindowsCondition] = None
    additional: BuildingOptions = field(default_factory=BuildingOptions)

# ---------- Main Offer ----------
@dataclass
class Offer:
    # Base params
    offer_type: OfferType
    property_type: PropertyType
    address: Address
    rooms: int
    floor: int
    floors_total: int
    price: float
    total_area: float
    living_area: float
    kitchen_area: float


    # Optional
    currency: Currency = Currency.UAH
    room_layout: RoomLayout = RoomLayout.SEPARATE
    condition: Condition = Condition.RENOVATED

    special_conditions: DealOptions = field(default_factory=DealOptions)

    without_power_supply: WithoutPowerSupply = field(default_factory=WithoutPowerSupply)
    accessibility: Accessibility = field(default_factory=Accessibility)

    # Photos
    apartment: PhotoBlock = field(default_factory=PhotoBlock)
    interior: PhotoBlock = field(default_factory=PhotoBlock)
    layout: PhotoBlock = field(default_factory=PhotoBlock)
    yard: PhotoBlock = field(default_factory=PhotoBlock)
    infrastructure: PhotoBlock = field(default_factory=PhotoBlock)

    # Commission etc
    assignment: bool = False
    buyer_commission: bool = False
    commission: Optional[float] = None
    commission_unit: Optional[CommissionUnit] = None
    commission_share: Optional[float] = None

    # Construction
    building_type: Optional[str] = None
    construction_technology: Optional[str] = None
    construction_stage: Optional[ConstructionStatus] = None
    year_built: Optional[int] = None
    home_program: Optional[bool] = None
    renewal_program: Optional[bool] = None


    # Amenities
    additional_params: AdditionalParams = field(default_factory=AdditionalParams)
    in_apartment: InApartment = field(default_factory=InApartment)

    exclusive: Optional[bool] = None
    exclusive_contract_scan: List[str] = field(default_factory=list)
    exclusive_expiration_date: Optional[datetime] = None
    exclusive_verify: Optional[bool] = None
    personal_notes: Optional[str] = None

    @classmethod
    def basic_offer(
        cls,
        rooms: int,
        floor: int,
        floors_total: int,
        offer_type: OfferType,
        property_type: PropertyType,
        price: float,
        total_area: float,
        living_area: float,
        kitchen_area: float,
        address: Address,
    ) -> "Offer":
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
            address=address,
        )
