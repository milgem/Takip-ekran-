"""Akıllı uyarılar: verileri son haftaların aynı gün/saatiyle karşılaştırıp normalden sapmaları yakalar.
Dışarıya veri göndermez. Sonuçlar firma başına birkaç dakika önbellekte tutulur."""
import time
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import and_, case, func, select

from . import config
from .channels import WEB
from .connectors import AD_KINDS, STORE_KINDS
from .db import AdCampaignDaily, Order, UnpaidHourly

_cache: dict[int, tuple[float, list]] = {}
CACHE_SECONDS = 600


def _now():
    return datetime.now(config.TIMEZONE).replace(tzinfo=None)


def _tl(v):
    return "₺" + f"{v:,.0f}".replace(",", ".")


def smart_alerts(s, firm, Alert) -> list:
    hit = _cache.get(firm.id)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    kinds = {i.kind for i in firm.integrations if i.enabled}
    out = []
    try:
        if kinds & STORE_KINDS:
            out += _payment(s, firm, Alert)
            out += _pacing(s, firm, Alert)
            out += _silence(s, firm, Alert)
            out += _stock(s, firm, Alert)
        if kinds & AD_KINDS:
            out += _campaigns(s, firm, Alert, has_store=bool(kinds & STORE_KINDS))
    except Exception:  # noqa: BLE001 — uyarı hesaplanamazsa sayfa yine açılsın
        import logging
        logging.getLogger("insights").exception("akıllı uyarı hesaplanamadı (firma %s)", firm.id)
    _cache[firm.id] = (time.time(), out)
    return out


def clear_cache(firm_id=None):
    if firm_id is None:
        _cache.clear()
    else:
        _cache.pop(firm_id, None)


# ---------- ödeme altyapısı ----------
def _payment(s, firm, Alert):
    """Web sitesi ödeme başarısı: ödenen siparişler (orders) ile yarıda kalan denemeler (unpaid_hourly) karşılaştırılır."""
    now = _now()
    out = []

    def paid(a, b):
        return s.scalar(select(func.count(Order.id)).where(
            Order.firm_id == firm.id, Order.channel == WEB, Order.is_cancelled.is_(False),
            Order.created_at >= a, Order.created_at < b)) or 0

    def unpaid(a, b):
        return int(s.scalar(select(func.coalesce(func.sum(UnpaidHourly.count), 0)).where(
            UnpaidHourly.firm_id == firm.id, UnpaidHourly.channel == WEB,
            UnpaidHourly.hour >= a.replace(minute=0, second=0, microsecond=0), UnpaidHourly.hour < b)) or 0)
    b0, b1 = now - timedelta(days=28), now - timedelta(hours=3)
    p_base, u_base = paid(b0, b1), unpaid(b0, b1)
    if p_base + u_base < 20:
        return out
    ratio_base = u_base / (p_base + u_base)
    p3, u3 = paid(now - timedelta(hours=3), now + timedelta(hours=1)), unpaid(now - timedelta(hours=3), now + timedelta(hours=1))
    n = p3 + u3
    if n >= 4 and u3 >= 3 and u3 / n >= max(min(ratio_base * 1.5, 0.95), ratio_base + 0.2):
        out.append(Alert("red", f"Son 3 saatte web sitesindeki ödeme denemelerinin %{u3 / n * 100:.0f}'i yarıda kaldı "
                                f"({u3} yarıda kalan, {p3} başarılı); normalde %{ratio_base * 100:.0f}. Ödeme altyapısını (sanal POS) kontrol et.",
                         tab="siparis"))
        return out
    last_paid = s.scalar(select(func.max(Order.created_at)).where(
        Order.firm_id == firm.id, Order.channel == WEB, Order.is_cancelled.is_(False)))
    if last_paid and now - last_paid > timedelta(hours=4):
        tries = unpaid(last_paid + timedelta(hours=1), now + timedelta(hours=1))
        if tries >= 3:
            h = (now - last_paid).total_seconds() / 3600
            out.append(Alert("red", f"Web sitesinde {h:.0f} saattir ödemesi tamamlanan sipariş yok ama {tries} ödeme denemesi "
                                    "yarıda kaldı. Ödeme sayfası çalışmıyor olabilir.", tab="siparis"))
    return out


# ---------- günün temposu ----------
def _pacing(s, firm, Alert):
    now = _now()
    if now.hour < 11:
        return []

    def upto(day_start, cut):
        r = s.execute(select(func.coalesce(func.sum(Order.total), 0), func.count(Order.id)).where(
            Order.firm_id == firm.id, Order.is_cancelled.is_(False),
            Order.created_at >= day_start, Order.created_at <= cut)).one()
        return float(r[0]), int(r[1])

    today0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    cur_rev, cur_n = upto(today0, now)
    past = [upto(today0 - timedelta(days=7 * w), now - timedelta(days=7 * w)) for w in range(1, 5)]
    past = [p for p in past if p[1] > 0]
    if len(past) < 3:
        return []
    avg_rev = sum(p[0] for p in past) / len(past)
    avg_n = sum(p[1] for p in past) / len(past)
    if avg_n < 5 or avg_rev <= 0:
        return []
    ch = (cur_rev - avg_rev) / avg_rev * 100
    gun = ["pazartesi", "salı", "çarşamba", "perşembe", "cuma", "cumartesi", "pazar"][now.weekday()]
    if ch <= -35:
        return [Alert("red" if ch <= -55 else "amber",
                      f"Bugün saat {now:%H:%M} itibarıyla ciro {_tl(cur_rev)}; son 4 {gun} gününün aynı saatine göre "
                      f"%{abs(ch):.0f} düşük (ortalama {_tl(avg_rev)}).", tab="ozet")]
    if ch >= 50:
        return [Alert("green", f"Bugün iyi gidiyor: saat {now:%H:%M} itibarıyla ciro {_tl(cur_rev)}, son 4 {gun} gününün "
                               f"aynı saatine göre %{ch:.0f} yüksek.", tab="ozet")]
    return []


# ---------- kanal sessizliği ----------
def _silence(s, firm, Alert):
    now = _now()
    since = now - timedelta(days=14)
    prof = defaultdict(lambda: [0.0] * 24)
    last = {}
    for ch, created in s.execute(select(Order.channel, Order.created_at).where(
            Order.firm_id == firm.id, Order.created_at >= since, Order.created_at <= now)):
        ch = ch or WEB
        prof[ch][created.hour] += 1 / 14
        if ch not in last or created > last[ch]:
            last[ch] = created
    out = []
    for ch, hours in prof.items():
        if sum(hours) * 14 < 30:            # az siparişli kanallarda yanlış alarm olmasın
            continue
        lo = last[ch]
        if now - lo < timedelta(hours=3):
            continue
        expected, t = 0.0, lo.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        while t <= now:
            expected += hours[t.hour]
            t += timedelta(hours=1)
        if expected >= 4:
            h = (now - lo).total_seconds() / 3600
            out.append(Alert("red" if expected >= 8 else "amber",
                             f"{ch} kanalından {h:.0f} saattir sipariş gelmedi; bu saatlerde normalde yaklaşık "
                             f"{expected:.0f} sipariş geliyor. Entegrasyonu ve mağaza durumunu kontrol et.", tab="satis"))
    return out


# ---------- stok ----------
def _stock(s, firm, Alert):
    from . import analysis
    today = _now().date()
    a = analysis.product_analysis(s, firm.id, today - timedelta(days=29), today)
    if not a["has_stock"]:
        return []
    top = a["products"][:20]
    gone_list, soon_list, lost = [], [], 0.0
    for p in top:
        gone = [v for v in p["variants"] if v["stock"] is not None and v["stock"] <= 0 and v["qty"] >= 2]
        soon = [v for v in p["variants"] if v["days_left"] is not None and v["days_left"] <= 3]
        if gone:
            lost += sum(v["revenue"] for v in gone) / a["days"]
            gone_list.append((p["product"], gone))
        elif soon:
            soon_list.append((p["product"], soon))

    def _desc(items):
        parts = []
        for name, vs in items[:3]:
            nm = name if len(name) <= 42 else name[:40].rstrip() + "…"
            parts.append(f"{nm} ({', '.join((v['variant'] or v['sku'] or '?').split('/')[-1].strip() for v in vs[:4])})")
        more = f" ve {len(items) - 3} model daha" if len(items) > 3 else ""
        return "; ".join(parts) + more

    out = []
    if gone_list:
        out.append(Alert("red", f"{len(gone_list)} çok satan modelde beden tükendi. Günde yaklaşık {_tl(lost)} satış kaçıyor: "
                                f"{_desc(gone_list)}.", tab="urunler"))
    if soon_list:
        out.append(Alert("amber", f"{len(soon_list)} çok satan modelde beden 3 gün içinde tükenecek. {_desc(soon_list)}.",
                         tab="urunler"))
    return out


# ---------- kampanyalar ----------
def _campaigns(s, firm, Alert, has_store):
    today = _now().date()
    a, b = today - timedelta(days=6), today
    pa, pb = today - timedelta(days=13), today - timedelta(days=7)

    def grab(x, y):
        q = select(AdCampaignDaily.source, AdCampaignDaily.campaign_id, func.max(AdCampaignDaily.name),
                   func.sum(AdCampaignDaily.spend), func.sum(AdCampaignDaily.conversions),
                   func.sum(AdCampaignDaily.conversion_value), func.sum(AdCampaignDaily.leads)).where(
            AdCampaignDaily.firm_id == firm.id, AdCampaignDaily.day >= x, AdCampaignDaily.day <= y).group_by(
            AdCampaignDaily.source, AdCampaignDaily.campaign_id)
        return {(r[0], r[1]): {"name": r[2], "spend": float(r[3] or 0), "conv": float(r[4] or 0),
                               "value": float(r[5] or 0), "leads": float(r[6] or 0)} for r in s.execute(q)}
    cur, prev = grab(a, b), grab(pa, pb)
    if not cur:
        return []
    total = sum(c["spend"] for c in cur.values()) or 1
    tracks_conv = any(c["conv"] or c["leads"] for c in list(cur.values()) + list(prev.values()))
    out = []
    for key, c in sorted(cur.items(), key=lambda x: -x[1]["spend"]):
        share = c["spend"] / total
        p = prev.get(key)
        if tracks_conv and c["spend"] >= 500 and share >= 0.1 and not c["conv"] and not c["leads"]:
            out.append(Alert("amber", f"“{c['name']}” kampanyası son 7 günde {_tl(c['spend'])} harcadı ama hiç dönüşüm/lead getirmedi.",
                             tab="reklam"))
            continue
        if has_store and c["conv"] and share >= 0.15:
            roas = c["value"] / c["spend"] if c["spend"] else 0
            proas = (p["value"] / p["spend"]) if p and p["spend"] else None
            if roas < firm.roas_alert * 0.7 and (proas is None or proas >= roas * 1.3):
                txt = f"“{c['name']}” kampanyasının son 7 gün ROAS'ı {roas:.1f} (hedef {firm.roas_alert:.1f})"
                txt += f", önceki hafta {proas:.1f} idi." if proas else "."
                out.append(Alert("amber", txt, tab="reklam"))
        if c["leads"] >= 5 and p and p["leads"] >= 5:
            cpl, pcpl = c["spend"] / c["leads"], p["spend"] / p["leads"]
            if cpl >= pcpl * 1.5:
                out.append(Alert("amber", f"“{c['name']}” kampanyasında lead başı maliyet {_tl(cpl)}; önceki hafta {_tl(pcpl)} idi "
                                          f"(%{(cpl / pcpl - 1) * 100:.0f} artış).", tab="reklam"))
    return out[:5]
