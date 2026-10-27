"""Analiz sayfası: sipariş önerisi, kanal kârlılığı, ölü stok, gün × saat ısı haritası."""
import math
from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import func, select

from . import analysis, config
from .channels import WEB, is_marketplace
from .db import AdDaily, Order, OrderItem, VariantStock


def _dt(d: date, end=False):
    return datetime.combine(d, datetime.max.time() if end else datetime.min.time())


def _today():
    return datetime.now(config.TIMEZONE).date()


# ---------- sipariş önerisi ----------
def reorder(s, firm_id: int, lead: int = 14, cover: int = 30, window: int = 30) -> dict:
    """Son `window` günün satış hızıyla: tedarik süresi + kaç günlük stok isteniyorsa o kadar sipariş önerisi."""
    t = _today()
    a = analysis.product_analysis(s, firm_id, t - timedelta(days=window - 1), t)
    brand_of = {p["pkey"]: (p["brand"] or "Markası belirtilmemiş") for p in a["products"]}
    rows = []
    for v in a["variants"]:
        if v["stock"] is None or not v["qty"]:
            continue
        daily = v["qty"] / window
        days_left = (v["stock"] / daily) if v["stock"] > 0 else 0
        need = daily * (lead + cover) - max(v["stock"], 0)
        if need < 1 or days_left > lead + cover:
            continue
        qty = math.ceil(need)
        urg = "acil" if days_left <= lead else ("yakin" if days_left <= lead + 7 else "plan")
        rows.append({"product": v["product"], "variant": v["variant"] or "—", "sku": v["sku"], "brand": brand_of.get(v["pkey"], "Markası belirtilmemiş"),
                     "sold": v["qty"], "daily": daily, "stock": v["stock"], "days_left": days_left, "suggest": qty,
                     "urgency": urg, "price": v.get("price") or 0, "value": qty * (v.get("price") or 0)})
    order = {"acil": 0, "yakin": 1, "plan": 2}
    rows.sort(key=lambda r: (order[r["urgency"]], r["days_left"], -r["sold"]))
    groups = defaultdict(lambda: {"rows": [], "qty": 0, "value": 0.0, "acil": 0})
    for r in rows:
        g = groups[r["brand"]]
        g["rows"].append(r)
        g["qty"] += r["suggest"]
        g["value"] += r["value"]
        g["acil"] += r["urgency"] == "acil"
    glist = [{"brand": k, **v} for k, v in groups.items()]
    glist.sort(key=lambda g: (-g["acil"], -g["value"]))
    return {"rows": rows, "groups": glist, "lead": lead, "cover": cover, "window": window, "has_stock": a["has_stock"],
            "acil": sum(r["urgency"] == "acil" for r in rows), "total_qty": sum(r["suggest"] for r in rows),
            "total_value": sum(r["value"] for r in rows)}


# ---------- kanal kârlılığı ----------
def profitability(s, firm_id: int, start: date, end: date) -> dict:
    orders = list(s.execute(select(Order.id, Order.channel, Order.total, Order.commission, Order.cost).where(
        Order.firm_id == firm_id, Order.is_cancelled.is_(False),
        Order.created_at >= _dt(start), Order.created_at <= _dt(end, True))))
    item_cost = dict(s.execute(select(OrderItem.order_id, func.sum(OrderItem.cost)).join(Order, Order.id == OrderItem.order_id).where(
        Order.firm_id == firm_id, Order.is_cancelled.is_(False),
        Order.created_at >= _dt(start), Order.created_at <= _dt(end, True)).group_by(OrderItem.order_id)).all())
    spend = float(s.scalar(select(func.coalesce(func.sum(AdDaily.spend), 0)).where(
        AdDaily.firm_id == firm_id, AdDaily.day >= start, AdDaily.day <= end)) or 0)
    ch = defaultdict(lambda: {"orders": 0, "revenue": 0.0, "commission": 0.0, "cost": 0.0, "rev_with_cost": 0.0})
    for oid, channel, total, comm, cost in orders:
        c = ch[channel or WEB]
        c["orders"] += 1
        c["revenue"] += total or 0
        c["commission"] += comm or 0
        oc = (cost or 0) or float(item_cost.get(oid) or 0)
        if oc > 0:
            c["cost"] += oc
            c["rev_with_cost"] += total or 0
    rows = []
    for name, c in ch.items():
        coverage = c["rev_with_cost"] / c["revenue"] * 100 if c["revenue"] else 0
        # maliyeti bilinmeyen siparişler için, bilinenlerin maliyet oranı kullanılır (tahmin)
        cost_ratio = (c["cost"] / c["rev_with_cost"]) if c["rev_with_cost"] else None
        est_cost = c["cost"] + ((c["revenue"] - c["rev_with_cost"]) * cost_ratio if cost_ratio is not None else 0)
        ad = spend if name == WEB else 0.0
        gross = c["revenue"] - c["commission"] - est_cost if cost_ratio is not None else None
        net = gross - ad if gross is not None else None
        rows.append({"channel": name, "marketplace": is_marketplace(name), "orders": c["orders"], "revenue": c["revenue"],
                     "commission": c["commission"], "commission_rate": c["commission"] / c["revenue"] * 100 if c["revenue"] else 0,
                     "cost": est_cost if cost_ratio is not None else None, "coverage": coverage, "ad": ad,
                     "gross": gross, "net": net, "net_margin": (net / c["revenue"] * 100) if (net is not None and c["revenue"]) else None,
                     "aov": c["revenue"] / c["orders"] if c["orders"] else 0})
    rows.sort(key=lambda r: -r["revenue"])
    tot = {k: sum((r[k] or 0) for r in rows) for k in ("orders", "revenue", "commission", "ad")}
    known = [r for r in rows if r["net"] is not None]
    tot["net"] = sum(r["net"] for r in known) if known else None
    tot["cost"] = sum(r["cost"] for r in known) if known else None
    tot["net_margin"] = (tot["net"] / sum(r["revenue"] for r in known) * 100) if known and sum(r["revenue"] for r in known) else None

    # ürün kârlılığı (maliyeti bilinen satırlar; komisyon sipariş tutarına oranla dağıtılır)
    prows = list(s.execute(select(OrderItem.name, OrderItem.amount, OrderItem.cost, OrderItem.quantity, Order.total, Order.commission).join(
        Order, Order.id == OrderItem.order_id).where(
        Order.firm_id == firm_id, Order.is_cancelled.is_(False), OrderItem.cost > 0,
        Order.created_at >= _dt(start), Order.created_at <= _dt(end, True))))
    prod = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])
    for name, amount, cost, q, total, comm in prows:
        p = prod[name or "Adsız ürün"]
        p[0] += amount or 0
        p[1] += cost or 0
        p[2] += (comm or 0) * ((amount or 0) / total) if total else 0
        p[3] += q or 0
    plist = [{"product": n, "revenue": v[0], "cost": v[1], "commission": v[2], "qty": v[3],
              "profit": v[0] - v[1] - v[2], "margin": (v[0] - v[1] - v[2]) / v[0] * 100 if v[0] else None} for n, v in prod.items()]
    plist.sort(key=lambda x: -x["revenue"])
    return {"rows": rows, "tot": tot, "spend": spend, "products": plist[:200],
            "has_cost": any(r["cost"] is not None for r in rows), "has_commission": any(r["commission"] for r in rows)}


# ---------- ölü stok ----------
def dead_stock(s, firm_id: int, days: int = 60) -> dict:
    t = _today()
    since = _dt(t - timedelta(days=days - 1))
    sold_vid, sold_sku = set(), set()
    last_sale = {}
    for integ, vid, sku, created in s.execute(select(OrderItem.integration_id, OrderItem.variant_id, OrderItem.sku, Order.created_at).join(
            Order, Order.id == OrderItem.order_id).where(OrderItem.firm_id == firm_id)):
        for key in ((integ, "v", str(vid or "").lower()), (integ, "s", str(sku or "").lower())):
            if key[2]:
                if created >= since:
                    (sold_vid if key[1] == "v" else sold_sku).add(key)
                if key not in last_sale or created > last_sale[key]:
                    last_sale[key] = created
    oldest = s.scalar(select(func.min(Order.created_at)).where(Order.firm_id == firm_id))
    prods = defaultdict(lambda: {"stock": 0.0, "value": 0.0, "variants": [], "brand": "", "category": "", "last": None})
    for v in s.scalars(select(VariantStock).where(VariantStock.firm_id == firm_id, VariantStock.stock > 0, VariantStock.active.is_(True))):
        kv, ks = (v.integration_id, "v", (v.variant_id or "").lower()), (v.integration_id, "s", (v.sku or "").lower())
        if kv in sold_vid or ks in sold_sku:
            continue
        last = max([d for d in (last_sale.get(kv), last_sale.get(ks)) if d] or [None]) if (last_sale.get(kv) or last_sale.get(ks)) else None
        p = prods[(v.integration_id, v.product_id)]
        p["name"] = v.product_name
        p["brand"], p["category"] = v.brand, v.category
        p["stock"] += v.stock
        p["value"] += v.stock * (v.price or 0)
        p["variants"].append({"variant": v.variant or "—", "sku": v.sku, "stock": v.stock, "price": v.price, "last": last})
        if last and (p["last"] is None or last > p["last"]):
            p["last"] = last
    rows = []
    for p in prods.values():
        p["variants"].sort(key=lambda x: -x["stock"])
        rows.append({"product": p["name"], "brand": p["brand"], "category": p["category"], "stock": p["stock"], "value": p["value"],
                     "variant_count": len(p["variants"]), "variants": p["variants"], "last": p["last"]})
    rows.sort(key=lambda r: -r["value"])
    return {"rows": rows, "days": days, "total_value": sum(r["value"] for r in rows), "total_stock": sum(r["stock"] for r in rows),
            "history_from": oldest, "short_history": bool(oldest and (datetime.now() - oldest).days < days - 2)}


# ---------- gün × saat ısı haritası ----------
DAYS_TR = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]


def heatmap(s, firm_id: int, start: date, end: date, channel: str = "") -> dict:
    q = select(Order.created_at, Order.total, Order.channel).where(
        Order.firm_id == firm_id, Order.is_cancelled.is_(False), Order.created_at >= _dt(start), Order.created_at <= _dt(end, True))
    rev = [[0.0] * 24 for _ in range(7)]
    cnt = [[0] * 24 for _ in range(7)]
    for created, total, ch in s.execute(q):
        ch = ch or WEB
        if channel == "web" and ch != WEB:
            continue
        if channel == "mp" and not is_marketplace(ch):
            continue
        rev[created.weekday()][created.hour] += total or 0
        cnt[created.weekday()][created.hour] += 1
    # haftanın her günü dönemde kaç kez geçti (ortalama için)
    occ = [0] * 7
    d = start
    while d <= end:
        occ[d.weekday()] += 1
        d += timedelta(days=1)
    avg = [[(rev[w][h] / occ[w]) if occ[w] else 0 for h in range(24)] for w in range(7)]
    mx = max((v for row in avg for v in row), default=0) or 1
    cells = [[{"rev": avg[w][h], "cnt": (cnt[w][h] / occ[w]) if occ[w] else 0, "lvl": avg[w][h] / mx} for h in range(24)] for w in range(7)]
    by_hour = [sum(avg[w][h] for w in range(7)) / 7 for h in range(24)]
    by_day = [sum(avg[w]) for w in range(7)]
    flat = sorted(((avg[w][h], w, h) for w in range(7) for h in range(24)), reverse=True)
    best = [{"day": DAYS_TR[w], "hour": h, "rev": v} for v, w, h in flat[:5] if v > 0]
    quiet = [by_hour[h] < (max(by_hour) or 1) * 0.15 for h in range(24)]
    # en uzun kesintisiz sessiz aralık (gece yarısını geçebilir)
    best_run, run = (0, 0), None
    for i in range(48):
        h = i % 24
        if quiet[h]:
            run = (run[0], i) if run else (i, i)
            if run[1] - run[0] > best_run[1] - best_run[0] and run[1] - run[0] < 24:
                best_run = run
        else:
            run = None
    quiet_hours = [(best_run[0] + k) % 24 for k in range(best_run[1] - best_run[0] + 1)] if any(quiet) else []
    if len(quiet_hours) < 2 or len(quiet_hours) >= 20:
        quiet_hours = []
    return {"cells": cells, "days": DAYS_TR, "by_hour": by_hour, "by_day": by_day, "best": best,
            "best_day": DAYS_TR[by_day.index(max(by_day))] if any(by_day) else None,
            "worst_day": DAYS_TR[by_day.index(min(by_day))] if any(by_day) else None,
            "quiet_hours": quiet_hours, "total_orders": sum(map(sum, cnt))}


# ---------- asorti (seri) bazında sipariş / üretim ----------
LETTER_ORDER = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "2XL", "3XL", "XXXL", "4XL", "5XL", "6XL", "7XL", "8XL"]


def _size_key(sz: str):
    t = (sz or "").strip().upper()
    num = t.replace(",", ".")
    try:
        return (0, float(num.split("-")[0].split("/")[0]), t)
    except ValueError:
        pass
    if t in LETTER_ORDER:
        return (1, LETTER_ORDER.index(t), t)
    return (2, 0, t)


def _split_variant(label: str) -> tuple[str, str]:
    """'Siyah / 42' → ('Siyah', '42'); '42' → ('', '42'). Beden her zaman en sonda."""
    parts = [p.strip() for p in (label or "").split(" / ")]
    if len(parts) > 1:
        return " / ".join(parts[:-1]), parts[-1]
    return "", parts[0] if parts else ""


def _ratio(shares: list[float], n: int) -> list[int]:
    """Payları toplamı n olan tam sayılara çevirir (en büyük kalan yöntemi). Payı 0 olan beden 0 alır."""
    k = len(shares)
    if not k or n <= 0:
        return [0] * k
    tot = sum(shares) or 1
    raw = [s / tot * n for s in shares]
    fl = [int(r) for r in raw]
    left = n - sum(fl)
    order = sorted(range(k), key=lambda i: -(raw[i] - fl[i]))
    for i in order[:left]:
        fl[i] += 1
    return fl


def reorder_assort(s, firm_id: int, lead: int = 14, cover: int = 30, window: int = 30, series: int = 0,
                   min_qty: int = 0) -> dict:
    """Model + renk bazında asorti önerisi. series=0 → seri büyüklüğü satılan beden sayısının 2 katı (otomatik)."""
    t = _today()
    a = analysis.product_analysis(s, firm_id, t - timedelta(days=window - 1), t)
    groups = defaultdict(list)
    for v in a["variants"]:
        if v["stock"] is None:
            continue
        color, size = _split_variant(v["variant"])
        groups[(v["pkey"], color)].append({**v, "size": size or "—"})
    out = []
    for (pkey, color), vs in groups.items():
        vs = [v for v in vs if v["qty"] > 0]
        if not vs:
            continue
        vs.sort(key=lambda v: _size_key(v["size"]))
        total_sold = sum(v["qty"] for v in vs)
        needs = []
        for v in vs:
            daily = v["qty"] / window
            needs.append(max(daily * (lead + cover) - max(v["stock"], 0), 0))
        need_total = sum(needs)
        if need_total < 1:
            continue
        # Asorti bu siparişin ihtiyacına göre kurulur: stoğu yeten beden 0 alır, eksik olan beden payı kadar
        k_need = sum(1 for x in needs if x >= 0.5)
        n = series or min(max(k_need * 2, 6), 36)
        ratio = _ratio(needs, n)
        rows, urgent = [], False
        for v, r, need in zip(vs, ratio, needs):
            daily = v["qty"] / window
            days_left = (v["stock"] / daily) if v["stock"] > 0 else 0
            urgent = urgent or days_left <= lead
            rows.append({"size": v["size"], "sku": v["sku"], "sold": v["qty"], "daily": daily, "stock": v["stock"],
                         "days_left": days_left, "need": need, "ratio": r, "share": v["qty"] / total_sold * 100})
        n_series = math.ceil(need_total / n)
        if min_qty:
            n_series = max(n_series, math.ceil(min_qty / n))
        for r in rows:
            r["make"] = r["ratio"] * n_series
            after = max(r["stock"], 0) + r["make"]
            r["days_after"] = after / r["daily"] if r["daily"] else None
            r["gap"] = r["make"] - max(r["need"], 0)        # + fazla, − eksik
        min_days = min((r["days_left"] for r in rows), default=0)
        short = [r["size"] for r in rows if r["need"] >= max(5, n_series * 0.5) and r["make"] < r["need"] * 0.6]
        out.append({"product": vs[0]["product"], "color": color, "rows": rows, "series_size": n, "short": short,
                    "ratio_text": " · ".join(f"{r['size']}:{r['ratio']}" for r in rows if r["ratio"]),
                    "series": n_series, "total": n_series * n, "need": need_total, "sold": total_sold,
                    "urgent": urgent, "min_days": min_days,
                    "value": sum(r["make"] * (vv.get("price") or 0) for r, vv in zip(rows, vs))})
    out.sort(key=lambda g: (not g["urgent"], g["min_days"], -g["sold"]))
    return {"groups": out, "lead": lead, "cover": cover, "window": window, "series": series, "min_qty": min_qty,
            "has_stock": a["has_stock"], "total_series": sum(g["series"] for g in out),
            "total_qty": sum(g["total"] for g in out), "urgent": sum(g["urgent"] for g in out),
            "total_value": sum(g["value"] for g in out)}
