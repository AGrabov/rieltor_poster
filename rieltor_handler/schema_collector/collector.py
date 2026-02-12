from ._address_seed import _AddressSeedMixin
from ._collapse import _CollapseMixin
from ._core import _CoreMixin
from ._deal_type import _DealTypeMixin
from ._field_collect import _FieldCollectMixin
from ._label import _LabelMixin
from ._navigation import _NavigationMixin
from ._parking_type import _ParkingTypeMixin
from ._property_type import _PropertyTypeMixin
from ._radio_probe import _RadioProbeMixin
from ._smoke_fill import _SmokeFillMixin
from ._widget import _WidgetMixin
from ._collector_base import _CollectorBase



class OfferCreateSchemaCollector(
    _CollectorBase,
    _CoreMixin,
    _CollapseMixin,
    _DealTypeMixin,
    _ParkingTypeMixin,
    _PropertyTypeMixin,
    _NavigationMixin,
    _LabelMixin,
    _WidgetMixin,
    _FieldCollectMixin,
    _AddressSeedMixin,
    _SmokeFillMixin,
    _RadioProbeMixin,
):
    """Concrete collector class composed from mixins."""