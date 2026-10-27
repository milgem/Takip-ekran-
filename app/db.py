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
    logo: Mapped[str] = mapped_column(String(60), default="")       # data/logos altındaki dosya adı
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
    backfill_from: Mapped[date | None] = mapped_column(Date, nullable=True)   # ilk senkronda buraya kadar (geriye) çekildi
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
    commission: Mapped[float] = mapped_column(Float, default=0)          # pazaryeri komisyonu
    cost: Mapped[float] = mapped_column(Float, default=0)                # ürün maliyeti (platform veriyorsa)
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


class UnpaidHourly(Base):
    """Ödemesi tamamlanmayan siparişler tek tek tutulmaz; saat, kanal ve ödeme durumuna göre sayı ve tutar olarak tutulur."""
    __tablename__ = "unpaid_hourly"
    __table_args__ = (UniqueConstraint("integration_id", "hour", "channel", "payment_status"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    firm_id: Mapped[int] = mapped_column(ForeignKey("firms.id", ondelete="CASCADE"), index=True)
    integration_id: Mapped[int] = mapped_column(ForeignKey("integrations.id", ondelete="CASCADE"))
    hour: Mapped[datetime] = mapped_column(DateTime, index=True)       # yerel saat, dakikası sıfır
    channel: Mapped[str] = mapped_column(String(40), default="Web sitesi")
    payment_status: Mapped[str] = mapped_column(String(40), default="")
    count: Mapped[int] = mapped_column(Integer, default=0)
    amount: Mapped[float] = mapped_column(Float, default=0)


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
    discount: Mapped[float] = mapped_column(Float, default=0)
    cost: Mapped[float] = mapped_column(Float, default=0)
    campaign_id: Mapped[str] = mapped_column(String(64), default="")
    campaign: Mapped[str] = mapped_column(String(200), default="")
    brand: Mapped[str] = mapped_column(String(120), default="")
    category: Mapped[str] = mapped_column(String(200), default="")


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
    brand: Mapped[str] = mapped_column(String(120), default="")
    category: Mapped[str] = mapped_column(String(200), default="")


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
    vacuum = False
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
        if "logo" not in {c["name"] for c in inspect(engine).get_columns("firms")}:
            con.execute(text("ALTER TABLE firms ADD COLUMN logo VARCHAR(60) DEFAULT ''"))
        icols = {c["name"] for c in inspect(engine).get_columns("integrations")}
        if "stock_synced_at" not in icols:
            con.execute(text("ALTER TABLE integrations ADD COLUMN stock_synced_at DATETIME"))
        for tbl, col, typ in (("orders", "commission", "FLOAT DEFAULT 0"), ("orders", "cost", "FLOAT DEFAULT 0"),
                              ("order_items", "cost", "FLOAT DEFAULT 0"), ("order_items", "discount", "FLOAT DEFAULT 0"), ("order_items", "campaign_id", "VARCHAR(64) DEFAULT ''"),
                              ("order_items", "campaign", "VARCHAR(200) DEFAULT ''"), ("order_items", "brand", "VARCHAR(120) DEFAULT ''"),
                              ("order_items", "category", "VARCHAR(200) DEFAULT ''"), ("variant_stock", "brand", "VARCHAR(120) DEFAULT ''"),
                              ("variant_stock", "category", "VARCHAR(200) DEFAULT ''")):
            if col not in {c["name"] for c in inspect(engine).get_columns(tbl)}:
                con.execute(text(f"ALTER TABLE {tbl} ADD COLUMN {col} {typ}"))
        if "backfill_from" not in icols:
            con.execute(text("ALTER TABLE integrations ADD COLUMN backfill_from DATE"))
        if not con.execute(text("SELECT 1 FROM meta WHERE key = 'marka_kategori_kampanya'")).first():
            # marka/kategori için stoklar hemen, kampanya bilgisi için son 60 gün yeniden çekilir
            con.execute(text("UPDATE integrations SET stock_synced_at = NULL WHERE kind IN ('ticimax', 'shopify')"))
            # ilk senkronu hâlâ süren bağlantılar (ör. Sabri Özel) baştan başlatılmaz
            con.execute(text("UPDATE integrations SET backfilled = 0, backfill_from = NULL "
                             "WHERE kind IN ('ticimax', 'shopify') AND backfilled = 1"))
            con.execute(text("INSERT INTO meta (key) VALUES ('marka_kategori_kampanya')"))
        if not con.execute(text("SELECT 1 FROM meta WHERE key = 'karlilik'")).first():
            # komisyon ve maliyet için son 60 gün yeniden çekilir (en yeni günler önce gelir)
            con.execute(text("UPDATE integrations SET backfilled = 0, backfill_from = NULL "
                             "WHERE kind IN ('ticimax', 'shopify') AND backfilled = 1"))
            con.execute(text("INSERT INTO meta (key) VALUES ('karlilik')"))
        if not con.execute(text("SELECT 1 FROM meta WHERE key = 'odemesiz_ozet'")).first():
            # ödemesi tamamlanmayan siparişler saatlik özete taşınır, satırları silinir (veritabanı küçülür)
            con.execute(text("""
                INSERT INTO unpaid_hourly (firm_id, integration_id, hour, channel, payment_status, count, amount)
                SELECT firm_id, integration_id, strftime('%Y-%m-%d %H:00:00.000000', created_at), channel, payment_status,
                       COUNT(*), SUM(total)
                FROM orders WHERE is_cancelled = 1 AND payment_status != ''
                GROUP BY firm_id, integration_id, strftime('%Y-%m-%d %H', created_at), channel, payment_status"""))
            con.execute(text("DELETE FROM order_items WHERE order_id IN "
                             "(SELECT id FROM orders WHERE is_cancelled = 1 AND payment_status != '')"))
            con.execute(text("DELETE FROM orders WHERE is_cancelled = 1 AND payment_status != ''"))
            con.execute(text("INSERT INTO meta (key) VALUES ('odemesiz_ozet')"))
            vacuum = True
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
        if not con.execute(text("SELECT 1 FROM meta WHERE key = 'logo_ilk'")).first():
            # firmaların sitelerindeki logolar: ilk açılışta /logo adresi bunları bir kez indirip saklar
            for pat, url in (("%ssa%", "https://static.ticimax.cloud/53344//uploads/images/logo-red.svg"),
                             ("%sabri%", "https://static.ticimax.cloud/66307/Uploads/HeaderTasarim/Header2/40631ce4-b803-464e-85d9-49cf46e2debe.jpg"),
                             ("%360%", "https://360koruma.com/cdn/shop/files/logo-01_90860a31-768f-4a98-80e6-c89691adb864.png?v=1784187132&width=400")):
                con.execute(text("UPDATE firms SET logo = :u WHERE lower(name) LIKE :p AND (logo IS NULL OR logo = '')"),
                            {"u": url, "p": pat})
            con.execute(text("INSERT INTO meta (key) VALUES ('logo_ilk')"))
    if vacuum and config.DATABASE_URL.startswith("sqlite"):
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as con:
            con.execute(text("VACUUM"))
