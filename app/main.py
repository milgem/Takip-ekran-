"""Danışmanlık Paneli — tüm müşteri firmaların e-ticaret ve reklam verileri tek ekranda."""
import hmac
import logging
import os
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta

import httpx
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from . import ai, analysis, config, deep, metrics, sync
from .connectors import REGISTRY, validate
from .connectors.base import ConnectorError
from .db import AdDaily, Firm, Integration, Order, SessionLocal, init_db
from .secure import decrypt, encrypt, mask

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# httpx her isteğin tam adresini loglar; Meta token'ı adreste olduğu için bu loglar kapatılır
for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)
config.check()
init_db()

BASE = os.path.dirname(__file__)
app = FastAPI(title="Panel", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=os.path.join(BASE, "static")), name="static")
tpl = Jinja2Templates(directory=os.path.join(BASE, "templates"))


# ---------- biçimlendirme ----------
def tl(v, digits=0):
    if v is None:
        return "–"
    s = f"{v:,.{digits}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"₺{s}"


def num(v, digits=0):
    if v is None:
        return "–"
    return f"{v:,.{digits}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def pct(v):
    if v is None:
        return ""
    return f"{'+' if v > 0 else ''}{v:.0f}%".replace(".", ",")


def ago(dt):
    if not dt:
        return "hiç"
    m = int((datetime.utcnow() - dt).total_seconds() // 60)
    if m < 1:
        return "az önce"
    if m < 60:
        return f"{m} dk önce"
    if m < 1440:
        return f"{m // 60} sa önce"
    return f"{m // 1440} gün önce"


def ai_lines(text):
    """'- ' ile başlayan satırları madde listesine çevirir (HTML kaçışlı)."""
    out = []
    for ln in (text or "").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        out.append(ln[2:].strip() if ln.startswith(("- ", "• ", "* ")) else ln)
    return out


def short(text, n=46):
    """Uyarı metninden kısa etiket: ilk cümle parçası."""
    t = (text or "").strip()
    for sep in (". ", "; ", ": ", " — "):
        i = t.find(sep)
        if 0 < i < n + 20:
            t = t[:i]
            break
    return t if len(t) <= n else t[:n - 1].rsplit(" ", 1)[0] + "…"


def initials(name):
    """Logo yoksa gösterilen kısaltma: 'SSA Store' → SSA, '360 Koruma' → 360, 'Sabri Özel' → SÖ."""
    words = [w for w in (name or "").split() if w]
    if not words:
        return "?"
    w0 = words[0]
    if w0[0].isdigit() or (w0.isupper() and len(w0) <= 4):
        return w0[:3]
    return "".join(w[0] for w in words[:2]).upper()


tpl.env.filters.update(tl=tl, num=num, pct=pct, ago=ago, ai_lines=ai_lines, initials=initials, short=short)
tpl.env.globals.update(REGISTRY=REGISTRY, PRESETS=metrics.PRESETS)


# ---------- giriş ----------
_fails: dict[str, list[float]] = defaultdict(list)


def authed(request: Request) -> bool:
    return request.session.get("u") == config.ADMIN_USER


async def guard(request: Request, call_next):
    p = request.url.path
    if p.startswith("/static") or p in ("/giris", "/saglik"):
        return await call_next(request)
    if not authed(request):
        if request.method != "GET":
            return JSONResponse({"ok": False, "message": "Oturum süresi doldu, tekrar giriş yapın."}, 401)
        return RedirectResponse("/giris", 303)
    return await call_next(request)


# Sıra önemli: en son eklenen en dışta çalışır -> önce oturum açılır, sonra kontrol edilir
app.add_middleware(BaseHTTPMiddleware, dispatch=guard)
app.add_middleware(SessionMiddleware, secret_key=config.SECRET_KEY, session_cookie="panel_oturum",
                   max_age=60 * 60 * 24 * 30, same_site="lax", https_only=config.COOKIE_SECURE)


@app.get("/saglik")
def health():
    return {"ok": True}


@app.get("/giris", response_class=HTMLResponse)
def login_page(request: Request):
    return tpl.TemplateResponse(request, "login.html", {"error": None})


@app.post("/giris")
def login(request: Request, kullanici: str = Form(...), sifre: str = Form(...)):
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0]
    now = time.time()
    _fails[ip] = [t for t in _fails[ip] if now - t < 900]
    if len(_fails[ip]) >= 8:
        return tpl.TemplateResponse(request, "login.html", {"error": "Çok fazla deneme. 15 dk sonra tekrar deneyin."},
                                    status_code=429)
    if hmac.compare_digest(kullanici, config.ADMIN_USER) and hmac.compare_digest(sifre, config.ADMIN_PASSWORD):
        request.session["u"] = config.ADMIN_USER
        _fails.pop(ip, None)
        return RedirectResponse("/", 303)
    _fails[ip].append(now)
    time.sleep(1)
    return tpl.TemplateResponse(request, "login.html", {"error": "Kullanıcı adı ya da şifre hatalı."}, status_code=401)


@app.get("/cikis")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/giris", 303)


# ---------- panel ----------
def _firms(s):
    return list(s.scalars(select(Firm).options(selectinload(Firm.integrations)).order_by(Firm.name)))


def _last_sync(firms):
    times = [i.last_sync_at for f in firms for i in f.integrations if i.last_sync_at]
    return max(times) if times else None


@app.get("/", response_class=HTMLResponse)
def overview(request: Request, p: str = "bugun", bas: str | None = None, bit: str | None = None):
    start, end, preset = metrics.resolve_range(p, bas, bit)
    with SessionLocal() as s:
        firms = _firms(s)
        cards = [metrics.firm_card(s, f, start, end, preset) for f in firms]
        tot = metrics.Summary()
        for c in cards:
            for k in ("revenue", "orders", "spend", "leads", "conv_value", "web_revenue", "web_orders", "mp_revenue", "mp_orders"):
                setattr(tot, k, getattr(tot, k) + getattr(c["cur"], k))
            c["n_red"] = sum(a.level == "red" for a in c["alerts"])
            c["n_amber"] = sum(a.level == "amber" for a in c["alerts"])
        prev = metrics.Summary()
        for c in cards:
            for k in ("revenue", "spend", "conv_value", "web_revenue"):
                setattr(prev, k, getattr(prev, k) + getattr(c["prev"], k))
        # dikkat isteyenler üstte: önce acil, sonra takip, sonra ciro
        cards.sort(key=lambda c: (-c["n_red"], -c["n_amber"], -c["cur"].revenue))
        attention = sum(1 for c in cards if c["n_red"] or c["n_amber"])
    return tpl.TemplateResponse(request, "overview.html", {
        "cards": cards, "tot": tot, "prev": prev, "start": start, "end": end, "preset": preset, "attention": attention,
        "n_red": sum(c["n_red"] for c in cards), "n_amber": sum(c["n_amber"] for c in cards),
        "rev_chg": metrics.change(tot.revenue, prev.revenue), "now": datetime.now(config.TIMEZONE),
        "last_sync": _last_sync(firms), "pending_total": sum(c["pending"] for c in cards)})


def _load_firm(s, firm_id):
    return s.scalar(select(Firm).options(selectinload(Firm.integrations)).where(Firm.id == firm_id))


def _firm_ctx(s, firm, tab, tab_path, tab_title, start, end, preset, **extra):
    kinds = {i.kind for i in firm.integrations if i.enabled}
    from .connectors import AD_KINDS, STORE_KINDS
    ctx = {"firm": firm, "tab": tab, "tab_path": tab_path, "tab_title": tab_title, "start": start, "end": end, "preset": preset,
           "all_firms": list(s.scalars(select(Firm).order_by(Firm.name))), "kinds": kinds,
           "has_store": bool(kinds & STORE_KINDS), "has_ads": bool(kinds & AD_KINDS),
           "has_mp": (s.scalar(select(func.count(Order.id)).where(Order.firm_id == firm.id, Order.channel != "Web sitesi")) or 0) > 0,
           "now": datetime.now(config.TIMEZONE)}
    ctx.update(extra)
    return ctx


def _ad_sources(s, firm, kinds, start, end):
    src = {}
    for kind in ("meta", "google_ads"):
        if kind in kinds:
            r = s.execute(select(func.sum(AdDaily.spend), func.sum(AdDaily.conversion_value), func.sum(AdDaily.conversions),
                                 func.sum(AdDaily.leads), func.sum(AdDaily.clicks)).where(
                AdDaily.firm_id == firm.id, AdDaily.source == kind, AdDaily.day >= start, AdDaily.day <= end)).one()
            sp = float(r[0] or 0)
            src[kind] = {"spend": sp, "value": float(r[1] or 0), "conv": float(r[2] or 0), "leads": float(r[3] or 0),
                         "clicks": int(r[4] or 0), "roas": (float(r[1] or 0) / sp) if sp else None}
    return src


TAB_LINK = {"siparis": ("satis?v=siparis", "Siparişlere bak"), "satis": ("satis", "Satışa bak"),
            "urunler": ("urunler", "Sipariş listesi"), "reklam": ("reklam", "Kampanyalara bak"), "ozet": ("satis", "Satışa bak")}


def _actions(firm, alerts):
    """Uyarıları 'yapılacak iş' kartlarına çevirir: başlık, açıklama, düğme."""
    out = []
    for a in alerts:
        if a.level == "green":
            continue
        txt = a.text.strip()
        cut = min([i for i in (txt.find(". "), txt.find("; ")) if i > 0] or [len(txt)])
        title, detail = txt[:cut].rstrip(".;"), txt[cut + 1:].strip()
        if "ödeme" in txt.lower() or "sanal pos" in txt.lower():
            link, label, icon = "satis#odemesiz", "İncele", "card"
        elif "bağlantı hatası" in txt.lower():
            link, label, icon = "/ayarlar", "Bağlantıyı aç", "plug"
        else:
            link, label = TAB_LINK.get(a.tab or "ozet", TAB_LINK["ozet"])
            icon = {"urunler": "box", "reklam": "ad", "siparis": "truck"}.get(a.tab, "chart")
        out.append({"level": a.level, "title": title, "detail": detail, "href": link if link.startswith("/") else f"/firma/{firm.id}/{link}",
                    "label": label, "icon": icon, "smart": a.smart})
    return out


def _headline(card, n_red, n_amber):
    from .metrics import change
    cur, prev = card["cur"], card["prev"]
    if card["has_store"]:
        rc = change(cur.revenue, prev.revenue)
        if rc is None:
            first = "Bugün henüz satış yok" if not cur.revenue else "Satışlar sürüyor"
        elif rc >= 10:
            first = f"Satışlar dünün aynı saatine göre %{rc:.0f} önde"
        elif rc <= -10:
            first = f"Satışlar dünün aynı saatine göre %{abs(rc):.0f} geride"
        else:
            first = "Satışlar dünle aynı seviyede"
    else:
        first = f"Bugün {cur.leads:.0f} lead geldi" if cur.leads else "Reklamlar çalışıyor"
    if n_red:
        return f"{first}; {n_red} konu acil ilgi istiyor"
    if n_amber:
        return f"{first}; takip edilecek {n_amber} konu var"
    return f"{first}; ilgilenmen gereken bir şey görünmüyor"


@app.get("/firma/{firm_id}", response_class=HTMLResponse)
def firm_today(request: Request, firm_id: int):
    t = datetime.now(config.TIMEZONE).date()
    with SessionLocal() as s:
        firm = _load_firm(s, firm_id)
        if not firm:
            return RedirectResponse("/", 303)
        card = metrics.firm_card(s, firm, t, t, "bugun")
        n_red = sum(a.level == "red" for a in card["alerts"])
        n_amber = sum(a.level == "amber" for a in card["alerts"])
        unpaid_today = metrics.unpaid_summary(s, firm.id, t, t) if card["has_store"] else None
        unpaid_30 = metrics.unpaid_summary(s, firm.id, t - timedelta(days=29), t) if card["has_store"] else None
        series = metrics.daily_series(s, firm.id, t - timedelta(days=13), t)
        chans = metrics.channel_breakdown(s, firm.id, t, t) if card["has_store"] else []
        ctx = _firm_ctx(s, firm, "bugun", "", "Bugün", t, t, "bugun", c=card, actions=_actions(firm, card["alerts"]),
                        good=[a for a in card["alerts"] if a.level == "green"], headline=_headline(card, n_red, n_amber),
                        unpaid_today=unpaid_today, unpaid_30=unpaid_30, series=series, chans=chans,
                        ai_note=ai.latest(s, firm.id), ai_ready=ai.ready(s))
    return tpl.TemplateResponse(request, "today.html", ctx)


@app.get("/firma/{firm_id}/satis", response_class=HTMLResponse)
def firm_sales(request: Request, firm_id: int, v: str = "ozet", p: str = "30g", bas: str | None = None, bit: str | None = None,
               k: str = ""):
    start, end, preset = metrics.resolve_range(p, bas, bit)
    v = v if v in ("ozet", "siparis", "karlilik", "saat") else "ozet"
    with SessionLocal() as s:
        firm = _load_firm(s, firm_id)
        if not firm:
            return RedirectResponse("/", 303)
        card = metrics.firm_card(s, firm, start, end, preset)
        extra = {"c": card, "v": v, "k": k}
        if v == "ozet":
            extra.update(series=metrics.daily_series(s, firm.id, start, end), chans=metrics.channel_breakdown(s, firm.id, start, end),
                         hourly=metrics.hourly_today(s, firm.id), unpaid=metrics.unpaid_summary(s, firm.id, start, end))
        elif v == "siparis":
            orders = list(s.scalars(select(Order).where(
                Order.firm_id == firm.id, Order.created_at >= datetime.combine(start, datetime.min.time()),
                Order.created_at <= datetime.combine(end, datetime.max.time())).order_by(Order.created_at.desc()).limit(3001)))
            extra.update(orders=orders[:3000], orders_capped=len(orders) > 3000,
                         pending=list(s.scalars(select(Order).where(Order.firm_id == firm.id, Order.is_pending.is_(True),
                                                                    Order.is_cancelled.is_(False)).order_by(Order.created_at).limit(100))))
        elif v == "karlilik":
            extra["d"] = deep.profitability(s, firm.id, start, end)
        else:
            extra["d"] = deep.heatmap(s, firm.id, start, end, k if k in ("web", "mp") else "")
        ctx = _firm_ctx(s, firm, "satis", "/satis", "Satış", start, end, preset, **extra)
    return tpl.TemplateResponse(request, "sales.html", ctx)


@app.get("/firma/{firm_id}/urunler", response_class=HTMLResponse)
def firm_stock(request: Request, firm_id: int, v: str = "siparis", p: str = "30g", bas: str | None = None, bit: str | None = None,
               k: str = "", g: str = "", lead: int = 14, cover: int = 30, gun: int = 60, mod: str = "", seri: int = 0, moq: int = 0):
    start, end, preset = metrics.resolve_range(p, bas, bit)
    v = v if v in ("siparis", "cok", "olu") else "siparis"
    lead, cover, gun = max(1, min(lead, 180)), max(1, min(cover, 365)), max(14, min(gun, 365))
    with SessionLocal() as s:
        firm = _load_firm(s, firm_id)
        if not firm:
            return RedirectResponse("/", 303)
        extra = {"v": v, "k": k, "g": g if g in ("marka", "kategori") else "", "lead": lead, "cover": cover, "gun": gun,
                 "mod": "beden" if mod == "beden" else "asorti", "seri": seri, "moq": moq}
        if v == "siparis":
            extra["d"] = (deep.reorder(s, firm.id, lead, cover) if mod == "beden" else
                          deep.reorder_assort(s, firm.id, lead, cover, series=max(0, min(seri, 500)), min_qty=max(0, min(moq, 100000))))
        elif v == "olu":
            extra["d"] = deep.dead_stock(s, firm.id, gun)
        elif extra["g"]:
            extra["a"] = analysis.product_analysis(s, firm.id, start, end, k if k in ("web", "mp") else "")
        else:
            extra["best"] = analysis.bestsellers_by_channel(s, firm.id, start, end, limit=50)
        ctx = _firm_ctx(s, firm, "urun", "/urunler", "Ürün ve stok", start, end, preset, **extra)
    return tpl.TemplateResponse(request, "stock.html", ctx)


@app.get("/firma/{firm_id}/reklam", response_class=HTMLResponse)
def firm_ads(request: Request, firm_id: int, v: str = "kampanya", p: str = "30g", bas: str | None = None, bit: str | None = None):
    start, end, preset = metrics.resolve_range(p, bas, bit)
    v = v if v in ("kampanya", "site") else "kampanya"
    with SessionLocal() as s:
        firm = _load_firm(s, firm_id)
        if not firm:
            return RedirectResponse("/", 303)
        card = metrics.firm_card(s, firm, start, end, preset)
        extra = {"c": card, "v": v}
        if v == "kampanya":
            extra.update(camps=metrics.campaigns(s, firm.id, start, end), src=_ad_sources(s, firm, card["kinds"], start, end))
        else:
            extra.update(a=analysis.product_analysis(s, firm.id, start, end), g="kampanya")
        ctx = _firm_ctx(s, firm, "reklam", "/reklam", "Reklam", start, end, preset, **extra)
    return tpl.TemplateResponse(request, "ads.html", ctx)


@app.get("/firma/{firm_id}/analiz")
def firm_analysis_redirect(request: Request, firm_id: int, v: str = "siparis"):
    """Eski Analiz adresleri yeni sekmelere yönlenir."""
    q = str(request.query_params)
    if v in ("karlilik", "saat"):
        return RedirectResponse(f"/firma/{firm_id}/satis?{q}", 303)
    return RedirectResponse(f"/firma/{firm_id}/urunler?{q}", 303)


@app.get("/firma/{firm_id}/analiz/excel")
def firm_analysis_excel(firm_id: int, v: str = "siparis", p: str = "30g", bas: str | None = None, bit: str | None = None,
                        lead: int = 14, cover: int = 30, gun: int = 60, mod: str = "", seri: int = 0, moq: int = 0,
                        model: str = "", renk: str = ""):
    """Analiz görünümünü Excel dosyası olarak indirir. model/renk verilirse yalnızca o ürün kartı."""
    import re
    from urllib.parse import quote
    from fastapi.responses import Response
    from . import export
    start, end, preset = metrics.resolve_range(p, bas, bit)
    lead, cover, gun = max(1, min(lead, 180)), max(1, min(cover, 365)), max(14, min(gun, 365))
    with SessionLocal() as s:
        firm = s.get(Firm, firm_id)
        if not firm:
            return RedirectResponse("/", 303)
        if v == "siparis" and mod != "beden":
            d = deep.reorder_assort(s, firm.id, lead, cover, series=max(0, min(seri, 500)), min_qty=max(0, min(moq, 100000)))
            only = [g for g in d["groups"] if g["product"] == model and g["color"] == renk] if model else None
            data, name = export.assort_xlsx(firm.name, d, only), ("siparis-" + (model[:40] + "-" + renk if model else "asorti"))
        elif v == "siparis":
            data, name = export.sizes_xlsx(firm.name, deep.reorder(s, firm.id, lead, cover)), "siparis-beden"
        elif v == "olu":
            data, name = export.dead_xlsx(firm.name, deep.dead_stock(s, firm.id, gun)), f"olu-stok-{gun}-gun"
        elif v == "karlilik":
            data, name = export.profit_xlsx(firm.name, deep.profitability(s, firm.id, start, end), start, end), "kanal-karliligi"
        else:
            return RedirectResponse(f"/firma/{firm_id}/analiz", 303)
    fname = re.sub(r"[^\w\-]+", "-", f"{firm.name}-{name}-{datetime.now():%Y%m%d}", flags=re.UNICODE).strip("-") + ".xlsx"
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"})


# ---------- firma logosu ----------
LOGO_DIR = os.path.join(os.path.dirname(config.DATABASE_URL.replace("sqlite:///", "")) or "data", "logos")
LOGO_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/svg+xml": "svg"}


def _fetch_logo(firm_id: int, url: str) -> bool:
    """Logo adresinden resmi indirip data/logos içine kaydeder."""
    import httpx
    url = (url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return False
    try:
        r = httpx.get(url, timeout=20, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 Panel"})
        r.raise_for_status()
    except Exception as e:
        logging.getLogger("panel").warning("Logo indirilemedi (%s): %s", url, e)
        return False
    data = r.content
    ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
    ext = LOGO_TYPES.get(ctype, "")
    if not ext:
        head = data[:400].lstrip()
        ext = "svg" if head.startswith((b"<svg", b"<?xml")) and b"<svg" in data[:2000] else \
              "png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "jpg" if data[:3] == b"\xff\xd8\xff" else \
              "webp" if data[8:12] == b"WEBP" else ""
    if not ext or len(data) > 6_000_000:
        return False
    os.makedirs(LOGO_DIR, exist_ok=True)
    with SessionLocal() as s:
        f = s.get(Firm, firm_id)
        if not f:
            return False
        old = f.logo
        f.logo = f"{firm_id}-{int(time.time())}.{ext}"
        with open(os.path.join(LOGO_DIR, f.logo), "wb") as fh:
            fh.write(data)
        s.commit()
    if old and "/" not in old:
        try:
            os.remove(os.path.join(LOGO_DIR, old))
        except OSError:
            pass
    return True


@app.get("/logo/{firm_id}")
def firm_logo(firm_id: int):
    from fastapi.responses import FileResponse, Response
    with SessionLocal() as s:
        f = s.get(Firm, firm_id)
        name = f.logo if f else ""
    if name.lower().startswith(("http://", "https://")):   # adres olarak girilmiş: bir kez indir
        if not _fetch_logo(firm_id, name):
            return Response(status_code=404)
        with SessionLocal() as s:
            name = s.get(Firm, firm_id).logo
    path = os.path.join(LOGO_DIR, name) if name else ""
    if not name or "/" in name or not os.path.isfile(path):
        return Response(status_code=404)
    ext = name.rsplit(".", 1)[-1]
    mt = {v: k for k, v in LOGO_TYPES.items()}.get(ext, "application/octet-stream")
    # SVG içinde betik çalışmasın
    return FileResponse(path, media_type=mt, headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
                                                      "Cache-Control": "private, max-age=86400"})


@app.post("/firma/{firm_id}/logo")
async def firm_logo_upload(firm_id: int, request: Request):
    form = await request.form()
    up = form.get("logo")
    ext = LOGO_TYPES.get(getattr(up, "content_type", ""), "")
    if not up or not ext:
        return RedirectResponse("/ayarlar?logo=tur#firma-" + str(firm_id), 303)
    data = await up.read()
    if len(data) > 1_000_000:
        return RedirectResponse("/ayarlar?logo=boyut#firma-" + str(firm_id), 303)
    os.makedirs(LOGO_DIR, exist_ok=True)
    with SessionLocal() as s:
        f = s.get(Firm, firm_id)
        if not f:
            return RedirectResponse("/ayarlar", 303)
        old = f.logo
        f.logo = f"{firm_id}-{int(time.time())}.{ext}"
        with open(os.path.join(LOGO_DIR, f.logo), "wb") as fh:
            fh.write(data)
        s.commit()
    if old and "/" not in old:
        try:
            os.remove(os.path.join(LOGO_DIR, old))
        except OSError:
            pass
    return RedirectResponse("/ayarlar#firma-" + str(firm_id), 303)


@app.post("/firma/{firm_id}/logo/adres")
def firm_logo_url(firm_id: int, url: str = Form("")):
    ok = _fetch_logo(firm_id, url)
    return RedirectResponse(("/ayarlar#firma-" if ok else "/ayarlar?logo=adres#firma-") + str(firm_id), 303)


@app.post("/firma/{firm_id}/logo/sil")
def firm_logo_delete(firm_id: int):
    with SessionLocal() as s:
        f = s.get(Firm, firm_id)
        if f and f.logo:
            try:
                if "/" not in f.logo:
                    os.remove(os.path.join(LOGO_DIR, f.logo))
            except OSError:
                pass
            f.logo = ""
            s.commit()
    return RedirectResponse("/ayarlar#firma-" + str(firm_id), 303)


# ---------- ayarlar ----------
@app.get("/ayarlar", response_class=HTMLResponse)
def settings(request: Request):
    with SessionLocal() as s:
        firms = _firms(s)
        ai_st = ai.get_settings(s)
    return tpl.TemplateResponse(request, "settings.html", {"firms": firms, "cfg": config, "ai_st": ai_st,
                                                            "ai_key_mask": mask(ai_st.get("api_key", ""))})


@app.post("/ayarlar/ai")
def ai_save(api_key: str = Form(""), model: str = Form(ai.DEFAULT_MODEL), enabled: str = Form(""), hour: int = Form(8)):
    with SessionLocal() as s:
        ai.save_settings(s, api_key, model, enabled == "1", hour)
    return RedirectResponse("/ayarlar#yapay-zeka", 303)


@app.post("/ayarlar/ai/test")
def ai_test():
    try:
        with SessionLocal() as s:
            return {"ok": True, "message": ai.test_connection(s)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": str(e)[:300]}


@app.post("/firma/{firm_id}/ai")
def ai_generate(firm_id: int):
    ok, msg = ai.generate(firm_id)
    return {"ok": ok, "message": msg}


@app.post("/firma/kaydet")
def firm_save(name: str = Form(...), color: str = Form("#4f46e5"), roas_alert: float = Form(3.0),
              note: str = Form(""), firm_id: int | None = Form(None)):
    with SessionLocal() as s:
        f = s.get(Firm, firm_id) if firm_id else Firm()
        f.name, f.color, f.roas_alert, f.note = name.strip()[:120], color[:9], roas_alert, note
        s.add(f)
        s.commit()
    return RedirectResponse("/ayarlar", 303)


@app.post("/firma/{firm_id}/sil")
def firm_delete(firm_id: int):
    with SessionLocal() as s:
        f = s.get(Firm, firm_id)
        if f:
            s.delete(f)
            s.commit()
    return RedirectResponse("/ayarlar", 303)


@app.get("/baglanti", response_class=HTMLResponse)
def integ_form(request: Request, firma: int, tur: str | None = None, id: int | None = None, mesaj: str = "", hata: str = ""):
    with SessionLocal() as s:
        firm = s.get(Firm, firma)
        integ = s.get(Integration, id) if id else None
        cfg = decrypt(integ.config_enc) if integ else {}
    kind = integ.kind if integ else tur
    if kind not in REGISTRY or not firm:
        return RedirectResponse("/ayarlar", 303)
    masked = {k: (mask(v) if any(f.name == k and f.secret for f in REGISTRY[kind].FIELDS) else v) for k, v in cfg.items()}
    return tpl.TemplateResponse(request, "integration.html", {
        "firm": firm, "kind": kind, "mod": REGISTRY[kind], "integ": integ, "values": masked,
        "mesaj": mesaj, "hata": hata, "has_client": bool(cfg.get("client_id") and cfg.get("client_secret")),
        "has_token": bool(cfg.get("access_token")), "shopify_redirect": _shopify_redirect(request)})


@app.post("/baglanti")
async def integ_save(request: Request):
    form = await request.form()
    firm_id, kind = int(form["firm_id"]), form["kind"]
    mod = REGISTRY[kind]
    with SessionLocal() as s:
        integ = s.get(Integration, int(form["id"])) if form.get("id") else None
        old = decrypt(integ.config_enc) if integ else {}
        cfg = {}
        for f in mod.FIELDS:
            v = str(form.get(f.name, "")).strip()
            if f.secret and integ and v == mask(old.get(f.name, "")):
                v = old.get(f.name, "")     # maskeli değer değiştirilmedi
            cfg[f.name] = v
        if not integ:
            integ = Integration(firm_id=firm_id, kind=kind)
        integ.config_enc = encrypt(cfg)
        integ.label = str(form.get("label", ""))[:120]
        integ.enabled = form.get("enabled") == "on" or not form.get("id")
        s.add(integ)
        s.commit()
        iid = integ.id
    threading.Thread(target=sync.sync_integration, args=(iid,), daemon=True).start()
    return RedirectResponse("/ayarlar", 303)


# ---------- Shopify OAuth ("Shopify ile bağlan") ----------
SHOPIFY_SCOPES = "read_orders,read_products,read_inventory,read_customers"


def _shopify_redirect(request: Request) -> str:
    return str(request.base_url).rstrip("/") + "/shopify/geri"


@app.get("/shopify/baglan/{iid}")
def shopify_connect(request: Request, iid: int):
    import secrets
    from urllib.parse import urlencode
    with SessionLocal() as s:
        integ = s.get(Integration, iid)
        if not integ or integ.kind != "shopify":
            return RedirectResponse("/ayarlar", 303)
        cfg = decrypt(integ.config_enc)
    shop = cfg.get("shop", "").strip().lower().replace("https://", "").replace("http://", "").strip("/")
    if not shop.endswith(".myshopify.com") or not cfg.get("client_id") or not cfg.get("client_secret"):
        return RedirectResponse(f"/baglanti?firma={integ.firm_id}&id={iid}&hata=eksik", 303)
    state = secrets.token_urlsafe(24)
    request.session["shopify_oauth"] = {"state": state, "iid": iid, "shop": shop}
    q = urlencode({"client_id": cfg["client_id"].strip(), "scope": SHOPIFY_SCOPES,
                   "redirect_uri": _shopify_redirect(request), "state": state})
    return RedirectResponse(f"https://{shop}/admin/oauth/authorize?{q}", 303)


@app.get("/shopify/geri")
def shopify_callback(request: Request):
    import hashlib
    params = dict(request.query_params)
    pending = request.session.pop("shopify_oauth", None) or {}
    iid = pending.get("iid")
    def back(msg):
        with SessionLocal() as s:
            integ = s.get(Integration, iid) if iid else None
        if not integ:
            return RedirectResponse("/ayarlar", 303)
        from urllib.parse import quote
        return RedirectResponse(f"/baglanti?firma={integ.firm_id}&id={iid}&mesaj={quote(msg)}", 303)
    if not iid or params.get("state") != pending.get("state") or params.get("shop") != pending.get("shop"):
        return back("Bağlantı doğrulanamadı (oturum ya da mağaza eşleşmedi). Tekrar dene.")
    with SessionLocal() as s:
        integ = s.get(Integration, iid)
        cfg = decrypt(integ.config_enc)
    secret = cfg.get("client_secret", "").strip()
    msg_items = sorted((k, v) for k, v in params.items() if k not in ("hmac", "signature"))
    digest = hmac.new(secret.encode(), "&".join(f"{k}={v}" for k, v in msg_items).encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(digest, params.get("hmac", "")):
        return back("Shopify'dan gelen yanıt doğrulanamadı (imza hatalı). Client Secret'ı kontrol et.")
    try:
        r = httpx.post(f"https://{pending['shop']}/admin/oauth/access_token", timeout=30,
                       json={"client_id": cfg["client_id"].strip(), "client_secret": secret, "code": params.get("code", "")})
        r.raise_for_status()
        token = r.json()["access_token"]
    except Exception as e:  # noqa: BLE001
        return back(f"Shopify token alınamadı: {str(e)[:200]}")
    with SessionLocal() as s:
        integ = s.get(Integration, iid)
        cfg = decrypt(integ.config_enc)
        cfg["access_token"] = token
        integ.config_enc = encrypt(cfg)
        integ.last_ok, integ.last_message = None, "Shopify bağlandı, veriler çekiliyor…"
        s.commit()
    threading.Thread(target=sync.sync_integration, args=(iid,), daemon=True).start()
    return back("Shopify bağlandı. Veriler arka planda çekiliyor.")


@app.post("/baglanti/{iid}/test")
def integ_test(iid: int):
    """Test en fazla 75 sn sürer; platform yanıt vermezse takıldığı adımı söyler."""
    import concurrent.futures
    with SessionLocal() as s:
        integ = s.get(Integration, iid)
        if not integ:
            return JSONResponse({"ok": False, "message": "Bulunamadı"})
        kind, cfg = integ.kind, decrypt(integ.config_enc)
    progress = {"step": "başlıyor"}
    cfg["_progress"] = progress
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    fut = ex.submit(_integ_test_run, kind, cfg)
    try:
        return fut.result(timeout=75)
    except concurrent.futures.TimeoutError:
        return {"ok": False, "message": f"75 saniyede yanıt gelmedi. Takıldığı adım: {progress['step']}. "
                                        "Alan adını, Ticimax web servis yetkisinin aktif olduğunu ve "
                                        "Ticimax'ta web servis için IP kısıtlaması olup olmadığını kontrol et."}
    finally:
        ex.shutdown(wait=False)


def _integ_test_run(kind, cfg):
    try:
        validate(kind, cfg)
        return {"ok": True, "message": REGISTRY[kind].test(cfg)}
    except ConnectorError as e:
        return {"ok": False, "message": str(e)}
    except httpx.HTTPError as e:
        return {"ok": False, "message": f"Platforma bağlanılamadı: {str(e)[:200]}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"{type(e).__name__}: {str(e)[:300]}"}


@app.post("/baglanti/{iid}/senkron")
def integ_sync(iid: int):
    ok, msg = sync.sync_integration(iid)
    return {"ok": ok, "message": msg}


@app.post("/baglanti/{iid}/sifirla")
def integ_reset(iid: int):
    """Geçmişi baştan çek (ilk kurulumdaki gibi)."""
    with SessionLocal() as s:
        integ = s.get(Integration, iid)
        if integ:
            integ.backfilled = False
            s.commit()
    threading.Thread(target=sync.sync_integration, args=(iid,), daemon=True).start()
    return RedirectResponse("/ayarlar", 303)


@app.post("/baglanti/{iid}/sil")
def integ_delete(iid: int):
    with SessionLocal() as s:
        integ = s.get(Integration, iid)
        if integ:
            s.delete(integ)
            s.commit()
    return RedirectResponse("/ayarlar", 303)


@app.post("/senkron")
def sync_now():
    threading.Thread(target=sync.sync_all, daemon=True).start()
    return {"ok": True, "message": "Tüm bağlantılar arka planda güncelleniyor…"}


@app.on_event("startup")
def _start():
    if os.environ.get("DISABLE_SCHEDULER") != "1":
        sync.start_scheduler()
