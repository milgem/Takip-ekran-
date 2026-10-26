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


def init_db():
    Base.metadata.create_all(engine)
    _migrate()


def _migrate():
    """Eski kurulumlara yeni sütunları ekler (veri kaybı olmadan)."""
    from sqlalchemy import inspect, text
    cols = {c["name"] for c in inspect(engine).get_columns("orders")}
    with engine.begin() as con:
        if "channel" not in cols:
            con.execute(text("ALTER TABLE orders ADD COLUMN channel VARCHAR(40) DEFAULT 'Web sitesi'"))
            con.execute(text("CREATE INDEX IF NOT EXISTS ix_orders_channel ON orders (channel)"))
