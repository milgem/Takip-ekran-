"""Ürün / beden bazlı satış analizi: çok satanlar, stoğu tükenen ve tükenmek üzere olan bedenler."""
from collections import defaultdict
from datetime import date, datetime

from sqlalchemy import func, select

from .channels import WEB, is_marketplace
from .db import Integration, Order, OrderItem, VariantStock

LOW_DAYS = 14          # bu kadar günden az yetecek stok "tükenmek üzere" sayılır


def _dt(d: date, end=False):
    return datetime.combine(d, datetime.max.time() if end else datetime.min.time())


def _norm(v) -> str:
    return str(v or "").strip().lower()


def product_analysis(s, firm_id: int, start: date, end: date, channel: str = "") -> dict:
    days = (end - start).days + 1
    q = select(OrderItem, Order.created_at, Order.channel, Order.id).join(Order, Order.id == OrderItem.order_id).where(
        OrderItem.firm_id == firm_id, Order.is_cancelled.is_(False),
        Order.created_at >= _dt(start), Order.created_at <= _dt(end, True))
    rows = s.execute(q).all()
    if channel == "web":
        rows = [r for r in rows if (r[2] or WEB) == WEB]
    elif channel == "mp":
        rows = [r for r in rows if is_marketplace(r[2] or WEB)]

    # stok indeksleri (bağlantı bazında: varyasyon ID → stok kodu → barkod)
    stocks = list(s.scalars(select(VariantStock).where(VariantStock.firm_id == firm_id)))
    by_vid, by_sku, by_bar = {}, {}, {}
    for v in stocks:
        if v.variant_id:
            by_vid[(v.integration_id, _norm(v.variant_id))] = v
        if v.sku:
            by_sku.setdefault((v.integration_id, _norm(v.sku)), v)
        if v.barcode:
            by_bar.setdefault((v.integration_id, _norm(v.barcode)), v)
    stock_info = s.execute(select(func.max(Integration.stock_synced_at), func.count(Integration.id)).where(
        Integration.firm_id == firm_id, Integration.stock_synced_at.is_not(None))).one()

    def match(it):
        return (by_vid.get((it.integration_id, _norm(it.variant_id))) if it.variant_id else None) \
            or (by_sku.get((it.integration_id, _norm(it.sku))) if it.sku else None) \
            or (by_bar.get((it.integration_id, _norm(it.barcode))) if it.barcode else None)

    variants: dict = {}
    products: dict = {}
    camps: dict = {}
    for it, created, ch, oid in rows:
        st = match(it)
        if it.campaign_id or it.campaign or it.discount:
            ck = it.campaign_id or it.campaign or "indirim"
            cp = camps.get(ck)
            if not cp:
                cp = camps[ck] = {"id": it.campaign_id, "name": it.campaign, "qty": 0.0, "revenue": 0.0, "discount": 0.0,
                                  "orders": set(), "products": defaultdict(lambda: [0.0, 0.0]), "first": created, "last": created}
            cp["qty"] += it.quantity
            cp["revenue"] += it.amount
            cp["discount"] += it.discount or 0
            cp["orders"].add(oid)
            cp["first"], cp["last"] = min(cp["first"], created), max(cp["last"], created)
        pkey = (it.integration_id, st.product_id if st else (it.product_id or _norm(it.name)))
        vkey = (it.integration_id, st.variant_id if st else (it.variant_id or it.sku or _norm(it.name) + "|" + _norm(it.variant)))
        pname = (st.product_name if st and st.product_name else it.name) or it.sku or "Adsız ürün"
        vlabel = it.variant or (st.variant if st else "") or ""
        v = variants.get(vkey)
        if not v:
            v = variants[vkey] = {"product": pname, "variant": vlabel, "sku": it.sku or (st.sku if st else ""),
                                  "vid": st.variant_id if st else None, "price": st.price if st else None,
                                  "qty": 0.0, "revenue": 0.0, "orders": set(), "last": created,
                                  "stock": st.stock if st else None, "active": st.active if st else None, "pkey": pkey,
                                  "by_ch": defaultdict(lambda: [0.0, 0.0, set()])}
        vc = v["by_ch"][ch or WEB]
        vc[0] += it.quantity
        vc[1] += it.amount
        vc[2].add(oid)
        v["qty"] += it.quantity
        v["revenue"] += it.amount
        v["orders"].add(oid)
        v["last"] = max(v["last"], created)
        if it.campaign_id or it.campaign or it.discount:
            pr = camps[it.campaign_id or it.campaign or "indirim"]["products"][pname]
            pr[0] += it.quantity
            pr[1] += it.amount
        p = products.get(pkey)
        if not p:
            p = products[pkey] = {"product": pname, "qty": 0.0, "revenue": 0.0, "orders": set(), "sizes": defaultdict(float),
                                  "brand": (it.brand or (st.brand if st else "") or "").strip(),
                                  "category": (it.category or (st.category if st else "") or "").strip(),
                                  "by_ch": defaultdict(lambda: [0.0, 0.0, set()])}
        bc = p["by_ch"][ch or WEB]
        bc[0] += it.quantity
        bc[1] += it.amount
        bc[2].add(oid)
        p["qty"] += it.quantity
        p["revenue"] += it.amount
        p["orders"].add(oid)
        if vlabel:
            p["sizes"][vlabel] += it.quantity

    # ürünün toplam stoğu ve tükenen beden sayısı (stok tablosundaki bütün bedenler)
    prod_stock = defaultdict(lambda: {"stock": 0.0, "known": False, "sizes": 0, "out": 0, "out_names": []})
    for v in stocks:
        ps = prod_stock[(v.integration_id, v.product_id)]
        ps["known"] = True
        ps["stock"] += max(v.stock, 0)
        ps["sizes"] += 1
        if v.stock <= 0:
            ps["out"] += 1
            if v.variant:
                ps["out_names"].append(v.variant)

    vlist = []
    for v in variants.values():
        daily = v["qty"] / days if days else 0
        v["orders"] = len(v["orders"])
        v["daily"] = daily
        v["days_left"] = (v["stock"] / daily) if (v["stock"] is not None and v["stock"] > 0 and daily) else None
        vlist.append(v)
    vlist.sort(key=lambda x: -x["qty"])

    # ürün → bedenleri (satılanlar + stokta olup bu dönemde satılmayanlar)
    by_prod = defaultdict(list)
    for v in vlist:
        by_prod[v["pkey"]].append(v)
    sold_vids = {(v["pkey"][0], v["vid"]) for v in vlist if v.get("vid")}
    for sv in stocks:
        key = (sv.integration_id, sv.product_id)
        if key in products and (sv.integration_id, sv.variant_id) not in sold_vids:
            by_prod[key].append({"product": sv.product_name, "variant": sv.variant, "sku": sv.sku, "vid": sv.variant_id,
                                 "by_ch": {},
                                 "qty": 0.0, "revenue": 0.0, "orders": 0, "last": None, "stock": sv.stock,
                                 "active": sv.active, "daily": 0.0, "days_left": None, "pkey": key})

    plist = []
    total_rev = sum(p["revenue"] for p in products.values()) or 0
    for key, p in products.items():
        ps = prod_stock.get(key)
        pv = sorted(by_prod.get(key, []), key=lambda x: (-x["qty"], str(x["variant"] or x["sku"])))
        for v in pv:
            v["pshare"] = v["qty"] / p["qty"] * 100 if p["qty"] else 0
        sizes = sorted(p["sizes"].items(), key=lambda x: -x[1])
        plist.append({
            "product": p["product"], "qty": p["qty"], "revenue": p["revenue"], "orders": len(p["orders"]),
            "share": p["revenue"] / total_rev * 100 if total_rev else 0,
            "sizes": sizes, "stock": ps["stock"] if ps and ps["known"] else None,
            "size_count": ps["sizes"] if ps else 0, "out_count": ps["out"] if ps else 0,
            "out_names": ps["out_names"] if ps else [], "variants": pv,
            "daily": p["qty"] / days if days else 0,
            "by_ch": {k: {"qty": v[0], "revenue": v[1], "orders": len(v[2])} for k, v in p["by_ch"].items()},
            "brand": p["brand"], "category": p["category"], "order_ids": p["orders"], "pkey": key,
        })
    for p in plist:   # stok kaç gün yeter: tüm kanalların toplam satış hızıyla (stok ortak)
        p["days_left"] = (p["stock"] / p["daily"]) if (p["stock"] and p["stock"] > 0 and p["daily"]) else None
    plist.sort(key=lambda x: -x["qty"])

    # marka ve kategori grupları
    def groups(field):
        g = {}
        for p in plist:
            name = p[field] or "Belirtilmemiş"
            x = g.get(name)
            if not x:
                x = g[name] = {"name": name, "qty": 0.0, "revenue": 0.0, "order_ids": set(), "products": [], "stock": 0.0,
                               "stock_known": False, "out": 0, "sizes": 0}
            x["qty"] += p["qty"]
            x["revenue"] += p["revenue"]
            x["order_ids"] |= p["order_ids"]
            x["products"].append(p)
            if p["stock"] is not None:
                x["stock"] += p["stock"]
                x["stock_known"] = True
            x["out"] += p["out_count"]
            x["sizes"] += p["size_count"]
        out = []
        for x in g.values():
            out.append({"name": x["name"], "qty": x["qty"], "revenue": x["revenue"], "orders": len(x["order_ids"]),
                        "share": x["revenue"] / total_rev * 100 if total_rev else 0, "product_count": len(x["products"]),
                        "stock": x["stock"] if x["stock_known"] else None, "out": x["out"], "sizes": x["sizes"],
                        "daily": x["qty"] / days if days else 0,
                        "days_left": (x["stock"] / (x["qty"] / days)) if (x["stock_known"] and x["stock"] > 0 and x["qty"]) else None,
                        "products": sorted(x["products"], key=lambda p: -p["qty"])[:15], "missing": x["name"] == "Belirtilmemiş"})
        out.sort(key=lambda x: -x["revenue"])
        return out
    brands, categories = groups("brand"), groups("category")
    clist = []
    for c in camps.values():
        top = sorted(c["products"].items(), key=lambda x: -x[1][1])
        clist.append({"id": c["id"], "name": c["name"] or (f"Kampanya #{c['id']}" if c["id"] else "İsimsiz indirim"),
                      "qty": c["qty"], "revenue": c["revenue"], "discount": c["discount"], "orders": len(c["orders"]),
                      "aov": c["revenue"] / len(c["orders"]) if c["orders"] else 0,
                      "share": c["revenue"] / total_rev * 100 if total_rev else 0,
                      "discount_rate": c["discount"] / (c["revenue"] + c["discount"]) * 100 if (c["revenue"] + c["discount"]) else 0,
                      "first": c["first"], "last": c["last"], "active_recently": (datetime.now() - c["last"]).days < 2,
                      "products": [{"product": n, "qty": v[0], "revenue": v[1]} for n, v in top[:15]],
                      "product_count": len(top)})
    clist.sort(key=lambda x: -x["revenue"])

    oos = [v for v in vlist if v["stock"] is not None and v["stock"] <= 0]
    low = [v for v in vlist if v["days_left"] is not None and v["days_left"] <= LOW_DAYS]
    low.sort(key=lambda x: x["days_left"])
    return {
        "days": days, "products": plist, "variants": vlist, "oos": oos, "low": low, "low_days": LOW_DAYS,
        "qty": sum(v["qty"] for v in vlist), "revenue": total_rev, "has_items": bool(rows),
        "has_stock": bool(stocks), "stock_at": stock_info[0], "matched": sum(1 for v in vlist if v["stock"] is not None),
        "brands": brands, "categories": categories, "campaigns": clist,
        "campaign_revenue": sum(c["revenue"] for c in clist), "campaign_discount": sum(c["discount"] for c in clist),
    }


def bestsellers_by_channel(s, firm_id: int, start: date, end: date, limit: int = 10) -> dict:
    """Her satış kanalı için ayrı çok satanlar listesi. Kalan stok ve 'kaç gün yeter' ürünün tüm kanallardaki
    toplam satış hızına göre hesaplanır, çünkü stok kanallar arasında ortaktır."""
    a = product_analysis(s, firm_id, start, end)
    if not a["has_items"]:
        return {"channels": [], "has_stock": a["has_stock"], "days": a["days"]}
    totals = defaultdict(lambda: [0.0, 0.0])
    for p in a["products"]:
        for ch, v in p["by_ch"].items():
            totals[ch][0] += v["qty"]
            totals[ch][1] += v["revenue"]

    def sizes(p, ch):
        out = []
        for v in p["variants"]:
            if ch is None:
                q, r, o = v["qty"], v["revenue"], v["orders"]
            else:
                b = v["by_ch"].get(ch)
                q, r, o = (b[0], b[1], len(b[2])) if b else (0.0, 0.0, 0)
            out.append({"variant": v["variant"], "sku": v["sku"], "qty": q, "revenue": r, "orders": o,
                        "stock": v["stock"], "days_left": v["days_left"], "daily_all": v["daily"]})
        tot = sum(x["qty"] for x in out) or 0
        for x in out:
            x["pshare"] = x["qty"] / tot * 100 if tot else 0
        out.sort(key=lambda x: (-x["qty"], str(x["variant"] or x["sku"])))
        return out

    def row(p, qty, rev, orders, ch_rev, ch=None):
        return {"product": p["product"], "qty": qty, "revenue": rev, "orders": orders, "sizes": sizes(p, ch),
                "share": rev / ch_rev * 100 if ch_rev else 0, "stock": p["stock"], "days_left": p["days_left"],
                "daily_all": p["daily"], "out_count": p["out_count"], "size_count": p["size_count"],
                "out_names": p["out_names"]}
    chans = [{"channel": "Tüm kanallar", "key": "all", "qty": a["qty"], "revenue": a["revenue"],
              "products": [row(p, p["qty"], p["revenue"], p["orders"], a["revenue"]) for p in a["products"][:limit]]}]
    for i, (ch, (q, r)) in enumerate(sorted(totals.items(), key=lambda x: -x[1][1])):
        items = [(p, p["by_ch"][ch]) for p in a["products"] if ch in p["by_ch"]]
        items.sort(key=lambda x: (-x[1]["qty"], -x[1]["revenue"]))
        chans.append({"channel": ch, "key": f"c{i}", "qty": q, "revenue": r, "marketplace": is_marketplace(ch),
                      "products": [row(p, v["qty"], v["revenue"], v["orders"], r, ch) for p, v in items[:limit]]})
    return {"channels": chans, "has_stock": a["has_stock"], "days": a["days"]}
