"""Uygulama ayarları — hepsi .env dosyasından okunur."""
import os
from zoneinfo import ZoneInfo


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


ADMIN_USER = _env("ADMIN_USER", "fatih")
ADMIN_PASSWORD = _env("ADMIN_PASSWORD")          # zorunlu
SECRET_KEY = _env("SECRET_KEY")                  # zorunlu, en az 32 karakter
DATABASE_URL = _env("DATABASE_URL", "sqlite:///./data/panel.db")
TIMEZONE = ZoneInfo(_env("TIMEZONE", "Europe/Istanbul"))
SYNC_MINUTES = int(_env("SYNC_MINUTES", "30"))   # kaç dakikada bir veri çekilsin
BACKFILL_DAYS = int(_env("BACKFILL_DAYS", "60")) # ilk bağlantıda kaç günlük geçmiş
RECENT_DAYS = int(_env("RECENT_DAYS", "3"))      # her senkronda yeniden çekilen son günler
COOKIE_SECURE = _env("COOKIE_SECURE", "1") == "1"

# API sürümleri — platform yeni sürüme geçtiğinde sadece .env'den değiştirin
SHOPIFY_API_VERSION = _env("SHOPIFY_API_VERSION", "2026-07")
META_API_VERSION = _env("META_API_VERSION", "v26.0")
GOOGLE_ADS_API_VERSION = _env("GOOGLE_ADS_API_VERSION", "v26")


def check():
    missing = [n for n, v in (("ADMIN_PASSWORD", ADMIN_PASSWORD), ("SECRET_KEY", SECRET_KEY)) if not v]
    if missing:
        raise SystemExit(f".env dosyasında eksik ayar: {', '.join(missing)}")
    if len(SECRET_KEY) < 32:
        raise SystemExit("SECRET_KEY en az 32 karakter olmalı")
