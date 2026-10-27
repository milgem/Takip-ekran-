"""Platformlardan veri çekip veritabanına yazan senkronizasyon işi."""
import logging

import httpx
import threading
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select

from . import config
from .connectors import REGISTRY, STORE_KINDS, validate
from .connectors.base import ConnectorError
from .db import AdCampaignDaily, AdDaily, Integration, Order, OrderItem, SessionLocal, UnpaidHourly, VariantStock
from .secure import decrypt

log = logging.getLogger("sync")
_lock = threading.Lock()


def today():
    return datetime.now(config.TIMEZONE).date()


_running: dict[int, threading.Lock] = {}
_running_guard = threading.Lock()


def sync_integration(integration_id: int) -> tuple[bool, str]:
    """Aynı bağlantı için aynı anda tek senkron çalışır; ikinci istek beklemeden döner."""
    with _running_guard:
        lock = _running.setdefault(integration_id, threading.Lock())
    if not lock.acquire(blocking=False):
        return False, "Bu bağlantı şu an zaten senkronize ediliyor, birkaç dakika sonra tekrar bak."
    try:
        return _sync_integration(integration_id)
    except Exception as e:  # noqa: BLE001 — hiçbir hata sessizce kaybolmasın
        log.exception("sync crashed %s", integration_id)
        msg = f"Beklenmeyen hata: {type(e).__name__}: {str(e)[:250]}"
        with SessionLocal() as s:
            integ = s.get(Integration, integration_id)
            if integ:
                integ.last_sync_at, integ.last_ok, integ.last_message = datetime.utcnow(), False, msg
                s.commit()
        return False, msg
    finally:
        lock.release()


def _sync_integration(integration_id: int) -> tuple[bool, str]:
    with SessionLocal() as s:
        integ = s.get(Integration, integration_id)
        if not integ:
            return False, "Bağlantı bulunamadı"
        mod = REGISTRY[integ.kind]
        end = today()
        start = end - timedelta(days=config.BACKFILL_DAYS if not integ.backfilled else config.RECENT_DAYS)
        if integ.kind in STORE_KINDS and integ.backfilled:
            # Hâlâ bekleyen eski siparişlerin durumunu da güncellemek için pencereyi genişlet
            oldest = s.scalar(select(func.min(Order.created_at)).where(
                Order.integration_id == integ.id, Order.is_pending.is_(True),
                Order.created_at >= datetime.combine(end - timedelta(days=21), datetime.min.time())))
            if oldest and oldest.date() < start:
                start = oldest.date()
        try:
            cfg = decrypt(integ.config_enc)
            if cfg.get("demo"):
                return True, "Deneme verisi"   # örnek bağlantı: dışarıya istek atılmaz
            validate(integ.kind, cfg)
        except ConnectorError as e:
            integ.last_sync_at, integ.last_ok, integ.last_message = datetime.utcnow(), False, str(e)
            s.commit()
            return False, str(e)

        # İlk senkron (mağazalar): bugünden geriye doğru küçük parçalarla; her parça kaydedilir,
        # yarıda kalırsa kaldığı yerden devam eder. Sonraki senkronlar tek pencere.
        if integ.kind in STORE_KINDS and not integ.backfilled:
            limit = end - timedelta(days=config.BACKFILL_DAYS)
            top = (integ.backfill_from - timedelta(days=1)) if integ.backfill_from else end
            windows = []
            while top >= limit:
                bottom = max(limit, top - timedelta(days=config.BACKFILL_CHUNK_DAYS - 1))
                windows.append((bottom, top))
                top = bottom - timedelta(days=1)
            if integ.backfill_from:   # devam ederken son günleri de tazele
                windows.insert(0, (end - timedelta(days=config.RECENT_DAYS), end))
        else:
            windows = [(start, end)]

        total, ok, msg = 0, True, ""
        for i, (a, b) in enumerate(windows, 1):
            if len(windows) > 1:
                integ.last_message = (f"İlk senkronizasyon sürüyor: {b:%d.%m} – {a:%d.%m} arası çekiliyor "
                                      f"({i}/{len(windows)}, şu ana kadar {total} sipariş)…")
                s.commit()
            try:
                log.info("sync %s (%s): %s – %s çekiliyor", integration_id, integ.kind, a, b)
                result = mod.fetch(cfg, a, b)
            except ConnectorError as e:
                ok, msg = False, str(e)
            except httpx.HTTPError as e:
                ok, msg = False, f"Platforma bağlanılamadı: {type(e).__name__}: {str(e)[:200]}"
            except Exception as e:  # noqa: BLE001
                log.exception("sync failed %s", integration_id)
                ok, msg = False, f"Beklenmeyen hata: {type(e).__name__}: {str(e)[:250]}"
            if not ok:
                if len(windows) > 1 and total:
                    msg = f"{msg} (ilk senkronizasyon {a:%d.%m} tarihinde durdu; {total} sipariş kaydedildi, bir sonraki senkronda devam edecek)"
                break
            try:
                _store(s, integ, result, a, b)
            except Exception as e:  # noqa: BLE001
                s.rollback()
                log.exception("store failed %s", integration_id)
                integ = s.get(Integration, integration_id)
                integ.last_sync_at, integ.last_ok = datetime.utcnow(), False
                integ.last_message = f"Veriler kaydedilemedi: {type(e).__name__}: {str(e)[:250]}"
                s.commit()
                return False, integ.last_message
            total += len(result.orders) if integ.kind in STORE_KINDS else len(result.ads)
            if integ.kind in STORE_KINDS and not integ.backfilled and (integ.backfill_from is None or a < integ.backfill_from):
                integ.backfill_from = a
            s.commit()
        if ok:
            integ.backfilled = True
            integ.backfill_from = None
            first, last = min(w[0] for w in windows), max(w[1] for w in windows)
            msg = (f"{total} sipariş" if integ.kind in STORE_KINDS else f"{total} gün") + f" güncellendi ({first:%d.%m} – {last:%d.%m})"
        integ.last_sync_at = datetime.utcnow()
        integ.last_ok, integ.last_message = ok, msg
        s.commit()
        if ok:   # stoklar siparişler kaydedildikten sonra; uzun sürse de siparişler görünür
            if hasattr(mod, "fetch_stock") and (integ.stock_synced_at is None):
                integ.stock_message = "Stoklar çekiliyor…"
                s.commit()
            _maybe_sync_stock(s, integ, mod, cfg)
            s.commit()
        from . import insights
        insights.clear_cache(integ.firm_id)
        return ok, msg


def _maybe_sync_stock(s, integ, mod, cfg, force=False):
    """Beden bazlı stokları en fazla STOCK_HOURS'ta bir yeniler. Hata olursa siparişleri etkilemez."""
    if not hasattr(mod, "fetch_stock"):
        return
    if not force and integ.stock_synced_at and \
            datetime.utcnow() - integ.stock_synced_at < timedelta(hours=config.STOCK_HOURS):
        return
    try:
        t0 = datetime.utcnow()
        rows = mod.fetch_stock(cfg)
        log.info("stok %s: %s varyasyon, %.0f sn", integ.id, len(rows), (datetime.utcnow() - t0).total_seconds())
    except Exception as e:  # noqa: BLE001
        log.warning("stok alınamadı %s: %s", integ.id, e)
        integ.stock_message = f"Stok alınamadı: {str(e)[:250]}"
        integ.stock_synced_at = datetime.utcnow()
        return
    s.execute(delete(VariantStock).where(VariantStock.integration_id == integ.id))
    for r in rows:
        s.add(VariantStock(firm_id=integ.firm_id, integration_id=integ.id, product_id=str(r.product_id)[:64],
                           variant_id=str(r.variant_id)[:64], product_name=(r.product_name or "")[:255],
                           variant=(r.variant or "")[:120], sku=(r.sku or "")[:120], barcode=(r.barcode or "")[:120],
                           stock=float(r.stock or 0), price=float(r.price or 0), active=bool(r.active),
                           brand=(r.brand or "")[:120], category=(r.category or "")[:200]))
    integ.stock_synced_at = datetime.utcnow()
    integ.stock_message = f"{len(rows)} varyasyon stoğu güncellendi"


def _store_unpaid(s, integ, unpaid, start, end):
    """Ödemesi tamamlanmayan siparişler: pencere için saatlik özet yeniden yazılır, varsa eski satırları silinir."""
    a = datetime.combine(start, datetime.min.time())
    b = datetime.combine(end, datetime.max.time())
    s.execute(delete(UnpaidHourly).where(UnpaidHourly.integration_id == integ.id, UnpaidHourly.hour >= a, UnpaidHourly.hour <= b))
    agg = {}
    for r in unpaid:
        k = (r.created_at.replace(minute=0, second=0, microsecond=0), (r.channel or "Web sitesi")[:40], (r.payment_status or "")[:40])
        c = agg.setdefault(k, [0, 0.0])
        c[0] += 1
        c[1] += float(r.total or 0)
    for (hour, ch, st), (n, amt) in agg.items():
        s.add(UnpaidHourly(firm_id=integ.firm_id, integration_id=integ.id, hour=hour, channel=ch, payment_status=st, count=n, amount=amt))
    ids = [r.external_id for r in unpaid]
    for i in range(0, len(ids), 500):
        old = list(s.scalars(select(Order.id).where(Order.integration_id == integ.id, Order.external_id.in_(ids[i:i + 500]))))
        if old:
            s.execute(delete(OrderItem).where(OrderItem.order_id.in_(old)))
            s.execute(delete(Order).where(Order.id.in_(old)))


def _store(s, integ, result, start, end):
    if integ.kind in STORE_KINDS:
        unpaid = [r for r in result.orders if r.is_cancelled and r.payment_status]
        _store_unpaid(s, integ, unpaid, start, end)
        result.orders = [r for r in result.orders if not (r.is_cancelled and r.payment_status)]
        existing = {o.external_id: o for o in s.scalars(select(Order).where(
            Order.integration_id == integ.id,
            Order.external_id.in_([r.external_id for r in result.orders] or ["-"])))}
        for r in result.orders:
            o = existing.get(r.external_id) or Order(firm_id=integ.firm_id, integration_id=integ.id,
                                                      source=integ.kind, external_id=r.external_id)
            o.number, o.created_at, o.total, o.currency = r.number, r.created_at, r.total, r.currency
            o.status, o.is_pending, o.is_cancelled = r.status, r.is_pending, r.is_cancelled
            o.channel = (r.channel or "Web sitesi")[:40]
            o.payment_status = (r.payment_status or "")[:40]
            o.customer = (r.customer or "")[:120]
            o.commission, o.cost = float(r.commission or 0), float(r.cost or 0)
            s.add(o)
            existing[r.external_id] = o
        s.flush()
        # sipariş kalemleri: çekilen siparişlerin kalemleri baştan yazılır
        with_items = [(existing[r.external_id], r) for r in result.orders if r.items is not None]
        ids = [o.id for o, _ in with_items]
        for i in range(0, len(ids), 500):
            s.execute(delete(OrderItem).where(OrderItem.order_id.in_(ids[i:i + 500])))
        for o, r in with_items:
            for it in r.items:
                s.add(OrderItem(order_id=o.id, firm_id=integ.firm_id, integration_id=integ.id,
                                product_id=str(it.product_id or "")[:64], variant_id=str(it.variant_id or "")[:64],
                                sku=(it.sku or "")[:120], barcode=(it.barcode or "")[:120], name=(it.name or "")[:255],
                                variant=(it.variant or "")[:120], quantity=float(it.quantity or 0), amount=float(it.amount or 0),
                                discount=float(it.discount or 0), cost=float(it.cost or 0), campaign_id=str(it.campaign_id or "")[:64],
                                campaign=(it.campaign or "")[:200], brand=(it.brand or "")[:120], category=(it.category or "")[:200]))
    else:
        s.execute(delete(AdDaily).where(AdDaily.integration_id == integ.id,
                                        AdDaily.day >= start, AdDaily.day <= end))
        for r in result.ads:
            s.add(AdDaily(firm_id=integ.firm_id, integration_id=integ.id, source=integ.kind, day=r.day,
                          spend=r.spend, impressions=r.impressions, clicks=r.clicks, conversions=r.conversions,
                          conversion_value=r.conversion_value, leads=r.leads))
        s.execute(delete(AdCampaignDaily).where(AdCampaignDaily.integration_id == integ.id,
                                                AdCampaignDaily.day >= start, AdCampaignDaily.day <= end))
        seen = set()
        for r in result.campaigns:
            key = (r.day, r.campaign_id)
            if key in seen:
                continue
            seen.add(key)
            s.add(AdCampaignDaily(firm_id=integ.firm_id, integration_id=integ.id, source=integ.kind, day=r.day,
                                  campaign_id=str(r.campaign_id)[:40], name=(r.name or "")[:255], status=(r.status or "")[:30],
                                  spend=r.spend, impressions=r.impressions, clicks=r.clicks, conversions=r.conversions,
                                  conversion_value=r.conversion_value, leads=r.leads))


def sync_all():
    if not _lock.acquire(blocking=False):
        return  # önceki tur hâlâ çalışıyor
    try:
        with SessionLocal() as s:
            ids = list(s.scalars(select(Integration.id).where(Integration.enabled.is_(True))))
        for i in ids:
            ok, msg = sync_integration(i)
            log.info("sync %s: %s %s", i, "OK" if ok else "HATA", msg)
    finally:
        _lock.release()


def start_scheduler():
    from apscheduler.schedulers.background import BackgroundScheduler
    sched = BackgroundScheduler(timezone=str(config.TIMEZONE))
    sched.add_job(sync_all, "interval", minutes=config.SYNC_MINUTES, next_run_time=datetime.now(config.TIMEZONE),
                  max_instances=1, coalesce=True)
    sched.add_job(_ai_tick, "cron", minute=5, max_instances=1, coalesce=True)
    sched.start()
    return sched


def _ai_tick():
    """Her saat başı çalışır; Ayarlar'daki saatte, o gün yorumu yazılmamış firmalar için yorum yazar."""
    from . import ai
    from .db import AiNote, Firm
    now = datetime.now(config.TIMEZONE)
    with SessionLocal() as s:
        st = ai.get_settings(s)
        if not (st.get("enabled") and st.get("api_key")) or now.hour < int(st.get("hour", 8)):
            return
        from datetime import timezone
        day0_utc = now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).replace(tzinfo=None)
        done = set(s.scalars(select(AiNote.firm_id).where(AiNote.created_at >= day0_utc)))
        ids = [f for f in s.scalars(select(Firm.id)) if f not in done]
    for fid in ids:
        ok, msg = ai.generate(fid)
        log.info("AI yorumu firma %s: %s", fid, msg)
