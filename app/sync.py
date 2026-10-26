"""Platformlardan veri çekip veritabanına yazan senkronizasyon işi."""
import logging

import httpx
import threading
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select

from . import config
from .connectors import REGISTRY, STORE_KINDS, validate
from .connectors.base import ConnectorError
from .db import AdDaily, Integration, Order, SessionLocal
from .secure import decrypt

log = logging.getLogger("sync")
_lock = threading.Lock()


def today():
    return datetime.now(config.TIMEZONE).date()


def sync_integration(integration_id: int) -> tuple[bool, str]:
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
            validate(integ.kind, cfg)
            result = mod.fetch(cfg, start, end)
        except ConnectorError as e:
            ok, msg = False, str(e)
        except httpx.HTTPError as e:
            ok, msg = False, f"Platforma bağlanılamadı: {type(e).__name__}: {str(e)[:200]}"
        except Exception as e:  # noqa: BLE001
            log.exception("sync failed %s", integration_id)
            ok, msg = False, f"Beklenmeyen hata: {type(e).__name__}: {str(e)[:250]}"
        else:
            _store(s, integ, result, start, end)
            integ.backfilled = True
            ok = True
            msg = f"{len(result.orders)} sipariş" if integ.kind in STORE_KINDS else f"{len(result.ads)} gün"
            msg += f" güncellendi ({start:%d.%m} – {end:%d.%m})"
        integ.last_sync_at = datetime.utcnow()
        integ.last_ok, integ.last_message = ok, msg
        s.commit()
        return ok, msg


def _store(s, integ, result, start, end):
    if integ.kind in STORE_KINDS:
        existing = {o.external_id: o for o in s.scalars(select(Order).where(
            Order.integration_id == integ.id,
            Order.external_id.in_([r.external_id for r in result.orders] or ["-"])))}
        for r in result.orders:
            o = existing.get(r.external_id) or Order(firm_id=integ.firm_id, integration_id=integ.id,
                                                      source=integ.kind, external_id=r.external_id)
            o.number, o.created_at, o.total, o.currency = r.number, r.created_at, r.total, r.currency
            o.status, o.is_pending, o.is_cancelled = r.status, r.is_pending, r.is_cancelled
            o.channel = (r.channel or "Web sitesi")[:40]
            s.add(o)
    else:
        s.execute(delete(AdDaily).where(AdDaily.integration_id == integ.id,
                                        AdDaily.day >= start, AdDaily.day <= end))
        for r in result.ads:
            s.add(AdDaily(firm_id=integ.firm_id, integration_id=integ.id, source=integ.kind, day=r.day,
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
    sched.start()
    return sched
