"""Yapay zekâ yorumu: firmanın toplam verilerini Claude'a gönderip kısa bir değerlendirme yazdırır.

Gizlilik: Claude'a yalnızca toplam rakamlar gider (ciro, sipariş sayısı, kanal/kampanya/ürün özetleri).
Müşteri adı, sipariş numarası, adres, e-posta gibi bilgiler hiçbir zaman gönderilmez.
"""
import json
import logging
from datetime import datetime, timedelta

import httpx
from sqlalchemy import select

from . import config, metrics
from .db import AiNote, AppSetting, Firm, SessionLocal
from .secure import decrypt, encrypt

log = logging.getLogger("ai")
DEFAULT_MODEL = "claude-sonnet-5-5"
API_URL = "https://api.anthropic.com/v1/messages"

SYSTEM = """Sen Türkiye'de çalışan deneyimli bir e-ticaret danışmanısın. Sana bir firmanın e-ticaret paneli verileri JSON olarak verilecek.
Görevin, firma yöneticisinin bugün ne yapması gerektiğini anlamasını sağlayan kısa bir değerlendirme yazmak.

Kurallar:
- Türkçe yaz. En fazla 6 madde. Her madde tek cümle ya da iki kısa cümle.
- En önemli konudan başla. Önce sorunlar ve kaçan satışlar, sonra fırsatlar.
- Her maddede verideki somut bir rakama dayan (₺, adet, %, ROAS). Veride olmayan bir şeyi uydurma, tahmin yürütüyorsan "olabilir" de.
- Mümkünse her maddeyi yapılacak bir işle bitir (ör. "… kampanyasının bütçesini düşürmeyi düşün", "… bedenleri için tedarikçiye sipariş geç").
- Pazaryeri satışlarını reklam başarısına sayma; reklamın etkisi web sitesi cirosu üzerinden değerlendirilir.
- "Tamamlanmadı", "Hatalı", "Ödeme kaydı yok" siparişler satış değildir; bunlar yarıda kalan ödeme denemeleridir.
- Selamlama, giriş cümlesi veya kapanış yazma. Sadece maddeleri "- " ile başlayarak yaz.
- Kayda değer bir şey yoksa bunu tek maddede söyle."""


# ---------- ayarlar ----------
def get_settings(s) -> dict:
    row = s.get(AppSetting, "ai")
    if not row:
        return {"api_key": "", "model": DEFAULT_MODEL, "enabled": False, "hour": 8}
    try:
        d = decrypt(row.value)
    except Exception:  # noqa: BLE001
        return {"api_key": "", "model": DEFAULT_MODEL, "enabled": False, "hour": 8}
    d.setdefault("model", DEFAULT_MODEL)
    d.setdefault("hour", 8)
    return d


def save_settings(s, api_key: str | None, model: str, enabled: bool, hour: int):
    cur = get_settings(s)
    if api_key:                       # boş bırakılırsa mevcut anahtar korunur
        cur["api_key"] = api_key.strip()
    cur["model"] = (model or DEFAULT_MODEL).strip()
    cur["enabled"] = bool(enabled)
    cur["hour"] = max(0, min(23, int(hour)))
    row = s.get(AppSetting, "ai") or AppSetting(key="ai")
    row.value = encrypt(cur)
    s.add(row)
    s.commit()


def ready(s) -> bool:
    st = get_settings(s)
    return bool(st.get("enabled") and st.get("api_key"))


# ---------- API ----------
def _call(st: dict, system: str, user: str, max_tokens=900) -> str:
    r = httpx.post(API_URL, timeout=90, headers={
        "x-api-key": st["api_key"], "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": st.get("model") or DEFAULT_MODEL, "max_tokens": max_tokens, "system": system,
              "messages": [{"role": "user", "content": user}]})
    if r.status_code != 200:
        try:
            msg = r.json().get("error", {}).get("message", r.text)
        except Exception:  # noqa: BLE001
            msg = r.text
        if r.status_code == 401:
            msg = "API anahtarı geçersiz."
        elif r.status_code == 404:
            msg = f"Model bulunamadı ({st.get('model')}). Ayarlardan model adını kontrol et. {msg}"
        raise RuntimeError(f"Claude API hatası ({r.status_code}): {str(msg)[:300]}")
    body = r.json()
    return "".join(b.get("text", "") for b in body.get("content", []) if b.get("type") == "text").strip()


def test_connection(s) -> str:
    st = get_settings(s)
    if not st.get("api_key"):
        raise RuntimeError("Önce API anahtarını kaydet.")
    txt = _call(st, "Kısa cevap ver.", "Sadece 'Bağlantı tamam' yaz.", max_tokens=20)
    return f"Bağlantı başarılı ({st.get('model')}): {txt[:60]}"


# ---------- bağlam (sadece toplam veriler) ----------
def _sum(sm) -> dict:
    d = {"ciro": round(sm.revenue), "siparis": sm.orders, "web_ciro": round(sm.web_revenue), "web_siparis": sm.web_orders,
         "pazaryeri_ciro": round(sm.mp_revenue), "pazaryeri_siparis": sm.mp_orders, "sepet_ort": round(sm.aov),
         "ciro_disi_siparis": sm.cancelled, "reklam_harcama": round(sm.spend), "meta_harcama": round(sm.spend_meta),
         "google_harcama": round(sm.spend_google), "platform_donusum_degeri": round(sm.conv_value),
         "lead": round(sm.leads), "tiklama": sm.clicks}
    d["roas_web"] = round(sm.roas, 2) if sm.roas else None
    d["roas_platform"] = round(sm.platform_roas, 2) if sm.platform_roas else None
    d["lead_basi"] = round(sm.cpl) if sm.cpl else None
    return {k: v for k, v in d.items() if v not in (0, None)}


def build_context(s, firm) -> dict:
    from . import analysis
    now = datetime.now(config.TIMEZONE)
    today = now.date()
    kinds = {i.kind for i in firm.integrations if i.enabled}
    ctx = {"firma": firm.name, "tarih_saat": now.strftime("%d.%m.%Y %H:%M ") + ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"][now.weekday()], "roas_hedefi": firm.roas_alert,
           "firma_notu": (firm.note or "")[:500] or None}

    def period(a, b, until_now=False):
        return _sum(metrics.summary(s, firm.id, a, b, until_now=until_now))
    ctx["bugun_su_ana_kadar"] = period(today, today, True)
    ctx["gecen_hafta_ayni_gun_ayni_saate_kadar"] = period(today - timedelta(days=7), today - timedelta(days=7), True)
    ctx["dun"] = period(today - timedelta(days=1), today - timedelta(days=1))
    ctx["son_7_gun"] = period(today - timedelta(days=6), today)
    ctx["onceki_7_gun"] = period(today - timedelta(days=13), today - timedelta(days=7))
    ctx["son_30_gun"] = period(today - timedelta(days=29), today)
    ctx["onceki_30_gun"] = period(today - timedelta(days=59), today - timedelta(days=30))
    if kinds & {"shopify", "ticimax"}:
        ctx["kanallar_son_7_gun"] = [
            {"kanal": c["channel"], "ciro": round(c["revenue"]), "siparis": c["orders"],
             "degisim_yuzde": round(c["chg"]) if c["chg"] is not None else None}
            for c in metrics.channel_breakdown(s, firm.id, today - timedelta(days=6), today)]
        a = analysis.product_analysis(s, firm.id, today - timedelta(days=6), today)
        if a["has_items"]:
            ctx["cok_satan_urunler_son_7_gun"] = [
                {"urun": p["product"], "adet": p["qty"], "ciro": round(p["revenue"]),
                 "toplam_stok": p["stock"], "tukenen_beden": p["out_names"][:8] or None}
                for p in a["products"][:12]]
            ctx["satisi_olup_stogu_tukenen_bedenler"] = [
                {"urun": v["product"], "beden": v["variant"] or v["sku"], "satis_adedi_7_gun": v["qty"],
                 "gunluk_ciro": round(v["revenue"] / a["days"])} for v in sorted(a["oos"], key=lambda x: -x["revenue"])[:10]]
            ctx["tukenmek_uzere"] = [
                {"urun": v["product"], "beden": v["variant"] or v["sku"], "stok": v["stock"], "kalan_gun": round(v["days_left"], 1)}
                for v in a["low"][:10]]
            ctx["stok_verisi_var"] = a["has_stock"]
            b = analysis.bestsellers_by_channel(s, firm.id, today - timedelta(days=6), today, limit=5)
            ctx["cok_satanlar_kanal_bazinda_son_7_gun"] = {
                c["channel"]: [{"urun": p["product"], "adet": p["qty"], "ciro": round(p["revenue"]),
                                "kalan_stok": p["stock"], "stok_kac_gun_yeter": round(p["days_left"], 1) if p["days_left"] else (0 if p["stock"] is not None and p["stock"] <= 0 else None)}
                               for p in c["products"]]
                for c in b["channels"][1:]}
    if kinds & {"meta", "google_ads"}:
        camps = metrics.campaigns(s, firm.id, today - timedelta(days=6), today)
        ctx["kampanyalar_son_7_gun"] = [
            {"kampanya": c["name"], "platform": c["source"], "durum": c["status"], "harcama": round(c["spend"]),
             "harcama_degisim_yuzde": round(c["chg"]) if c["chg"] is not None else None, "tiklama": c["clicks"],
             "donusum": round(c["conversions"], 1), "donusum_degeri": round(c["value"]),
             "roas": round(c["roas"], 2) if c["roas"] else None, "lead": round(c["leads"]),
             "lead_basi": round(c["cpl"]) if c["cpl"] else None} for c in camps[:12]]
    card = metrics.firm_card(s, firm, today, today, "bugun")
    ctx["panel_uyarilari"] = [a.text for a in card["alerts"]]
    return ctx


def generate(firm_id: int) -> tuple[bool, str]:
    from sqlalchemy.orm import selectinload
    with SessionLocal() as s:
        st = get_settings(s)
        if not st.get("api_key"):
            return False, "Yapay zekâ yorumu için Ayarlar'dan Claude API anahtarını gir."
        firm = s.scalar(select(Firm).options(selectinload(Firm.integrations)).where(Firm.id == firm_id))
        if not firm:
            return False, "Firma bulunamadı"
        ctx = build_context(s, firm)
        user = ("Aşağıdaki panel verilerine göre değerlendirmeni yaz.\n\n```json\n"
                + json.dumps(ctx, ensure_ascii=False, indent=1, default=str) + "\n```")
        try:
            text = _call(st, SYSTEM, user)
        except Exception as e:  # noqa: BLE001
            log.warning("AI yorumu yazılamadı (%s): %s", firm.name, e)
            return False, str(e)
        s.add(AiNote(firm_id=firm.id, text=text, model=st.get("model") or DEFAULT_MODEL))
        s.commit()
        return True, "Yorum güncellendi"


def latest(s, firm_id: int):
    return s.scalar(select(AiNote).where(AiNote.firm_id == firm_id).order_by(AiNote.created_at.desc()).limit(1))


def daily_job():
    """Her sabah (Ayarlar'daki saatte) tüm firmalar için yorum yazar."""
    with SessionLocal() as s:
        if not ready(s):
            return
        ids = list(s.scalars(select(Firm.id)))
    for fid in ids:
        ok, msg = generate(fid)
        log.info("AI yorumu firma %s: %s", fid, msg)
