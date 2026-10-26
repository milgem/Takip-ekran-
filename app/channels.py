"""Siparişin hangi satış kanalından geldiğini tespit eder.

Pazaryeri entegrasyonları (Trendyol, Hepsiburada, Amazon…) siparişleri Ticimax/Shopify'a
aktardığında bu siparişler de mağazada görünür. Reklam ROAS'ı doğru hesaplansın diye
her sipariş bir kanala ayrılır; reklam ROAS'ı sadece WEB kanalı üzerinden hesaplanır.
"""
import re
import unicodedata

WEB = "Web sitesi"
POS = "Mağaza (POS)"
OTHER_MP = "Diğer pazaryeri"

# (anahtar kelime, kanal adı) — sıra önemli, ilk eşleşen kazanır
MARKETPLACES = [
    ("trendyol", "Trendyol"),
    ("hepsiburada", "Hepsiburada"), ("hepsi burada", "Hepsiburada"),
    ("amazon", "Amazon"),
    ("n11", "N11"),
    ("ciceksepeti", "Çiçeksepeti"), ("cicek sepeti", "Çiçeksepeti"),
    ("pttavm", "PttAVM"), ("ptt avm", "PttAVM"),
    ("lc waikiki", "LC Waikiki"), ("lcwaikiki", "LC Waikiki"), ("lcw", "LC Waikiki"),
    ("modanisa", "Modanisa"),
    ("boyner", "Boyner"),
    ("morhipo", "Morhipo"),
    ("koctas", "Koçtaş"),
    ("idefix", "İdefix"),
    ("etsy", "Etsy"),
    ("ebay", "eBay"),
    ("temu", "Temu"),
    ("aliexpress", "AliExpress"),
    ("getir", "Getir"),
    ("yemeksepeti", "Yemeksepeti"),
    ("pazarama", "Pazarama"),
    ("teknosa", "Teknosa"),
]
_POS_WORDS = ("pos", "magaza", "store")
_WEB_WORDS = ("web", "mobil", "mobile", "site", "online_store", "iphone", "android", "shopify_draft_order",
              "draft", "app", "uygulama", "telefon", "")


def _plain(text: str) -> str:
    t = str(text or "").translate(str.maketrans("ıİ", "iI"))
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[_\-./]+", " ", t).strip()


def detect(*texts, default: str = WEB) -> str:
    """Verilen metinlerin herhangi birinde pazaryeri adı geçiyorsa o kanalı döndürür."""
    for raw in texts:
        t = _plain(raw)
        if not t:
            continue
        for key, name in MARKETPLACES:
            if re.search(rf"(^|[^a-z0-9]){re.escape(key)}([^a-z0-9]|$)", t):
                return name
    return default


def is_marketplace(channel: str) -> bool:
    return channel not in (WEB, POS)


def classify_source(source: str) -> str:
    """Pazaryeri değilse kaynak adına göre Web / POS ayrımı."""
    t = _plain(source)
    if t in _POS_WORDS or t.startswith("pos"):
        return POS
    return WEB
