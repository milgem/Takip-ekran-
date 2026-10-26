from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass
class OrderRow:
    external_id: str
    number: str
    created_at: datetime        # İstanbul yerel saatine çevrilmiş, tz'siz
    total: float
    currency: str = "TRY"
    status: str = ""
    is_pending: bool = False
    is_cancelled: bool = False
    channel: str = "Web sitesi"   # Web sitesi | Trendyol | Hepsiburada | Amazon | …


@dataclass
class AdRow:
    day: date
    spend: float = 0
    impressions: int = 0
    clicks: int = 0
    conversions: float = 0
    conversion_value: float = 0
    leads: float = 0


@dataclass
class FetchResult:
    orders: list[OrderRow] = field(default_factory=list)
    ads: list[AdRow] = field(default_factory=list)


@dataclass
class Field:
    name: str
    label: str
    secret: bool = False
    required: bool = True
    help: str = ""
    placeholder: str = ""


class ConnectorError(Exception):
    """Kullanıcıya gösterilecek, anlaşılır hata."""
