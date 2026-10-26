"""Ticimax web servis (SOAP) bağlayıcısı — SiparisServis.SelectSiparis.

Ticimax panelinde: Ayarlar > Web Servis > yeni yetki kodu oluşturup
'Sipariş' servisine okuma izni verin. Yetki kodu burada 'UyeKodu' olarak geçer.
"""
from datetime import date, datetime, timedelta, time as dtime

from .. import config
from .. import channels
from .base import ConnectorError, Field, FetchResult, OrderRow

KIND = "ticimax"
TITLE = "Ticimax"
FIELDS = [
    Field("domain", "Site alan adı", placeholder="www.ornekmagaza.com",
          help="Ticimax sitenizin adresi. Servis adresi otomatik oluşturulur."),
    Field("uye_kodu", "Web servis yetki kodu", secret=True),
    Field("total_field", "Tutar alanı (gelişmiş)", required=False, placeholder="otomatik",
          help="Ciro yanlış görünürse 'Test et' çıktısındaki alan adını buraya yazın."),
    Field("channel_field", "Satış kanalı alanı (gelişmiş)", required=False, placeholder="otomatik",
          help="Pazaryeri siparişleri 'Web sitesi' görünüyorsa 'Test et' çıktısındaki kanal alanının adını yazın."),
]

# Ticimax Sipariş Servis dokümanındaki SiparisDurumu değerleri
DURUM = {0: "Ön Sipariş", 1: "Onay Bekliyor", 2: "Onaylandı", 3: "Ödeme Bekliyor", 4: "Paketleniyor",
         5: "Tedarik Ediliyor", 6: "Kargoya Verildi", 7: "Teslim Edildi", 8: "İptal Edildi", 9: "İade Edildi",
         10: "Silinmiş", 11: "İade Talebi Alındı", 12: "İade Ulaştı", 13: "İade Ödemesi Yapıldı",
         14: "Teslimat Öncesi İptal Talebi", 15: "İptal Talebi", 16: "Kısmi İade Talebi", 17: "Kısmi İade Yapıldı"}
PENDING = {1, 2, 4, 5}                    # onaylı ama henüz kargoya verilmemiş
CANCELLED = {8, 9, 10, 12, 13, 14, 15}    # iptal / iade / silinmiş / iptal talebi
NOT_A_SALE = {3}                          # ödeme bekleyen: ciroya katılmaz (ön sipariş ciroya dahil)


def _norm(t: str) -> str:
    import unicodedata
    t = str(t).translate(str.maketrans("ıİ", "iI"))
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()
    return "".join(c for c in t if c.isalnum())


# Servis durumu metin olarak da dönebiliyor ("Onaylandi", "Kargoya Verildi", "Iptal"…)
DURUM_BY_NAME = {_norm(v): k for k, v in DURUM.items()}
DURUM_BY_NAME.update({"onsiparis": 0, "iptal": 8, "iade": 9, "silindi": 10, "kargolandi": 6,
                      "teslimedildi": 7, "iadeulasti": 12, "iadeulastiodemeyapilacak": 12})

# Siparişin geldiği kanalı (Web / Trendyol / Hepsiburada…) taşıyabilecek alanlar
CHANNEL_CANDIDATES = ["SiparisKaynagi", "SatisKanali", "SiparisKanali", "PazaryeriAdi", "Pazaryeri",
                      "PazaryeriKodu", "EntegrasyonKodu", "Kaynak", "Platform", "MarketplaceName"]
CHANNEL_HINTS = ("kayna", "kanal", "pazar", "entegrasyon", "market", "platform")

# Dokümana göre siparişin toplam tutarı SiparisToplamTutari alanındadır
TOTAL_CANDIDATES = ["SiparisToplamTutari", "ToplamTutar", "Tutar"]
_clients: dict[str, object] = {}


def _client(cfg):
    from zeep import Client, Settings
    from zeep.transports import Transport
    domain = cfg.get("domain", "").strip().replace("https://", "").replace("http://", "").strip("/")
    if not domain:
        raise ConnectorError("Site alan adı girilmeli.")
    if domain not in _clients:
        try:
            _clients[domain] = Client(f"https://{domain}/Servis/SiparisServis.svc?wsdl",
                                      settings=Settings(strict=False, xml_huge_tree=True),
                                      transport=Transport(timeout=60, operation_timeout=120))
        except Exception as e:  # noqa: BLE001
            raise ConnectorError(f"Ticimax servisine ulaşılamadı ({domain}): {e}") from e
    return _clients[domain]


def _type(client, local_name):
    for t in client.wsdl.types.types:
        if getattr(t, "name", None) == local_name:
            return t
    raise ConnectorError(f"Ticimax WSDL içinde {local_name} tipi bulunamadı.")


def _filter(client, start: datetime, end: datetime):
    t = _type(client, "WebSiparisFiltre")
    values = {}
    for name, el in t.elements:
        tname = getattr(el.type, "name", "") or ""
        if name == "SiparisTarihiBas":
            values[name] = start
        elif name == "SiparisTarihiSon":
            values[name] = end
        elif tname in ("int", "long", "short"):
            values[name] = -1          # Ticimax'ta -1 = filtre yok
    return t(**values)


def _paging(client, index, size):
    t = _type(client, "WebSiparisSayfalama")
    names = {n for n, _ in t.elements}
    vals = {"BaslangicIndex": index, "KayitSayisi": size, "SiralamaYonu": "ASC"}
    # Dokümanda alan adı hem "SiralamaDeger" hem "SiralamaDegeri" geçiyor; WSDL'de hangisi varsa o kullanılır
    for n in ("SiralamaDegeri", "SiralamaDeger"):
        if n in names:
            vals[n] = "ID"
    return t(**{k: v for k, v in vals.items() if k in names})


def _get(obj, *names, default=None):
    for n in names:
        v = getattr(obj, n, None) if not isinstance(obj, dict) else obj.get(n)
        if v not in (None, ""):
            return v
    return default


def _durum(raw) -> int | None:
    if raw in (None, ""):
        return None
    if isinstance(raw, int):
        return raw
    s = str(raw).strip()
    if s.lstrip("-").isdigit():
        return int(s)
    return DURUM_BY_NAME.get(_norm(s))


def _status_of(o) -> int | None:
    """Önce metin SiparisDurumu (dokümanda string), çözülemezse sayısal alanlar."""
    for name in ("SiparisDurumu", "Durum", "StrSiparisDurumu"):
        d = _durum(_get(o, name))
        if d is not None:
            return d
    return None


def _call(cfg, start: datetime, end: datetime):
    client = _client(cfg)
    rows, index, size = [], 0, 100
    while True:
        try:
            page = client.service.SelectSiparis(UyeKodu=cfg["uye_kodu"], f=_filter(client, start, end),
                                                s=_paging(client, index, size))
        except ConnectorError:
            raise
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "yetki" in msg.lower() or "uyekodu" in msg.lower():
                raise ConnectorError("Ticimax yetki kodu geçersiz ya da sipariş servisine izni yok.") from e
            raise ConnectorError(f"Ticimax hatası: {msg[:300]}") from e
        page = list(page or [])
        rows.extend(page)
        if len(page) < size:
            return rows
        index += size


def _flat(o) -> dict:
    """SOAP nesnesini düz sözlüğe çevirir (iç içe alanlar 'Ust.Alt' şeklinde)."""
    from zeep.helpers import serialize_object
    out = {}

    def walk(prefix, v, depth=0):
        if isinstance(v, dict) and depth < 2:
            for k, x in v.items():
                walk(f"{prefix}.{k}" if prefix else k, x, depth + 1)
        elif isinstance(v, (str, int, float)) and not isinstance(v, bool):
            out[prefix] = v
    walk("", serialize_object(o) if not isinstance(o, dict) else o)
    return out


def _channel(o, cfg) -> str:
    flat = _flat(o)
    if cfg.get("channel_field"):
        val = flat.get(cfg["channel_field"]) or _get(o, cfg["channel_field"])
        return channels.detect(val, default=channels.WEB) if val not in (None, "") else channels.WEB
    values = [_get(o, n) for n in CHANNEL_CANDIDATES]
    # Kanal/pazaryeri ipucu taşıyan diğer alanlar (adres, isim gibi alanlara bakılmaz)
    values += [v for k, v in flat.items() if any(h in k.lower() for h in CHANNEL_HINTS)]
    return channels.detect(*[v for v in values if v not in (None, "")], default=channels.WEB)


def _to_row(o, cfg) -> OrderRow:
    total_field = cfg.get("total_field") or None
    total = _get(o, total_field) if total_field else _get(o, *TOTAL_CANDIDATES, default=0)
    created = _get(o, "SiparisTarihi", "Tarih", "EklenmeTarihi")
    if isinstance(created, datetime) and created.tzinfo:
        created = created.astimezone(config.TIMEZONE).replace(tzinfo=None)
    d = _status_of(o)
    return OrderRow(
        external_id=str(_get(o, "ID", "SiparisID")), number=str(_get(o, "SiparisNo", "SiparisKodu", "ID", default="")),
        created_at=created or datetime.now(), total=float(total or 0),
        currency=str(_get(o, "ParaBirimi", default="TRY"))[:8],
        status=DURUM.get(d, str(d)) if d is not None else "",
        is_pending=d in PENDING,
        is_cancelled=d in CANCELLED or d in NOT_A_SALE,
        channel=_channel(o, cfg),
    )


def test(cfg) -> str:
    now = datetime.now(config.TIMEZONE).replace(tzinfo=None)
    rows = _call(cfg, now - timedelta(days=7), now)
    if not rows:
        return "Bağlantı başarılı, ancak son 7 günde sipariş bulunamadı."
    from collections import Counter
    parsed = [_to_row(o, cfg) for o in rows]
    dist = Counter(r.channel for r in parsed)
    flat = _flat(rows[-1])
    names = ", ".join(k for k, v in flat.items() if v not in ("", None))[:700]
    # kanal ipucu taşıyan alanların son 7 gündeki farklı değerleri — doğru alanı bulmayı kolaylaştırır
    hint_vals = {}
    for o in rows[-200:]:
        for k, v in _flat(o).items():
            if any(h in k.lower() for h in CHANNEL_HINTS) and v not in ("", None, 0, -1):
                hint_vals.setdefault(k, set()).add(str(v)[:30])
    hints = "; ".join(f"{k}: {', '.join(sorted(v)[:6])}" for k, v in hint_vals.items())[:500]
    r = parsed[-1]
    return (f"Bağlantı başarılı: son 7 günde {len(rows)} sipariş. Kanallar: "
            + ", ".join(f"{k} {v}" for k, v in dist.most_common())
            + f". Örnek: #{r.number} · {r.total:,.2f} · {r.status}."
            + (f" Kanal alanları: {hints}." if hints else " Kanal alanı bulunamadı.")
            + f" Gelen alanlar: {names}")


def fetch(cfg, start: date, end: date) -> FetchResult:
    rows = _call(cfg, datetime.combine(start, dtime.min), datetime.combine(end, dtime.max))
    return FetchResult(orders=[_to_row(o, cfg) for o in rows])
