"""Veritabanı modelleri (SQLite varsayılan, PostgreSQL de çalışır)."""
import os
from datetime import datetime, date

from sqlalchemy import (Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text,
                        UniqueConstraint, create_engine)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from . import config

if config.DATABASE_URL.startswith("sqlite:///"):
    os.makedirs(os.path.dirname(config.DATABASE_URL.replace("sqlite:///", "")) or ".", exist_ok=True)

engine = create_engine(
    config.DATABASE_URL,
    connect_args={"check_same_thread": False} if config.DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Firm(Base):
    __tablename__ = "firms"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    color: Mapped[str] = mapped_column(String(9), default="#4f46e5")
    roas_alert: Mapped[float] = mapped_column(Float, default=3.0)   # bu ROAS'ın altı uyarı
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    integrations: Mapped[list["Integration"]] = relationship(back_populates="firm", cascade="all, delete-orphan")


class Integration(Base):
    __tablename__ = "integrations"
    id: Mapped[int] = mapped_column(primary_key=True)
    firm_id: Mapped[int] = mapped_column(ForeignKey("firms.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(20))           # shopify | ticimax | meta | google_ads
    label: Mapped[str] = mapped_column(String(120), default="")
    config_enc: Mapped[str] = mapped_column(Text)            # şifrelenmiş JSON
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    backfilled: Mapped[bool] = mapped_column(Boolean, default=False)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_message: Mapped[str] = mapped_column(Text, default="")
    stock_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    stock_message: Mapped[str] = mapped_column(Text, default="")
    firm: Mapped[Firm] = relationship(back_populates="integrations")


class Order(Base):
    """Mağaza siparişleri (Shopify / Ticimax)."""
    __tablename__ = "orders"
    __table_args__ = (UniqueConstraint("integration_id", "external_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    firm_id: Mapped[int] = mapped_column(ForeignKey("firms.id", ondelete="CASCADE"), index=True)
    integration_id: Mapped[int] = mapped_column(ForeignKey("integrations.id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(String(20))
    external_id: Mapped[str] = mapped_column(String(64))
    number: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)   # yerel saat (İstanbul)
    total: Mapped[float] = mapped_column(Float, default=0)
    currency: Mapped[str] = mapped_column(String(8), default="TRY")
    status: Mapped[str] = mapped_column(String(60), default="")
    is_pending: Mapped[bool] = mapped_column(Boolean, default=False)     # kargolanmadı
    is_cancelled: Mapped[bool] = mapped_column(Boolean, default=False)   # iptal / iade
    channel: Mapped[str] = mapped_column(String(40), default="Web sitesi", index=True)  # satış kanalı
    payment_status: Mapped[str] = mapped_column(String(40), default="")  # ödeme statüsü
    customer: Mapped[str] = mapped_column(String(120), default="")       # müşteri adı soyadı


class AdDaily(Base):
    """Reklam platformlarından günlük hesap seviyesi metrikler."""
    __tablename__ = "ad_daily"
    __table_args__ = (UniqueConstraint("integration_id", "day"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    firm_id: Mapped[int] = mapped_column(ForeignKey("firms.id", ondelete="CASCADE"), index=True)
    integration_id: Mapped[int] = mapped_column(ForeignKey("integrations.id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(String(20))
    day: Mapped[date] = mapped_column(Date, index=True)
    spend: Mapped[float] = mapped_column(Float, default=0)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    conversions: Mapped[float] = mapped_column(Float, default=0)
    conversion_value: Mapped[float] = mapped_column(Float, default=0)
    leads: Mapped[float] = mapped_column(Float, default=0)


class OrderItem(Base):
    """Sipariş kalemleri — ürün / beden bazlı satış analizi için."""
    __tablename__ = "order_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    firm_id: Mapped[int] = mapped_column(ForeignKey("firms.id", ondelete="CASCADE"), index=True)
    integration_id: Mapped[int] = mapped_column(ForeignKey("integrations.id", ondelete="CASCADE"))
    product_id: Mapped[str] = mapped_column(String(64), default="")
    variant_id: Mapped[str] = mapped_column(String(64), default="")
    sku: Mapped[str] = mapped_column(String(120), default="")
    barcode: Mapped[str] = mapped_column(String(120), default="")
    name: Mapped[str] = mapped_column(String(255), default="")
    variant: Mapped[str] = mapped_column(String(120), default="")
    quantity: Mapped[float] = mapped_column(Float, default=0)
    amount: Mapped[float] = mapped_column(Float, default=0)


class VariantStock(Base):
    """Varyasyon (beden) bazında güncel stok — her stok senkronunda baştan yazılır."""
    __tablename__ = "variant_stock"
    id: Mapped[int] = mapped_column(primary_key=True)
    firm_id: Mapped[int] = mapped_column(ForeignKey("firms.id", ondelete="CASCADE"), index=True)
    integration_id: Mapped[int] = mapped_column(ForeignKey("integrations.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[str] = mapped_column(String(64), default="")
    variant_id: Mapped[str] = mapped_column(String(64), default="")
    product_name: Mapped[str] = mapped_column(String(255), default="")
    variant: Mapped[str] = mapped_column(String(120), default="")
    sku: Mapped[str] = mapped_column(String(120), default="")
    barcode: Mapped[str] = mapped_column(String(120), default="")
    stock: Mapped[float] = mapped_column(Float, default=0)
    price: Mapped[float] = mapped_column(Float, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class AppSetting(Base):
    """Uygulama ayarları (değerler şifreli JSON)."""
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class AiNote(Base):
    """Firma için yazılan yapay zekâ yorumları."""
    __tablename__ = "ai_notes"
    id: Mapped[int] = mapped_column(primary_key=True)
    firm_id: Mapped[int] = mapped_column(ForeignKey("firms.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    model: Mapped[str] = mapped_column(String(60), default="")
    text: Mapped[str] = mapped_column(Text, default="")


class AdCampaignDaily(Base):
    """Reklam platformlarından günlük kampanya seviyesi metrikler."""
    __tablename__ = "ad_campaign_daily"
    __table_args__ = (UniqueConstraint("integration_id", "day", "campaign_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    firm_id: Mapped[int] = mapped_column(ForeignKey("firms.id", ondelete="CASCADE"), index=True)
    integration_id: Mapped[int] = mapped_column(ForeignKey("integrations.id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(String(20))
    day: Mapped[date] = mapped_column(Date, index=True)
    campaign_id: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(30), default="")
    spend: Mapped[float] = mapped_column(Float, default=0)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    conversions: Mapped[float] = mapped_column(Float, default=0)
    conversion_value: Mapped[float] = mapped_column(Float, default=0)
    leads: Mapped[float] = mapped_column(Float, default=0)


def init_db():
    Base.metadata.create_all(engine)
    _migrate()


def _migrate():
    """Eski kurulumlara yeni sütunları ekler (veri kaybı olmadan)."""
    from sqlalchemy import inspect, text
    cols = {c["name"] for c in inspect(engine).get_columns("orders")}
    with engine.begin() as con:
        con.execute(text("CREATE TABLE IF NOT EXISTS meta (key VARCHAR(60) PRIMARY KEY)"))
        if "channel" not in cols:
            con.execute(text("ALTER TABLE orders ADD COLUMN channel VARCHAR(40) DEFAULT 'Web sitesi'"))
            con.execute(text("CREATE INDEX IF NOT EXISTS ix_orders_channel ON orders (channel)"))
        if "customer" not in cols:
            con.execute(text("ALTER TABLE orders ADD COLUMN customer VARCHAR(120) DEFAULT ''"))
        if "payment_status" not in cols:
            con.execute(text("ALTER TABLE orders ADD COLUMN payment_status VARCHAR(40) DEFAULT ''"))
        # Ticimax panelindeki adıyla: "Ön Sipariş" → "Sipariş Alındı"
        con.execute(text("UPDATE orders SET status = 'Sipariş Alındı' WHERE status = 'Ön Sipariş'"))
        con.execute(text("UPDATE orders SET payment_status = 'Tamamlanmadı' WHERE payment_status = '5'"))
        if not con.execute(text("SELECT 1 FROM meta WHERE key = 'ticimax_sayfalama'")).first():
            # sayfalama düzeltildi: 100'den fazla siparişi olan dönemler eksik gelmişti, geçmiş baştan çekilir
            con.execute(text("UPDATE integrations SET backfilled = 0, stock_synced_at = NULL WHERE kind = 'ticimax'"))
            con.execute(text("INSERT INTO meta (key) VALUES ('ticimax_sayfalama')"))
        # Tek seferlik: Ticimax ciro kuralı ödeme statüsüne göre çalışıyor. Eski siparişler yeni kurala göre
        # yeniden hesaplansın diye bir sonraki senkronda geçmiş baştan çekilir.
        if not con.execute(text("SELECT 1 FROM meta WHERE key = 'ticimax_odeme_statusu_v2'")).first():
            con.execute(text("UPDATE integrations SET backfilled = 0 WHERE kind = 'ticimax'"))
            con.execute(text("INSERT INTO meta (key) VALUES ('ticimax_odeme_statusu_v2')"))
        icols = {c["name"] for c in inspect(engine).get_columns("integrations")}
        if "stock_synced_at" not in icols:
            con.execute(text("ALTER TABLE integrations ADD COLUMN stock_synced_at DATETIME"))
        if "stock_message" not in icols:
            con.execute(text("ALTER TABLE integrations ADD COLUMN stock_message TEXT DEFAULT ''"))
        if not con.execute(text("SELECT 1 FROM meta WHERE key = 'stok_filtre'")).first():
            # ürün servisi filtresi düzeltildi: stoklar bir sonraki senkronda hemen yeniden denenir
            con.execute(text("UPDATE integrations SET stock_synced_at = NULL WHERE kind = 'ticimax'"))
            con.execute(text("INSERT INTO meta (key) VALUES ('stok_filtre')"))
        if not con.execute(text("SELECT 1 FROM meta WHERE key = 'urun_kalemleri'")).first():
            # ürün analizi geçmişe dönük dolsun: mağaza bağlantılarında geçmiş baştan çekilir
            con.execute(text("UPDATE integrations SET backfilled = 0 WHERE kind IN ('ticimax', 'shopify')"))
            con.execute(text("INSERT INTO meta (key) VALUES ('urun_kalemleri')"))
        if not con.execute(text("SELECT 1 FROM meta WHERE key = 'kampanyalar'")).first():
            # kampanya verisi geçmişe dönük de dolsun: reklam bağlantılarında geçmiş baştan çekilir
            con.execute(text("UPDATE integrations SET backfilled = 0 WHERE kind IN ('meta', 'google_ads')"))
            con.execute(text("INSERT INTO meta (key) VALUES ('kampanyalar')"))
        if not con.execute(text("SELECT 1 FROM meta WHERE key = 'musteri_adi'")).first():
            con.execute(text("UPDATE integrations SET backfilled = 0 WHERE kind IN ('ticimax', 'shopify')"))
            con.execute(text("INSERT INTO meta (key) VALUES ('musteri_adi')"))
