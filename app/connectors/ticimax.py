"""Ticimax web servis (SOAP) bağlayıcısı — SiparisServis.SelectSiparis.

Ticimax panelinde: Ayarlar > Web Servis > yeni yetki kodu oluşturup
'Sipariş' servisine okuma izni verin. Yetki kodu burada 'UyeKodu' olarak geçer.
"""
from datetime import date, datetime, timedelta, time as dtime

from .. import config
from .. import channels
import logging

log = logging.getLogger("ticimax")
from .base import ItemRow, StockRow, ConnectorError, Field, FetchResult, OrderRow

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
DURUM = {0: "Sipariş Alındı",   # dokümanda "Ön sipariş"; Ticimax panelinde "Sipariş Alındı" görünüyor
         1: "Onay Bekliyor", 2: "Onaylandı", 3: "Ödeme Bekliyor", 4: "Paketleniyor",
         5: "Tedarik Ediliyor", 6: "Kargoya Verildi", 7: "Teslim Edildi", 8: "İptal Edildi", 9: "İade Edildi",
         10: "Silinmiş", 11: "İade Talebi Alındı", 12: "İade Ulaştı", 13: "İade Ödemesi Yapıldı",
         14: "Teslimat Öncesi İptal Talebi", 15: "İptal Talebi", 16: "Kısmi İade Talebi", 17: "Kısmi İade Yapıldı"}
PENDING = {1, 2, 4, 5}                    # onaylı ama henüz kargoya verilmemiş


def _norm(t: str) -> str:
    import unicodedata
    t = str(t).translate(str.maketrans("ıİ", "iI"))
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()
    return "".join(c for c in t if c.isalnum())


# Servis durumu metin olarak da dönebiliyor ("Onaylandi", "Kargoya Verildi", "Iptal"…)
DURUM_BY_NAME = {_norm(v): k for k, v in DURUM.items()}
DURUM_BY_NAME.update({"onsiparis": 0, "siparisalindi": 0, "iptal": 8, "iade": 9, "silindi": 10, "kargolandi": 6,
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


# Ticimax Sipariş Servis dokümanı: ödeme statüsü (OdemeDurumu / WebSiparisOdeme.Onaylandi)
ODEME_DURUM = {0: "Onay bekliyor", 1: "Onaylandı", 2: "Hatalı", 3: "İade edilmiş", 4: "İptal edilmiş",
               5: "Tamamlanmadı"}   # 5 dokümanda yok; ödeme adımında yarıda kalan kart denemelerinde görülüyor
NO_PAYMENT = "Ödeme kaydı yok"
# Ödeme tipi (OdemeTipi). Bunlarda ödeme sonradan alınır; "Onay bekliyor" olsa da gerçek siparişti
# (havale, kapıda ödeme, cari, mail order, mağazada öde). Kart/online ödemede "Onay bekliyor" = tamamlanmamış ödeme.
OFFLINE_TYPES = {1, 2, 3, 7, 8, 10, 12, 27}
# Önceliği yüksek olan ödeme kaydı siparişin ödeme statüsünü belirler
_PRIORITY = [1, 3, 0, 4, 2]


def _payments(o):
    """Siparişteki ödeme kayıtları (Odemeler listesi). Alan hiç yoksa None."""
    v = o.get("Odemeler", None) if isinstance(o, dict) else getattr(o, "Odemeler", None)
    present = ("Odemeler" in o) if isinstance(o, dict) else hasattr(o, "Odemeler")
    if not present:
        return None
    if v is None:
        return []
    w = v.get("WebSiparisOdeme") if isinstance(v, dict) else getattr(v, "WebSiparisOdeme", None)
    if w is not None:                      # SOAP dizileri bir kat sarmalı gelebilir
        v = w
    if not isinstance(v, (list, tuple)):
        v = [v]
    return [x for x in v if x is not None]


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _payment_state(o) -> tuple[str, bool]:
    """(ödeme statüsü metni, ciroya girer mi). Servis ödeme bilgisi göndermiyorsa ('', True)."""
    pays = _payments(o)
    if pays is None:
        return "", True
    if not pays:
        return NO_PAYMENT, False
    best = None
    for p in pays:
        st = _int(_get(p, "Onaylandi", "OdemeDurumu"))
        st = 0 if st is None else st
        if best is None or _PRIORITY.index(st if st in _PRIORITY else 0) < _PRIORITY.index(best[0] if best[0] in _PRIORITY else 0):
            best = (st, _int(_get(p, "OdemeTipi")))
    st, tip = best
    label = ODEME_DURUM.get(st, str(st))
    if st in (1, 3):                       # onaylandı / iade edilmiş: ödeme alınmış sipariş
        return label, True
    if st == 0:                            # onay bekliyor: sadece sonradan ödemeli tiplerde gerçek sipariş
        return label, tip in OFFLINE_TYPES
    return label, False                    # hatalı / iptal edilmiş ödeme


def _unwrap(v, *inner):
    """SOAP listeleri bir kat sarmalı gelebilir (ArrayOfX → X)."""
    if v is None:
        return []
    for n in inner:
        w = v.get(n) if isinstance(v, dict) else getattr(v, n, None)
        if w is not None:
            v = w
            break
    if not isinstance(v, (list, tuple)):
        v = [v]
    return [x for x in v if x is not None]


def _num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _raw_items(o):
    """WebSiparis.Urunler — alan hiç yoksa None."""
    present = ("Urunler" in o) if isinstance(o, dict) else hasattr(o, "Urunler")
    if not present:
        return None
    return _unwrap(_get(o, "Urunler"), "WebSiparisUrun")


def _items(raw, unit_price: bool) -> list:
    out = []
    for u in raw:
        qty = _num(_get(u, "Adet", default=0))
        tutar = _num(_get(u, "Tutar", default=0))
        out.append(ItemRow(
            product_id=str(_get(u, "UrunKartiID", default="") or ""), variant_id=str(_get(u, "UrunID", default="") or ""),
            sku=str(_get(u, "StokKodu", default="") or ""), barcode=str(_get(u, "Barkod", default="") or ""),
            name=str(_get(u, "UrunAdi", default="") or ""), quantity=qty,
            amount=tutar * qty if unit_price else tutar,
            cost=_num(_get(u, "Maliyet", default=0)) * (qty if unit_price else 1),
            discount=_num(_get(u, "KampanyaIndirimTutari", default=0)),
            campaign_id=_campaign_id(_get(u, "KampanyaID", default=""))))
    return out


def _campaign_id(v) -> str:
    s = str(v if v is not None else "").strip()
    return "" if s in ("", "0", "-1", "None") else s


def _unit_price_mode(pairs) -> bool:
    """Ticimax'ta kalem 'Tutar' alanı birim fiyat mı satır toplamı mı? Sipariş toplamına hangisi
    daha yakınsa o kabul edilir (kargo/indirim farkı ikisini de aynı oranda etkiler)."""
    a = b = 0.0
    for total, raw in pairs:
        if not raw or total <= 0:
            continue
        unit = sum(_num(_get(u, "Tutar")) * _num(_get(u, "Adet")) for u in raw)
        line = sum(_num(_get(u, "Tutar")) for u in raw)
        a += abs(unit - total) / total
        b += abs(line - total) / total
    return a <= b


def _fetch_items_separately(cfg, client, order_id):
    try:
        res = client.service.SelectSiparisUrun(UyeKodu=cfg["uye_kodu"], SiparisId=int(order_id), IptalEdilmisUrunler=False)
    except Exception:  # noqa: BLE001
        return None
    return _unwrap(res, "WebSiparisUrun")


def _urun_client(cfg):
    from zeep import Client, Settings
    from zeep.transports import Transport
    domain = cfg.get("domain", "").strip().replace("https://", "").replace("http://", "").strip("/")
    key = domain + "#urun"
    if key not in _clients:
        try:
            _clients[key] = Client(f"https://{domain}/Servis/UrunServis.svc?wsdl",
                                   settings=Settings(strict=False, xml_huge_tree=True),
                                   transport=Transport(timeout=60, operation_timeout=180))
        except Exception as e:  # noqa: BLE001
            raise ConnectorError(f"Ticimax ürün servisine ulaşılamadı ({domain}): {e}") from e
    return _clients[key]


SIZE_HINTS = ("beden", "numara", "size", "olcu", "boy")


def _variant_text(ozellikler) -> str:
    pairs = [(str(_get(x, "Tanim", default="") or ""), str(_get(x, "Deger", default="") or "").strip())
             for x in _unwrap(ozellikler, "VaryasyonOzellik", "UrunVaryasyonOzellik")]
    pairs = [(t, d) for t, d in pairs if d]
    if not pairs:
        return ""
    sizes = [d for t, d in pairs if any(h in _norm(t) for h in SIZE_HINTS)]
    others = [d for t, d in pairs if not any(h in _norm(t) for h in SIZE_HINTS)]
    return " / ".join(others + sizes)        # beden en sonda: "Siyah / 42"


DOC_FILTER = {"Aktif": -1, "Firsat": -1, "Indirimli": -1, "Vitrin": -1, "KategoriID": 0, "MarkaID": 0, "UrunKartiID": 0}
_filter_choice: dict[str, str] = {}


def _urun_filters(client):
    """Ürün filtresi için denenecek seçenekler. Bazı Ticimax sürümlerinde dokümanda olmayan sayısal alanlar
    (ör. stok aralığı) -1 verilince hiç ürün dönmüyor; bu yüzden önce sadece dokümandaki alanlar doldurulur."""
    ft = _type(client, "UrunFiltre")
    ints = {n for n, el in ft.elements if (getattr(el.type, "name", "") or "") in ("int", "long", "short")}
    names = {n for n, _ in ft.elements}
    doc = {k: v for k, v in DOC_FILTER.items() if k in names}
    yield "doküman", ft(**doc)
    yield "aktifler", ft(**{**doc, **({"Aktif": 1} if "Aktif" in names else {})})
    yield "boş filtre", ft()
    yield "tüm sayılar", ft(**{n: (0 if n.endswith("ID") else -1) for n in ints})


def _urun_page(client, cfg, flt, index, size):
    st = _type(client, "UrunSayfalama")
    snames = {n for n, _ in st.elements}
    svals = {"BaslangicIndex": index, "KayitSayisi": size, "SiralamaDegeri": "ID", "SiralamaYonu": "ASC"}
    return _unwrap(client.service.SelectUrun(UyeKodu=cfg["uye_kodu"], f=flt,
                                             s=st(**{k: v for k, v in svals.items() if k in snames})), "UrunKarti")


def _pick_filter(client, cfg):
    """İlk sayfada ürün getiren filtre seçeneğini bulur (site başına hatırlanır)."""
    key = cfg.get("domain", "")
    variants = list(_urun_filters(client))
    if key in _filter_choice:
        variants.sort(key=lambda v: v[0] != _filter_choice[key])
    errors = []
    for label, flt in variants:
        try:
            page = _urun_page(client, cfg, flt, 0, 5)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{label}: {str(e)[:120]}")
            continue
        if page:
            _filter_choice[key] = label
            return label, flt, page
    return None, None, errors


def stock_diagnostics(cfg) -> str:
    """Ürün servisinden ilk sayfayı okuyup stok verisinin nasıl geldiğini özetler (kişisel veri yok)."""
    try:
        client = _urun_client(cfg)
        ops = sorted({op for svc in client.wsdl.services.values() for port in svc.ports.values()
                      for op in port.binding._operations})
        fnames = [n for n, _ in _type(client, "UrunFiltre").elements]
        label, flt, page = _pick_filter(client, cfg)
    except Exception as e:  # noqa: BLE001
        return f" Stok testi: ürün servisi hatası: {str(e)[:250]}."
    sel = ", ".join(o for o in ops if o.startswith(("Select", "Get")))
    if not label:
        errs = ("; hatalar: " + " | ".join(page)) if page else ""
        return (f" Stok testi: ürün servisi hiçbir filtreyle ürün döndürmedi{errs}. Filtre alanları: {', '.join(fnames)}."
                f" Okuma işlemleri: {sel[:600]}.")
    u = page[0]
    vars_ = _unwrap(_get(u, "Varyasyonlar"), "Varyasyon")
    n_var = sum(len(_unwrap(_get(x, "Varyasyonlar"), "Varyasyon")) for x in page)
    fields = ", ".join(k for k, v in _flat(u).items() if v not in ("", None))[:300]
    vfields = ", ".join(k for k, v in _flat(vars_[0]).items() if v not in ("", None))[:300] if vars_ else "-"
    sample = f"{_variant_text(_get(vars_[0], 'Ozellikler'))} · stok {_num(_get(vars_[0], 'StokAdedi'))}" if vars_ else "-"
    brands = _brand_map(client, cfg)
    mk = f" Marka: {_brand_name(u, brands) or 'bulunamadı'} (marka listesi: {len(brands)} kayıt), kategori: {_category_name(u) or 'bulunamadı'}."
    return (f" Stok testi: '{label}' filtresiyle ilk {len(page)} üründe {n_var} varyasyon.{mk} Ürün alanları: {fields}."
            f" Varyasyon alanları: {vfields}. Örnek varyasyon: {sample}. Okuma işlemleri: {sel[:400]}.")


def _brand_map(client, cfg) -> dict:
    """Marka ID → ad. Ürün servisinde SelectMarka varsa kullanılır; yoksa boş döner."""
    out = {}
    try:
        ops = {op for svc in client.wsdl.services.values() for port in svc.ports.values() for op in port.binding._operations}
        if "SelectMarka" not in ops:
            return out
        try:
            res = client.service.SelectMarka(UyeKodu=cfg["uye_kodu"], markaID=0)
        except Exception:  # noqa: BLE001 — parametre adı sürüme göre değişebiliyor
            res = client.service.SelectMarka(UyeKodu=cfg["uye_kodu"])
        for m in _unwrap(res, "Marka"):
            mid = str(_get(m, "ID", default="") or "")
            name = str(_get(m, "Tanim", "MarkaAdi", "Ad", "Isim", default="") or "").strip()
            if mid and name:
                out[mid] = name
    except Exception as e:  # noqa: BLE001
        log.info("Ticimax marka listesi alınamadı: %s", str(e)[:150])
    return out


def _brand_name(u, brands: dict) -> str:
    m = _get(u, "Marka", "MarkaAdi", default="")
    if m and not isinstance(m, (int, float)):
        txt = str(_get(m, "Tanim", "MarkaAdi", default="") or "") if not isinstance(m, str) else m
        if txt.strip():
            return txt.strip()
    mid = str(_get(u, "MarkaID", default="") or "")
    return brands.get(mid, "")


def _category_name(u) -> str:
    c = _get(u, "AnaKategori", "KategoriAdi", default="")
    if c and not isinstance(c, (str, int, float)):
        c = _get(c, "Tanim", "KategoriAdi", "Ad", default="")
    c = str(c or "").strip()
    if c and not c.isdigit():
        return c.split(">")[-1].strip() if ">" in c else c
    return ""


def fetch_stock(cfg) -> list:
    client = _urun_client(cfg)
    label, flt, first = _pick_filter(client, cfg)
    if not label:
        raise ConnectorError("Ticimax ürün servisi hiç ürün döndürmedi (bütün filtre seçenekleri denendi).")
    size = 100

    def get_page(index):
        try:
            return _urun_page(client, cfg, flt, index, size)
        except Exception as e:  # noqa: BLE001
            raise ConnectorError(f"Ticimax ürün servisi hatası: {str(e)[:250]}") from e

    brands = _brand_map(client, cfg)
    rows = []
    log.info("Ticimax ürün servisi: '%s' filtresiyle ürünler çekiliyor", label)
    for u in _paged(get_page, size):
        pname = str(_get(u, "UrunAdi", default="") or "")
        pid = str(_get(u, "ID", default="") or "")
        p_active = bool(_get(u, "Aktif", default=True))
        brand = _brand_name(u, brands)
        category = _category_name(u)
        for v in _unwrap(_get(u, "Varyasyonlar"), "Varyasyon"):
            rows.append(StockRow(
                product_id=pid, variant_id=str(_get(v, "ID", default="") or ""), product_name=pname,
                variant=_variant_text(_get(v, "Ozellikler")), sku=str(_get(v, "StokKodu", default="") or ""),
                barcode=str(_get(v, "Barkod", default="") or ""), stock=_num(_get(v, "StokAdedi", default=0)),
                price=_num(_get(v, "SatisFiyati", default=0)),
                active=p_active and bool(_get(v, "Aktif", default=True)), brand=brand, category=category))
    return rows


def _paged(get_page, size=100, max_pages=3000):
    """Ticimax sayfalaması: BaslangicIndex bazı sitelerde kayıt sırası (0, 100, 200…), bazılarında sayfa
    numarası (0, 1, 2…) olarak çalışıyor. İkinci sayfada hangisinin yeni kayıt getirdiğine bakılır;
    aynı kayıt iki kez alınmaz."""
    rows, seen = [], set()

    def fresh(page):
        out = []
        for x in page:
            k = str(_get(x, "ID", "SiparisID", default=id(x)))
            if k not in seen:
                seen.add(k)
                out.append(x)
        return out

    page = get_page(0)
    rows += fresh(page)
    if len(page) < size:
        return rows
    step = None
    for mode_step, idx in ((size, size), (1, 1)):
        page = get_page(idx)
        new = fresh(page)
        if new:
            step, index = mode_step, idx
            rows += new
            break
    if step is None:
        return rows
    for _ in range(max_pages):
        if len(page) < size:
            return rows
        index += step
        page = get_page(index)
        new = fresh(page)
        if not new:
            return rows
        rows += new
    return rows


def _call(cfg, start: datetime, end: datetime):
    client = _client(cfg)
    size = 100

    def get_page(index):
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
        return list(page or [])
    rows = _paged(get_page, size)
    log.info("Ticimax siparişleri %s – %s: %s kayıt", start, end, len(rows))
    # Sayfalama hiç çalışmıyorsa (tam 100'ün katı kadar kayıt) aralık ikiye bölünüp ayrı ayrı istenir
    if rows and len(rows) % size == 0 and (end - start) > timedelta(hours=6):
        mid = start + (end - start) / 2
        seen, out = set(), []
        for part in (_call(cfg, start, mid), _call(cfg, mid + timedelta(seconds=1), end)):
            for x in part:
                k = str(_get(x, "ID", "SiparisID", default=id(x)))
                if k not in seen:
                    seen.add(k)
                    out.append(x)
        if len(out) > len(rows):
            return out
    return rows


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
    ch = _channel(o, cfg)
    pay_label, counts = _payment_state(o)
    # Pazaryeri siparişlerinin ödemesi pazaryerinde alınır; ödeme kuralı sadece web sitesi siparişlerine uygulanır
    # Servis ödeme bilgisi göndermezse yedek kural: web sitesindeki "Sipariş Alındı" (kod 0) = tamamlanmamış ödeme
    unpaid = (ch == channels.WEB) and (not counts or (pay_label == "" and d == 0))
    return OrderRow(
        external_id=str(_get(o, "ID", "SiparisID")), number=str(_get(o, "SiparisNo", "SiparisKodu", "ID", default="")),
        created_at=created or datetime.now(), total=float(total or 0),
        currency=str(_get(o, "ParaBirimi", default="TRY"))[:8],
        status=DURUM.get(d, str(d)) if d is not None else "",
        payment_status=pay_label,
        customer=str(_get(o, "AdiSoyadi", "UyeAdiSoyadi", default="")).strip(),
        is_pending=d in PENDING and not unpaid,
        is_cancelled=unpaid,   # sipariş statüsü ayrımı yok; ciroyu ödeme statüsü belirler (web sitesi)
        channel=ch,
        commission=abs(_num(_get(o, "PazaryeriKomisyonTutari", default=0))),
        cost=_num(_get(o, "Maliyet", default=0)),
    )


def _step(cfg, text):
    p = cfg.get("_progress")
    if isinstance(p, dict):
        p["step"] = text
    log.info("Ticimax test: %s", text)


def test(cfg) -> str:
    import time as _t
    now = datetime.now(config.TIMEZONE).replace(tzinfo=None)
    _step(cfg, f"sipariş servisi tanımı (WSDL) indiriliyor: https://{cfg.get('domain', '').strip()}/Servis/SiparisServis.svc?wsdl")
    t0 = _t.time()
    _client(cfg)
    _step(cfg, f"WSDL alındı ({_t.time() - t0:.1f} sn); son 1 günün siparişleri isteniyor")
    t0 = _t.time()
    rows = _call(cfg, now - timedelta(days=1), now)
    took = _t.time() - t0
    if took < 8:   # hızlı sitelerde daha anlamlı bir özet için 7 gün
        _step(cfg, f"1 günlük sipariş {took:.1f} sn'de geldi; son 7 gün isteniyor")
        rows, span = _call(cfg, now - timedelta(days=7), now), "7 günde"
    else:
        span = f"1 günde (bu sitede 1 günlük sipariş {took:.0f} sn sürüyor; ilk senkronizasyon parça parça yapılır)"
    _step(cfg, "siparişler alındı; ürün ve stok servisi deneniyor")
    if not rows:
        return f"Bağlantı başarılı, ancak son {span} sipariş bulunamadı."
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
    raws = [_raw_items(o) for o in rows]
    if all(x is None for x in raws):
        items_info = " Ürün kalemleri sipariş listesinde gelmiyor, her sipariş için ayrıca istenecek."
    else:
        n_items = sum(len(x or []) for x in raws)
        mode = "birim fiyat" if _unit_price_mode([(p.total, x) for p, x in zip(parsed, raws)]) else "satır toplamı"
        n_camp = sum(1 for x in raws for u in (x or []) if _campaign_id(_get(u, "KampanyaID", default="")))
        items_info = (f" Ürün kalemleri: {n_items} kalem (kalem tutarı {mode} olarak okunuyor), "
                      f"{n_camp} kalemde site kampanyası var.")
    unpaid = sum(1 for x in parsed if x.is_cancelled)
    if any(_payments(o) is not None for o in rows):
        cross = Counter(f"{'Web' if x.channel == channels.WEB else 'Pazaryeri'}: {x.status or '?'} / "
                        f"{x.payment_status or '?'}" for x in parsed)
        tips = Counter(str(_int(_get(p, "OdemeTipi"))) for o in rows for p in (_payments(o) or []))
        payinfo = (" Sipariş statüsü / ödeme statüsü: " + "; ".join(f"{k} {v}" for k, v in cross.most_common(14))
                   + ". Ödeme tipleri: " + ", ".join(f"{k}:{v}" for k, v in tips.most_common(8)) + ".")
    else:
        payinfo = " UYARI: Ticimax sipariş verisinde ödeme bilgisi (Odemeler) gelmiyor, ödeme statüsü okunamıyor."
    return (f"Bağlantı başarılı: son {span} {len(rows)} sipariş, {unpaid} tanesi ödeme statüsü nedeniyle ciro dışı."
            + payinfo + items_info + stock_diagnostics(cfg) + " Kanallar: "
            + ", ".join(f"{k} {v}" for k, v in dist.most_common())
            + f". Örnek: #{r.number} · {r.total:,.2f} · {r.status}."
            + (f" Kanal alanları: {hints}." if hints else " Kanal alanı bulunamadı.")
            + f" Gelen alanlar: {names}")


def fetch(cfg, start: date, end: date) -> FetchResult:
    rows = _call(cfg, datetime.combine(start, dtime.min), datetime.combine(end, dtime.max))
    out = [_to_row(o, cfg) for o in rows]
    raws = [_raw_items(o) for o in rows]
    if raws and all(r is None for r in raws):
        # Sipariş listesinde ürünler gelmiyorsa her sipariş için ayrı ayrı istenir
        client = _client(cfg)
        log.info("Ticimax: ürün kalemleri listede yok, %s sipariş için ayrı ayrı isteniyor", len(rows))
        raws = []
        for n, o in enumerate(rows, 1):
            raws.append(_fetch_items_separately(cfg, client, _get(o, "ID", "SiparisID")))
            if n % 100 == 0:
                log.info("Ticimax ürün kalemleri: %s / %s", n, len(rows))
    unit = _unit_price_mode([(r.total, raw) for r, raw in zip(out, raws)])
    for r, raw in zip(out, raws):
        r.items = _items(raw, unit) if raw is not None else None
    return FetchResult(orders=out)
