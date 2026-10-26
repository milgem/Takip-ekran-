"""Deneme verisi: `python -m app.demo` — paneli gerçek bağlantı olmadan görmek için.
Gerçek kurulumda ÇALIŞTIRMAYIN (ayrı bir veritabanında deneyin)."""
import random
from datetime import datetime, timedelta

from . import config
from .db import AdCampaignDaily, AdDaily, Firm, Integration, Order, OrderItem, SessionLocal, VariantStock, init_db
from .secure import encrypt


CATALOG = {
    "ticimax": [("Örnek Keten Gömlek", 899, ["S", "M", "L", "XL"]), ("Örnek Slim Jean", 1299, ["28", "30", "32", "34", "36"]),
                ("Örnek Triko Kazak", 749, ["S", "M", "L"]), ("Örnek Deri Ceket", 3499, ["M", "L", "XL"]),
                ("Örnek Basic Tişört", 299, ["XS", "S", "M", "L", "XL"]), ("Örnek Spor Ayakkabı", 1899, ["40", "41", "42", "43", "44"])],
    "shopify": [("Örnek S3 İş Ayakkabısı", 2190, ["39", "40", "41", "42", "43", "44", "45"]),
                ("Örnek Nitril İş Eldiveni", 89, ["7", "8", "9", "10"]), ("Örnek Koruyucu Gözlük", 349, ["Tek beden"]),
                ("Örnek FFP2 Maske (20'li)", 459, ["Tek beden"]), ("Örnek Kışlık İş Montu", 1590, ["M", "L", "XL", "XXL"])],
}


def _catalog(rnd, kind):
    out = []
    for pi, (name, price, sizes) in enumerate(CATALOG[kind]):
        pop = rnd.uniform(.4, 2.2)
        for si, sz in enumerate(sizes):
            mid = (len(sizes) - 1) / 2
            weight = pop * (1.6 - abs(si - mid) / max(mid, 1) * .9)
            stock = 0 if rnd.random() < .18 else int(rnd.choice([2, 4, 8, 15, 40, 90]))
            out.append({"pid": f"P{pi}", "vid": f"P{pi}-{si}", "name": name, "size": sz, "price": price,
                        "sku": f"{name.split()[1][:3].upper()}-{sz}".replace(" ", ""), "w": weight, "stock": stock})
    return out


def run():
    init_db()
    rnd = random.Random(7)
    now = datetime.now(config.TIMEZONE).replace(tzinfo=None)
    firms = [("Örnek Moda", "#1f4d3f", ["ticimax", "meta", "google_ads"], 380, 2400),
             ("Örnek KKD Mağaza", "#2563eb", ["shopify", "google_ads"], 25, 1900),
             ("Örnek Konut Projesi", "#b45309", ["meta"], 0, 0)]
    with SessionLocal() as s:
        for name, color, kinds, daily_orders, aov in firms:
            f = Firm(name=name, color=color, roas_alert=4 if daily_orders > 100 else 3)
            s.add(f)
            s.flush()
            for k in kinds:
                i = Integration(firm_id=f.id, kind=k, config_enc=encrypt({"demo": True}), backfilled=True,
                                last_sync_at=datetime.utcnow() - timedelta(minutes=rnd.randint(2, 25)),
                                last_ok=True, last_message="Deneme verisi")
                s.add(i)
                s.flush()
                cat = _catalog(rnd, k) if k in CATALOG else []
                for v in cat:
                    s.add(VariantStock(firm_id=f.id, integration_id=i.id, product_id=v["pid"], variant_id=v["vid"],
                                       product_name=v["name"], variant=v["size"], sku=v["sku"], barcode="",
                                       stock=v["stock"], price=v["price"], active=True))
                if cat:
                    i.stock_synced_at, i.stock_message = datetime.utcnow(), f"{len(cat)} varyasyon stoğu güncellendi"
                for d in range(60, -1, -1):
                    day = (now - timedelta(days=d)).date()
                    if k in ("shopify", "ticimax"):
                        n = max(0, int(rnd.gauss(daily_orders, daily_orders * .18)))
                        for j in range(n):
                            ts = datetime.combine(day, datetime.min.time()) + timedelta(
                                minutes=rnd.triangular(0, 1439, 1260))
                            if ts > now:
                                continue
                            age_h = (now - ts).total_seconds() / 3600
                            mix = ([("Web sitesi", 60), ("Trendyol", 20), ("Hepsiburada", 10), ("Amazon", 5), ("LC Waikiki", 5)]
                                   if k == "ticimax" else [("Web sitesi", 85), ("Trendyol", 10), ("Hepsiburada", 5)])
                            channel = rnd.choices([m[0] for m in mix], [m[1] for m in mix])[0]
                            cancelled = rnd.random() < .05
                            pending = not cancelled and (age_h < 30 or (age_h < 72 and rnd.random() < .05))
                            picks = rnd.choices(cat, [v["w"] for v in cat], k=rnd.choice([1, 1, 1, 2, 2, 3]))
                            o = Order(firm_id=f.id, integration_id=i.id, source=k, external_id=f"{d}-{j}",
                                      number=f"{100000 + d * 1000 + j}", created_at=ts,
                                      total=0, status="İptal Edildi" if cancelled else ("Onaylandı" if pending else "Kargoya Verildi"),
                                      is_pending=pending, is_cancelled=cancelled, channel=channel)
                            s.add(o)
                            s.flush()
                            tot = 0
                            for v in picks:
                                q = rnd.choice([1, 1, 1, 2]) if v["price"] > 200 else rnd.choice([1, 2, 5, 10])
                                tot += q * v["price"]
                                s.add(OrderItem(order_id=o.id, firm_id=f.id, integration_id=i.id, product_id=v["pid"],
                                                variant_id=v["vid"], sku=v["sku"], name=v["name"], quantity=q, amount=q * v["price"]))
                            o.total = tot
                    else:
                        base = daily_orders * aov * {"meta": .07, "google_ads": .045}[k] if daily_orders else 1200
                        spend = max(0, rnd.gauss(base, base * .2)) * (now.hour / 24 if d == 0 else 1)
                        roas = rnd.uniform(3.5, 7) if daily_orders else 0
                        s.add(AdDaily(firm_id=f.id, integration_id=i.id, source=k, day=day, spend=round(spend, 2),
                                      impressions=int(spend * 40), clicks=int(spend / 4),
                                      conversions=round(spend * roas / max(aov, 1), 1), conversion_value=round(spend * roas, 2),
                                      leads=0 if daily_orders else round(spend / rnd.uniform(90, 160))))
                        names = ({"meta": [("Örnek – Satış (Advantage+)", .5, "Aktif"), ("Örnek – Retargeting 30 gün", .3, "Aktif"),
                                           ("Örnek – Yeni sezon görsel testi", .2, "Duraklatıldı")],
                                  "google_ads": [("Örnek – Performance Max", .55, "Aktif"), ("Örnek – Arama / Marka", .15, "Aktif"),
                                                 ("Örnek – Arama / Kategori", .3, "Aktif")]}[k] if daily_orders else
                                 [("Örnek – Lead formu / Daire tanıtım", .7, "Aktif"), ("Örnek – Video izlenme", .3, "Aktif")])
                        for n, (cname, w, st) in enumerate(names):
                            sp = spend * w * rnd.uniform(.8, 1.2)
                            r2 = roas * rnd.uniform(.5, 1.5)
                            s.add(AdCampaignDaily(firm_id=f.id, integration_id=i.id, source=k, day=day,
                                                  campaign_id=f"{k}-{n}", name=cname, status=st, spend=round(sp, 2),
                                                  impressions=int(sp * rnd.uniform(25, 60)), clicks=int(sp / rnd.uniform(2.5, 6)),
                                                  conversions=round(sp * r2 / max(aov, 1), 1), conversion_value=round(sp * r2, 2),
                                                  leads=0 if daily_orders else round(sp / rnd.uniform(90, 160))))
        s.commit()
    print("Deneme verisi yüklendi.")


if __name__ == "__main__":
    run()
