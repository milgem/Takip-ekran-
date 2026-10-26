"""Ticimax sipariş servisi test aracı.

Kendi bilgisayarınızda çalıştırın. Yetki kodu hiçbir yere kaydedilmez ve gönderilmez;
sadece kendi Ticimax sitenize bağlanılır. Ekrana müşteri adı, adres, telefon
YAZILMAZ — yalnızca alan adları, kanal bilgileri ve tutar özetleri.

Kurulum (bir kez):   pip install zeep
Çalıştırma:          python ticimax_test.py
"""
import getpass
import json
from collections import Counter
from datetime import datetime, timedelta

try:
    from zeep import Client, Settings
    from zeep.helpers import serialize_object
except ImportError:
    raise SystemExit("Önce şunu çalıştırın:  pip install zeep")

PERSONAL = ("ad", "soyad", "isim", "adres", "tel", "gsm", "mail", "eposta", "tc", "vergi", "uye", "musteri",
            "fatura", "teslimat", "not", "aciklama", "ipadres", "kargotakip")
CHANNEL_HINTS = ("kayna", "kanal", "pazar", "entegrasyon", "market", "platform")
MONEY_HINTS = ("tutar", "toplam", "fiyat", "odeme")


def flat(obj, prefix="", depth=0, out=None):
    out = {} if out is None else out
    if isinstance(obj, dict) and depth < 2:
        for k, v in obj.items():
            flat(v, f"{prefix}.{k}" if prefix else k, depth + 1, out)
    elif isinstance(obj, (str, int, float, datetime)) and not isinstance(obj, bool):
        out[prefix] = obj
    return out


def is_personal(name):
    n = name.lower()
    return any(p in n for p in PERSONAL) and not any(h in n for h in CHANNEL_HINTS + MONEY_HINTS)


def paging(types, idx):
    t = types["WebSiparisSayfalama"]
    names = {n for n, _ in t.elements}
    vals = {"BaslangicIndex": idx, "KayitSayisi": 100, "SiralamaYonu": "ASC", "SiralamaDegeri": "ID", "SiralamaDeger": "ID"}
    return t(**{k: v for k, v in vals.items() if k in names})


def main():
    domain = input("Site alan adı (örn. www.siteniz.com): ").strip().replace("https://", "").replace("http://", "").strip("/")
    code = getpass.getpass("Web servis yetki kodu (yazarken görünmez): ").strip()
    print("\nBağlanılıyor…")
    client = Client(f"https://{domain}/Servis/SiparisServis.svc?wsdl", settings=Settings(strict=False))
    types = {t.name: t for t in client.wsdl.types.types if getattr(t, "name", None)}

    end = datetime.now()
    start = end - timedelta(days=7)
    ft = types["WebSiparisFiltre"]
    fvals = {}
    for name, el in ft.elements:
        tname = getattr(el.type, "name", "") or ""
        if name == "SiparisTarihiBas":
            fvals[name] = start
        elif name == "SiparisTarihiSon":
            fvals[name] = end
        elif tname in ("int", "long", "short"):
            fvals[name] = -1
    rows, idx = [], 0
    while True:
        page = client.service.SelectSiparis(
            UyeKodu=code, f=ft(**fvals),
            s=paging(types, idx))
        page = list(page or [])
        rows += page
        if len(page) < 100 or len(rows) >= 1000:
            break
        idx += 100

    report = {"siparis_sayisi_7_gun": len(rows), "filtre_alanlari": [n for n, _ in ft.elements]}
    if rows:
        flats = [flat(serialize_object(r)) for r in rows]
        all_fields = sorted({k for f in flats for k in f})
        report["alanlar"] = [k for k in all_fields if not is_personal(k)]
        # Kanal ipucu taşıyan alanlar ve aldıkları değerler
        ch = {}
        for f in flats:
            for k, v in f.items():
                if any(h in k.lower() for h in CHANNEL_HINTS) and v not in ("", None):
                    ch.setdefault(k, Counter())[str(v)[:40]] += 1
        report["kanal_alanlari"] = {k: dict(c.most_common(12)) for k, c in ch.items()}
        # Tutar alanları: toplamları (hangisinin gerçek ciro olduğunu bulmak için)
        money = {}
        for f in flats:
            for k, v in f.items():
                if any(h in k.lower() for h in MONEY_HINTS) and isinstance(v, (int, float)):
                    money[k] = round(money.get(k, 0) + v, 2)
        report["tutar_alanlari_7_gun_toplam"] = money
        # Durum alanları
        st = {}
        for f in flats:
            for k, v in f.items():
                if "durum" in k.lower() and v not in ("", None):
                    st.setdefault(k, Counter())[str(v)[:30]] += 1
        report["durum_alanlari"] = {k: dict(c.most_common(12)) for k, c in st.items()}

    out = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    with open("ticimax_test_sonuc.json", "w", encoding="utf-8") as fh:
        fh.write(out)
    print(out)
    print("\n✓ Sonuç 'ticimax_test_sonuc.json' dosyasına kaydedildi. Bu dosyayı Claude'a gönderebilirsiniz "
          "(müşteri bilgisi ve yetki kodu içermez).")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        print("\n✕ Hata:", msg[:500])
        if "403" in msg or "yetki" in msg.lower():
            print("  → Yetki kodu hatalı olabilir ya da Ticimax web servisinde IP kısıtlaması var "
                  "(bilgisayarınızın IP'sini izinli listeye ekleyin).")
        if "404" in msg or "Name or service" in msg or "getaddrinfo" in msg:
            print("  → Alan adını kontrol edin (örn. www.siteniz.com).")
