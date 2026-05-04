from datetime import date as date_t
from datetime import datetime

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class Home(Base):
    __tablename__ = "homes"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    city: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    address: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    amenities: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Inventory(Base):
    """Per-day-per-home availability (Q1: per-day rows model)."""

    __tablename__ = "inventory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    home_id: Mapped[str] = mapped_column(String(16), ForeignKey("homes.id"), nullable=False)
    city: Mapped[str] = mapped_column(String(64), nullable=False)
    date: Mapped[date_t] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="available")
    holder: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("uq_inventory_home_date", "home_id", "date", unique=True),
    )


class Booking(Base):
    __tablename__ = "bookings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    home_id: Mapped[str] = mapped_column(String(16), ForeignKey("homes.id"), nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    start_date: Mapped[date_t] = mapped_column(Date, nullable=False)
    end_date: Mapped[date_t] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="reserved")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    stripe_event_id: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)


class WebhookEvent(Base):
    """Idempotency table (Q4)."""

    __tablename__ = "webhook_events"

    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    booking_id: Mapped[str] = mapped_column(String(32), nullable=False)
    processed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
