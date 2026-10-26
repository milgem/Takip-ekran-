"""Deneme verisi: `python -m app.demo` — paneli gerçek bağlantı olmadan görmek için.
Gerçek kurulumda ÇALIŞTIRMAYIN (ayrı bir veritabanında deneyin)."""
import random
from datetime import datetime, timedelta

from . import config
from .db import AdDaily, Firm, Integration, Order, SessionLocal, init_db
from .secure import encrypt


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
                i = Integration(firm_id=f.id, kind=k, config_enc=encrypt({}), backfilled=True,
                                last_sync_at=datetime.utcnow() - timedelta(minutes=rnd.randint(2, 25)),
                                last_ok=True, last_message="Deneme verisi")
                s.add(i)
                s.flush()
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
                            s.add(Order(firm_id=f.id, integration_id=i.id, source=k, external_id=f"{d}-{j}",
                                        number=f"{100000 + d * 1000 + j}", created_at=ts,
                                        total=round(max(150, rnd.gauss(aov, aov * .45)), 2),
                                        status="İptal Edildi" if cancelled else ("Onaylandı" if pending else "Kargoya Verildi"),
                                        is_pending=pending, is_cancelled=cancelled, channel=channel))
                    else:
                        base = daily_orders * aov * {"meta": .07, "google_ads": .045}[k] if daily_orders else 1200
                        spend = max(0, rnd.gauss(base, base * .2)) * (now.hour / 24 if d == 0 else 1)
                        roas = rnd.uniform(3.5, 7) if daily_orders else 0
                        s.add(AdDaily(firm_id=f.id, integration_id=i.id, source=k, day=day, spend=round(spend, 2),
                                      impressions=int(spend * 40), clicks=int(spend / 4),
                                      conversions=round(spend * roas / max(aov, 1), 1), conversion_value=round(spend * roas, 2),
                                      leads=0 if daily_orders else round(spend / rnd.uniform(90, 160))))
        s.commit()
    print("Deneme verisi yüklendi.")


if __name__ == "__main__":
    run()
