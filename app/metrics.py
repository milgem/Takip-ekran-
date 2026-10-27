"""Panelde gösterilen hesaplamalar: özetler, karşılaştırmalar, grafikler, uyarılar."""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import func, select

from . import config
from .channels import WEB, is_marketplace
from .connectors import AD_KINDS, STORE_KINDS
from .db import AdCampaignDaily, AdDaily, Firm, Order, UnpaidHourly

PRESETS = {
    "bugun": "Bugün", "dun": "Dün", "7g": "Son 7 gün", "30g": "Son 30 gün", "buay": "Bu ay", "gecenay": "Geçen ay",
}


def resolve_range(preset: str, start: str | None = None, end: str | None = None) -> tuple[date, date, str]:
    t = datetime.now(config.TIMEZONE).date()
    if start and end:
        try:
            a, b = date.fromisoformat(start), date.fromisoformat(end)
            return min(a, b), max(a, b), "ozel"
        except ValueError:
            pass
    if preset == "dun":
        return t - timedelta(days=1), t - timedelta(days=1), preset
    if preset == "7g":
        return t - timedelta(days=6), t, preset
    if preset == "30g":
        return t - timedelta(days=29), t, preset
    if preset == "buay":
        return t.replace(day=1), t, preset
    if preset == "gecenay":
        last = t.replace(day=1) - timedelta(days=1)
        return last.replace(day=1), last, preset
    return t, t, "bugun"


def previous(start: date, end: date) -> tuple[date, date]:
    n = (end - start).days + 1
    return start - timedelta(days=n), start - timedelta(days=1)


@dataclass
class Summary:
    revenue: float = 0          # tüm kanallar
    orders: int = 0
    web_revenue: float = 0      # sadece web sitesi — reklam ROAS'ı bununla hesaplanır
    web_orders: int = 0
    mp_revenue: float = 0       # pazaryerleri toplamı
    mp_orders: int = 0
    cancelled: int = 0
    spend: float = 0
    spend_meta: float = 0
    spend_google: float = 0
    conv_value: float = 0
    conversions: float = 0
    leads: float = 0
    clicks: int = 0
    impressions: int = 0

    @property
    def aov(self):
        return self.revenue / self.orders if self.orders else 0

    @property
    def web_aov(self):
        return self.web_revenue / self.web_orders if self.web_orders else 0

    @property
    def web_share(self):
        return self.web_revenue / self.revenue * 100 if self.revenue else None

    @property
    def roas(self):          # WEB sitesi cirosu / toplam reklam harcaması (pazaryeri hariç)
        return self.web_revenue / self.spend if self.spend else None

    @property
    def platform_roas(self):  # reklam platformlarının raporladığı dönüşüm değeri / harcama
        return self.conv_value / self.spend if self.spend else None

    @property
    def cpl(self):
        return self.spend / self.leads if self.leads else None

    @property
    def cpc(self):
        return self.spend / self.clicks if self.clicks else None


def _dt(d: date, end=False):
    return datetime.combine(d, datetime.max.time() if end else datetime.min.time())


def summary(s, firm_id: int, start: date, end: date, until_now: bool = False) -> Summary:
    """until_now: önceki dönemi 'aynı saate kadar' karşılaştırmak için (bugün vs dün)."""
    out = Summary()
    end_dt = _dt(end, end=True)
    if until_now:
        now = datetime.now(config.TIMEZONE).replace(tzinfo=None)
        end_dt = datetime.combine(end, now.time())
    for ch, rev, cnt in s.execute(select(Order.channel, func.coalesce(func.sum(Order.total), 0), func.count(Order.id)).where(
            Order.firm_id == firm_id, Order.is_cancelled.is_(False),
            Order.created_at >= _dt(start), Order.created_at <= end_dt).group_by(Order.channel)):
        rev, cnt = float(rev), int(cnt)
        out.revenue += rev
        out.orders += cnt
        if is_marketplace(ch or WEB):
            out.mp_revenue += rev
            out.mp_orders += cnt
        elif (ch or WEB) == WEB:
            out.web_revenue += rev
            out.web_orders += cnt
    out.cancelled = (s.scalar(select(func.count(Order.id)).where(
        Order.firm_id == firm_id, Order.is_cancelled.is_(True),
        Order.created_at >= _dt(start), Order.created_at <= end_dt)) or 0) + int(s.scalar(select(
            func.coalesce(func.sum(UnpaidHourly.count), 0)).where(
            UnpaidHourly.firm_id == firm_id, UnpaidHourly.hour >= _dt(start), UnpaidHourly.hour <= end_dt)) or 0)
    for src, sp, cv, cn, ld, cl, im in s.execute(select(
            AdDaily.source, func.sum(AdDaily.spend), func.sum(AdDaily.conversion_value), func.sum(AdDaily.conversions),
            func.sum(AdDaily.leads), func.sum(AdDaily.clicks), func.sum(AdDaily.impressions)).where(
            AdDaily.firm_id == firm_id, AdDaily.day >= start, AdDaily.day <= end).group_by(AdDaily.source)):
        out.spend += sp or 0
        out.conv_value += cv or 0
        out.conversions += cn or 0
        out.leads += ld or 0
        out.clicks += cl or 0
        out.impressions += im or 0
        if src == "meta":
            out.spend_meta += sp or 0
        else:
            out.spend_google += sp or 0
    return out


def change(cur, prev):
    if cur is None or prev in (None, 0):
        return None
    return (cur - prev) / prev * 100


def pending_count(s, firm_id: int) -> tuple[int, int]:
    """(toplam bekleyen, 48 saatten eski bekleyen)"""
    base = select(func.count(Order.id)).where(Order.firm_id == firm_id, Order.is_pending.is_(True),
                                              Order.is_cancelled.is_(False))
    old_limit = datetime.now(config.TIMEZONE).replace(tzinfo=None) - timedelta(hours=48)
    return (s.scalar(base) or 0, s.scalar(base.where(Order.created_at < old_limit)) or 0)


def channel_breakdown(s, firm_id: int, start: date, end: date) -> list[dict]:
    """Kanal bazında ciro, sipariş, pay ve önceki döneme göre değişim."""
    def grab(a, b):
        return {ch or WEB: (float(r), int(c)) for ch, r, c in s.execute(
            select(Order.channel, func.sum(Order.total), func.count(Order.id)).where(
                Order.firm_id == firm_id, Order.is_cancelled.is_(False),
                Order.created_at >= _dt(a), Order.created_at <= _dt(b, True)).group_by(Order.channel))}
    cur = grab(start, end)
    prev = grab(*previous(start, end))
    total = sum(r for r, _ in cur.values()) or 0
    rows = []
    for ch in set(cur) | set(prev):
        r, c = cur.get(ch, (0.0, 0))
        pr, _ = prev.get(ch, (0.0, 0))
        rows.append({"channel": ch, "revenue": r, "orders": c, "aov": r / c if c else 0,
                     "share": r / total * 100 if total else 0, "chg": change(r, pr),
                     "marketplace": is_marketplace(ch)})
    rows.sort(key=lambda x: -x["revenue"])
    return rows


def daily_series(s, firm_id: int, start: date, end: date) -> dict:
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    rev = {d: 0.0 for d in days}
    web = {d: 0.0 for d in days}
    cnt = {d: 0 for d in days}
    spend = {d: 0.0 for d in days}
    for o in s.execute(select(Order.created_at, Order.total, Order.channel).where(
            Order.firm_id == firm_id, Order.is_cancelled.is_(False),
            Order.created_at >= _dt(start), Order.created_at <= _dt(end, True))):
        d = o.created_at.date()
        rev[d] += o.total
        cnt[d] += 1
        if not is_marketplace(o.channel or WEB):
            web[d] += o.total
    for a in s.execute(select(AdDaily.day, func.sum(AdDaily.spend)).where(
            AdDaily.firm_id == firm_id, AdDaily.day >= start, AdDaily.day <= end).group_by(AdDaily.day)):
        spend[a[0]] = float(a[1] or 0)
    return {"labels": [d.strftime("%d.%m") for d in days], "revenue": [round(rev[d], 2) for d in days],
            "web": [round(web[d], 2) for d in days], "mp": [round(rev[d] - web[d], 2) for d in days],
            "orders": [cnt[d] for d in days], "spend": [round(spend[d], 2) for d in days]}


def hourly_today(s, firm_id: int) -> dict:
    t = datetime.now(config.TIMEZONE).date()
    today, yday = [0.0] * 24, [0.0] * 24
    for o in s.execute(select(Order.created_at, Order.total).where(
            Order.firm_id == firm_id, Order.is_cancelled.is_(False),
            Order.created_at >= _dt(t - timedelta(days=1)))):
        (today if o.created_at.date() == t else yday)[o.created_at.hour] += o.total
    # kümülatif
    for arr in (today, yday):
        for i in range(1, 24):
            arr[i] += arr[i - 1]
    now_h = datetime.now(config.TIMEZONE).hour
    return {"labels": [f"{h:02d}" for h in range(24)],
            "today": [round(v, 2) if i <= now_h else None for i, v in enumerate(today)],
            "yesterday": [round(v, 2) for v in yday]}


@dataclass
class Alert:
    level: str   # "red" | "amber" | "green"
    text: str
    tab: str = ""        # ilgili firma sekmesi (ozet, satis, siparis, urunler, reklam)
    smart: bool = False  # akıllı uyarı mı


def firm_card(s, firm: Firm, start: date, end: date, preset: str) -> dict:
    kinds = {i.kind for i in firm.integrations if i.enabled}
    cur = summary(s, firm.id, start, end)
    ps, pe = previous(start, end)
    prev = summary(s, firm.id, ps, pe, until_now=(preset == "bugun"))
    pend, pend_old = pending_count(s, firm.id) if kinds & STORE_KINDS else (0, 0)
    alerts: list[Alert] = []
    for i in firm.integrations:
        if i.enabled and i.last_ok is False:
            alerts.append(Alert("red", f"{i.kind.replace('_', ' ').title()} bağlantı hatası: {i.last_message[:90]}"))
    if kinds & STORE_KINDS and kinds & AD_KINDS and cur.spend > 0 and cur.roas is not None and cur.roas < firm.roas_alert:
        alerts.append(Alert("amber", f"ROAS {cur.roas:.1f} — hedef {firm.roas_alert:.1f} altında"))
    if pend_old:
        alerts.append(Alert("amber", f"{pend_old} sipariş 48 saattir kargolanmadı"))
    if kinds & STORE_KINDS and preset == "bugun" and cur.orders == 0 and datetime.now(config.TIMEZONE).hour >= 12:
        alerts.append(Alert("red", "Öğleden sonra ve bugün hiç sipariş yok"))
    rc = change(cur.revenue, prev.revenue)
    if kinds & STORE_KINDS and rc is not None and rc <= -30:
        alerts.append(Alert("amber", f"Ciro önceki döneme göre %{abs(rc):.0f} düşük"))
    if kinds & AD_KINDS and cur.spend and prev.spend and (cur.spend - prev.spend) / prev.spend > 0.5:
        alerts.append(Alert("amber", "Reklam harcaması önceki döneme göre %50+ arttı"))
    from . import insights
    smart = insights.smart_alerts(s, firm, lambda level, text, tab="": Alert(level, text, tab, True))
    alerts = smart + alerts
    alerts.sort(key=lambda a: {"red": 0, "amber": 1, "green": 2}.get(a.level, 3))
    return {
        "firm": firm, "kinds": kinds, "has_store": bool(kinds & STORE_KINDS), "has_ads": bool(kinds & AD_KINDS),
        "cur": cur, "prev": prev, "pending": pend, "pending_old": pend_old, "alerts": alerts,
        "has_mp": cur.mp_orders > 0 or prev.mp_orders > 0,
        "chg": {"revenue": rc, "web_revenue": change(cur.web_revenue, prev.web_revenue),
                "mp_revenue": change(cur.mp_revenue, prev.mp_revenue), "orders": change(cur.orders, prev.orders), "spend": change(cur.spend, prev.spend),
                "roas": change(cur.roas, prev.roas), "leads": change(cur.leads, prev.leads),
                "aov": change(cur.aov, prev.aov), "cpl": change(cur.cpl, prev.cpl)},
    }


def campaigns(s, firm_id: int, start: date, end: date) -> list[dict]:
    """Kampanya bazında dönem toplamları ve önceki döneme göre harcama değişimi."""
    def grab(a, b):
        q = select(AdCampaignDaily.source, AdCampaignDaily.campaign_id,
                   func.sum(AdCampaignDaily.spend), func.sum(AdCampaignDaily.impressions),
                   func.sum(AdCampaignDaily.clicks), func.sum(AdCampaignDaily.conversions),
                   func.sum(AdCampaignDaily.conversion_value), func.sum(AdCampaignDaily.leads)).where(
            AdCampaignDaily.firm_id == firm_id, AdCampaignDaily.day >= a, AdCampaignDaily.day <= b).group_by(
            AdCampaignDaily.source, AdCampaignDaily.campaign_id)
        return {(r[0], r[1]): r[2:] for r in s.execute(q)}
    cur = grab(start, end)
    if not cur:
        return []
    prev = grab(*previous(start, end))
    # kampanyanın en güncel adı ve durumu
    latest = {}
    for src, cid, name, st, day in s.execute(select(
            AdCampaignDaily.source, AdCampaignDaily.campaign_id, AdCampaignDaily.name, AdCampaignDaily.status,
            AdCampaignDaily.day).where(AdCampaignDaily.firm_id == firm_id, AdCampaignDaily.day <= end).order_by(
            AdCampaignDaily.day)):
        latest[(src, cid)] = (name, st)
    total_spend = sum(float(v[0] or 0) for v in cur.values()) or 0
    rows = []
    for key, (sp, im, cl, cv, val, ld) in cur.items():
        sp, im, cl, cv, val, ld = float(sp or 0), int(im or 0), int(cl or 0), float(cv or 0), float(val or 0), float(ld or 0)
        name, st = latest.get(key, ("", ""))
        rows.append({
            "source": key[0], "id": key[1], "name": name or key[1], "status": st,
            "spend": sp, "impressions": im, "clicks": cl, "conversions": cv, "value": val, "leads": ld,
            "ctr": cl / im * 100 if im else None, "cpc": sp / cl if cl else None,
            "roas": val / sp if sp else None, "cpa": sp / cv if cv else None, "cpl": sp / ld if ld else None,
            "share": sp / total_spend * 100 if total_spend else 0,
            "chg": change(sp, float((prev.get(key) or [0])[0] or 0)),
        })
    rows.sort(key=lambda x: -x["spend"])
    return rows


def unpaid_summary(s, firm_id: int, start: date, end: date) -> dict:
    """Ödemesi tamamlanmayan denemeler: durum ve gün bazında; web sitesi ödeme başarı oranı."""
    rows = list(s.execute(select(UnpaidHourly.hour, UnpaidHourly.channel, UnpaidHourly.payment_status,
                                 UnpaidHourly.count, UnpaidHourly.amount).where(
        UnpaidHourly.firm_id == firm_id, UnpaidHourly.hour >= _dt(start), UnpaidHourly.hour <= _dt(end, True))))
    by_status, by_day = {}, {}
    for hour, ch, st, n, amt in rows:
        x = by_status.setdefault(st or "Bilinmiyor", [0, 0.0])
        x[0] += n
        x[1] += amt
        d = by_day.setdefault(hour.date(), [0, 0.0])
        d[0] += n
        d[1] += amt
    paid_day = {}
    for created, in s.execute(select(Order.created_at).where(
            Order.firm_id == firm_id, Order.channel == WEB, Order.is_cancelled.is_(False),
            Order.created_at >= _dt(start), Order.created_at <= _dt(end, True))):
        paid_day[created.date()] = paid_day.get(created.date(), 0) + 1
    days = []
    for d in sorted(set(by_day) | set(paid_day), reverse=True):
        u, amt = by_day.get(d, [0, 0.0])
        p = paid_day.get(d, 0)
        days.append({"day": d, "unpaid": u, "amount": amt, "paid": p, "rate": p / (p + u) * 100 if (p + u) else None})
    total_u = sum(v[0] for v in by_status.values())
    total_p = sum(paid_day.values())
    return {"count": total_u, "amount": sum(v[1] for v in by_status.values()),
            "statuses": sorted(({"status": k, "count": v[0], "amount": v[1]} for k, v in by_status.items()), key=lambda x: -x["count"]),
            "days": days, "paid": total_p, "rate": total_p / (total_p + total_u) * 100 if (total_p + total_u) else None}
