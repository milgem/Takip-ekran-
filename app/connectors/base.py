from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass
class ItemRow:
    """Sipariş kalemi (bir ürün / beden satırı)."""
    product_id: str = ""          # ürün kartı
    variant_id: str = ""          # varyasyon (beden) — stokla eşleştirmek için
    sku: str = ""
    barcode: str = ""
    name: str = ""
    variant: str = ""             # beden / seçenek metni (platform veriyorsa)
    quantity: float = 0
    amount: float = 0             # satır toplamı (adet × birim fiyat)
    discount: float = 0           # bu satıra düşen kampanya / indirim tutarı
    cost: float = 0               # satırın toplam maliyeti (platform veriyorsa)
    campaign_id: str = ""         # site kampanyası / indirim kodu kimliği
    campaign: str = ""            # kampanya adı ya da indirim kodu
    brand: str = ""               # platform satırda veriyorsa
    category: str = ""


@dataclass
class StockRow:
    """Bir varyasyonun (beden) güncel stoğu."""
    product_id: str
    variant_id: str
    product_name: str = ""
    variant: str = ""             # "42", "L", "Siyah / 42" …
    sku: str = ""
    barcode: str = ""
    stock: float = 0
    price: float = 0
    active: bool = True
    brand: str = ""
    category: str = ""


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
    payment_status: str = ""      # ödeme statüsü (sipariş statüsünden ayrı)
    customer: str = ""            # müşteri adı soyadı
    commission: float = 0         # pazaryeri komisyonu
    cost: float = 0               # sipariş maliyeti (platform veriyorsa)
    items: list | None = None     # list[ItemRow]; None = kalem bilgisi alınamadı (mevcut kalemler korunur)


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
class CampaignRow:
    day: date
    campaign_id: str
    name: str = ""
    status: str = ""              # Aktif | Duraklatıldı | Kaldırıldı | Arşivlendi
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
    campaigns: list[CampaignRow] = field(default_factory=list)


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
