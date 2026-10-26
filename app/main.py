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

from . import ai, analysis, config, metrics, sync
from .connectors import REGISTRY, validate
from .connectors.base import ConnectorError
from .db import AdDaily, Firm, Integration, Order, SessionLocal, init_db
from .secure import decrypt, encrypt, mask

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
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


tpl.env.filters.update(tl=tl, num=num, pct=pct, ago=ago, ai_lines=ai_lines)
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
        alerts = [(c["firm"], a) for c in cards for a in c["alerts"]]
        alerts.sort(key=lambda x: x[1].level != "red")
    return tpl.TemplateResponse(request, "overview.html", {
        "cards": cards, "tot": tot, "alerts": alerts, "start": start, "end": end, "preset": preset,
        "last_sync": _last_sync(firms), "pending_total": sum(c["pending"] for c in cards)})


@app.get("/firma/{firm_id}", response_class=HTMLResponse)
def firm_detail(request: Request, firm_id: int, p: str = "30g", bas: str | None = None, bit: str | None = None):
    start, end, preset = metrics.resolve_range(p, bas, bit)
    with SessionLocal() as s:
        firm = s.scalar(select(Firm).options(selectinload(Firm.integrations)).where(Firm.id == firm_id))
        if not firm:
            return RedirectResponse("/", 303)
        card = metrics.firm_card(s, firm, start, end, preset)
        series = metrics.daily_series(s, firm.id, start, end)
        chans = metrics.channel_breakdown(s, firm.id, start, end) if card["has_store"] else []
        hourly = metrics.hourly_today(s, firm.id) if card["has_store"] else None
        best = analysis.bestsellers_by_channel(s, firm.id, start, end) if card["has_store"] else None
        orders = list(s.scalars(select(Order).where(
            Order.firm_id == firm.id, Order.created_at >= datetime.combine(start, datetime.min.time()),
            Order.created_at <= datetime.combine(end, datetime.max.time())).order_by(Order.created_at.desc()).limit(3001)))
        orders_capped = len(orders) > 3000
        orders = orders[:3000]
        # ödeme statüsü yüzünden ciroya girmeyen siparişler ayrı listede gösterilir
        unpaid = [o for o in orders if o.is_cancelled and o.payment_status]
        orders = [o for o in orders if not (o.is_cancelled and o.payment_status)]
        pending = list(s.scalars(select(Order).where(Order.firm_id == firm.id, Order.is_pending.is_(True),
                                                     Order.is_cancelled.is_(False)).order_by(Order.created_at).limit(100)))
        camps = metrics.campaigns(s, firm.id, start, end) if card["has_ads"] else []
        ai_note, ai_ready = ai.latest(s, firm.id), ai.ready(s)
        src = {}
        for kind in ("meta", "google_ads"):
            if kind in card["kinds"]:
                r = s.execute(select(func.sum(AdDaily.spend), func.sum(AdDaily.conversion_value), func.sum(AdDaily.conversions),
                                     func.sum(AdDaily.leads), func.sum(AdDaily.clicks)).where(
                    AdDaily.firm_id == firm.id, AdDaily.source == kind, AdDaily.day >= start, AdDaily.day <= end)).one()
                sp = float(r[0] or 0)
                src[kind] = {"spend": sp, "value": float(r[1] or 0), "conv": float(r[2] or 0), "leads": float(r[3] or 0),
                             "clicks": int(r[4] or 0), "roas": (float(r[1] or 0) / sp) if sp else None}
    return tpl.TemplateResponse(request, "firm.html", {
        "c": card, "firm": firm, "series": series, "chans": chans, "hourly": hourly, "orders": orders, "unpaid": unpaid, "best": best, "orders_capped": orders_capped, "pending": pending,
        "src": src, "camps": camps, "ai_note": ai_note, "ai_ready": ai_ready, "start": start, "end": end, "preset": preset, "now": datetime.now(config.TIMEZONE)})


@app.get("/firma/{firm_id}/urunler", response_class=HTMLResponse)
def firm_products(request: Request, firm_id: int, p: str = "30g", bas: str | None = None, bit: str | None = None,
                  k: str = ""):
    start, end, preset = metrics.resolve_range(p, bas, bit)
    with SessionLocal() as s:
        firm = s.scalar(select(Firm).options(selectinload(Firm.integrations)).where(Firm.id == firm_id))
        if not firm:
            return RedirectResponse("/", 303)
        a = analysis.product_analysis(s, firm.id, start, end, k if k in ("web", "mp") else "")
        has_mp = s.scalar(select(func.count(Order.id)).where(Order.firm_id == firm.id, Order.channel != "Web sitesi")) or 0
    return tpl.TemplateResponse(request, "products.html", {
        "firm": firm, "a": a, "start": start, "end": end, "preset": preset, "k": k, "has_mp": has_mp > 0,
        "now": datetime.now(config.TIMEZONE)})


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
def integ_form(request: Request, firma: int, tur: str | None = None, id: int | None = None):
    with SessionLocal() as s:
        firm = s.get(Firm, firma)
        integ = s.get(Integration, id) if id else None
        cfg = decrypt(integ.config_enc) if integ else {}
    kind = integ.kind if integ else tur
    if kind not in REGISTRY or not firm:
        return RedirectResponse("/ayarlar", 303)
    masked = {k: (mask(v) if any(f.name == k and f.secret for f in REGISTRY[kind].FIELDS) else v) for k, v in cfg.items()}
    return tpl.TemplateResponse(request, "integration.html", {
        "firm": firm, "kind": kind, "mod": REGISTRY[kind], "integ": integ, "values": masked})


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


@app.post("/baglanti/{iid}/test")
def integ_test(iid: int):
    with SessionLocal() as s:
        integ = s.get(Integration, iid)
        if not integ:
            return JSONResponse({"ok": False, "message": "Bulunamadı"})
        kind, cfg = integ.kind, decrypt(integ.config_enc)
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
