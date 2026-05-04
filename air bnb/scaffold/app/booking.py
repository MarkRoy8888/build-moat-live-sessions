"""Booking concurrency control.

Two modes for didactic comparison:
- 'logical' : conditional UPDATE with logical-availability check (recommended,
              matches Q2 option (c)). Atomic, no DB lock across human time.
- 'naive'   : SELECT-then-UPDATE, exposing the race condition. Demonstrates
              why the naive approach allows double booking.
"""

from datetime import date, datetime, timedelta
from secrets import token_hex
from time import sleep

from sqlalchemy import text
from sqlalchemy.exc import OperationalError, IntegrityError
from sqlalchemy.orm import Session

from .models import Booking, WebhookEvent
from .settings import settings


def _nights(start: date, end: date) -> int:
    return (end - start).days + 1


def try_reserve(
    db: Session,
    home_id: str,
    user_id: str,
    start_date: date,
    end_date: date,
    artificial_delay_ms: int = 0,
) -> tuple[Booking | None, str]:
    """Attempt to reserve a home for the given date range.

    Returns (booking, reason). booking is None on failure, reason explains why.
    """
    nights = _nights(start_date, end_date)
    if nights <= 0:
        return None, "Invalid date range"

    ttl = settings.reservation_ttl_seconds
    now = datetime.utcnow()
    expires_at = now + timedelta(seconds=ttl)
    booking_id = "B_" + token_hex(8)

    try:
        if settings.concurrency_mode == "naive":
            return _reserve_naive(
                db,
                home_id,
                user_id,
                start_date,
                end_date,
                nights,
                now,
                expires_at,
                booking_id,
                artificial_delay_ms,
            )
        return _reserve_logical(
            db,
            home_id,
            user_id,
            start_date,
            end_date,
            nights,
            now,
            expires_at,
            booking_id,
        )
    except (OperationalError, IntegrityError) as e:
        db.rollback()
        return None, f"DB conflict (concurrent write): {type(e).__name__}"


def _reserve_logical(
    db, home_id, user_id, start_date, end_date, nights, now, expires_at, booking_id
):
    """Conditional UPDATE: atomic, race-free. The recommended path."""
    update_sql = text(
        """
        UPDATE inventory
        SET status = 'reserved',
            holder = :holder,
            expires_at = :expires_at
        WHERE home_id = :home_id
          AND date BETWEEN :start AND :end
          AND (
            status = 'available'
            OR (status = 'reserved' AND expires_at < :now)
          )
        """
    )
    result = db.execute(
        update_sql,
        {
            "holder": booking_id,
            "expires_at": expires_at,
            "home_id": home_id,
            "start": start_date,
            "end": end_date,
            "now": now,
        },
    )

    if result.rowcount != nights:
        db.rollback()
        return None, f"Dates not fully available (got {result.rowcount}/{nights} nights)"

    booking = Booking(
        id=booking_id,
        home_id=home_id,
        user_id=user_id,
        start_date=start_date,
        end_date=end_date,
        status="reserved",
        created_at=now,
        expires_at=expires_at,
    )
    db.add(booking)
    db.commit()
    db.refresh(booking)
    return booking, "OK"


def _reserve_naive(
    db,
    home_id,
    user_id,
    start_date,
    end_date,
    nights,
    now,
    expires_at,
    booking_id,
    artificial_delay_ms: int,
):
    """SELECT-then-UPDATE: demonstrates race condition.

    artificial_delay_ms is inserted between SELECT and UPDATE so two concurrent
    naive requests reliably collide in the playground.
    """
    select_sql = text(
        """
        SELECT id, status, expires_at
        FROM inventory
        WHERE home_id = :home_id
          AND date BETWEEN :start AND :end
        """
    )
    rows = db.execute(
        select_sql,
        {"home_id": home_id, "start": start_date, "end": end_date},
    ).fetchall()

    if len(rows) != nights:
        db.rollback()
        return None, f"Inventory rows missing ({len(rows)}/{nights})"

    for r in rows:
        is_available = r.status == "available"
        exp = r.expires_at
        if isinstance(exp, str):
            try:
                exp = datetime.fromisoformat(exp)
            except ValueError:
                exp = None
        is_expired_reserved = r.status == "reserved" and exp is not None and exp < now
        if not (is_available or is_expired_reserved):
            db.rollback()
            return None, "Dates not available"

    if artificial_delay_ms > 0:
        sleep(artificial_delay_ms / 1000)

    update_sql = text(
        """
        UPDATE inventory
        SET status = 'reserved',
            holder = :holder,
            expires_at = :expires_at
        WHERE home_id = :home_id
          AND date BETWEEN :start AND :end
        """
    )
    db.execute(
        update_sql,
        {
            "holder": booking_id,
            "expires_at": expires_at,
            "home_id": home_id,
            "start": start_date,
            "end": end_date,
        },
    )

    booking = Booking(
        id=booking_id,
        home_id=home_id,
        user_id=user_id,
        start_date=start_date,
        end_date=end_date,
        status="reserved",
        created_at=now,
        expires_at=expires_at,
    )
    db.add(booking)
    db.commit()
    db.refresh(booking)
    return booking, "OK (naive — no race protection)"


def confirm_payment(
    db: Session,
    booking_id: str,
    idempotency_key: str | None,
) -> tuple[Booking | None, str, bool]:
    """Step 1 of Q4 checkpoint: Stripe webhook arrived, money received.

    Transition: reserved -> paid (single-row conditional UPDATE).
    Inventory rows are NOT yet flipped to 'booked' — that's finalize_booking()'s job.
    This gap is intentional: 'paid' is the cross-system checkpoint that says
    'I acknowledge the external charge', distinct from 'booked' which says
    'my internal inventory is updated'.
    """
    if settings.idempotency_enforced and idempotency_key:
        existing = db.get(WebhookEvent, idempotency_key)
        if existing:
            booking = db.get(Booking, existing.booking_id)
            return booking, "Idempotent replay (event already processed)", True

    booking = db.get(Booking, booking_id)
    if booking is None:
        return None, "Booking not found", False
    if booking.status == "paid":
        return booking, "Already paid (call /finalize to mark booked)", False
    if booking.status == "booked":
        return booking, "Already booked", False
    if booking.status != "reserved":
        return booking, f"Cannot pay from status={booking.status}", False

    update_sql = text(
        """
        UPDATE bookings
        SET status = 'paid',
            stripe_event_id = :event_id
        WHERE id = :id AND status = 'reserved'
        """
    )
    result = db.execute(
        update_sql, {"event_id": idempotency_key, "id": booking_id}
    )
    if result.rowcount == 0:
        db.refresh(booking)
        return booking, "Concurrent confirm — already advanced", False
    db.commit()
    db.refresh(booking)

    if settings.idempotency_enforced and idempotency_key:
        try:
            db.add(
                WebhookEvent(
                    event_id=idempotency_key,
                    booking_id=booking_id,
                    result={"status": "paid"},
                )
            )
            db.commit()
        except Exception:
            db.rollback()

    return booking, "Paid (Stripe ack recorded). Inventory still 'reserved' until finalize.", False


def finalize_booking(
    db: Session, booking_id: str
) -> tuple[Booking | None, str]:
    """Step 2 of Q4 checkpoint: internal transaction to flip inventory + booking
    from paid -> booked. Pure internal atomic operation, no external dependency.

    If this step fails (DB error, etc.), a recovery cron can find rows where
    booking.status='paid' but inventory.status still 'reserved', and re-run.
    """
    booking = db.get(Booking, booking_id)
    if booking is None:
        return None, "Booking not found"
    if booking.status == "booked":
        return booking, "Already booked"
    if booking.status != "paid":
        return booking, (
            f"Cannot finalize from status={booking.status}. "
            "Must call /confirm first to mark paid."
        )

    finalize_sql = text(
        """
        UPDATE inventory
        SET status = 'booked'
        WHERE home_id = :home_id
          AND date BETWEEN :start AND :end
          AND holder = :holder
        """
    )
    db.execute(
        finalize_sql,
        {
            "home_id": booking.home_id,
            "start": booking.start_date,
            "end": booking.end_date,
            "holder": booking.id,
        },
    )
    db.execute(
        text("UPDATE bookings SET status = 'booked' WHERE id = :id"),
        {"id": booking_id},
    )
    db.commit()
    db.refresh(booking)
    return booking, "Booked (inventory finalized)"


def cancel_booking(
    db: Session, booking_id: str
) -> tuple[Booking | None, str]:
    booking = db.get(Booking, booking_id)
    if booking is None:
        return None, "Booking not found"
    if booking.status == "booked":
        return booking, "Cannot cancel a booked reservation (would need refund flow)"
    if booking.status == "cancelled":
        return booking, "Already cancelled"

    db.execute(
        text(
            """
            UPDATE inventory
            SET status = 'available', holder = NULL, expires_at = NULL
            WHERE home_id = :home_id
              AND date BETWEEN :start AND :end
              AND holder = :holder
            """
        ),
        {
            "home_id": booking.home_id,
            "start": booking.start_date,
            "end": booking.end_date,
            "holder": booking.id,
        },
    )
    booking.status = "cancelled"
    db.commit()
    db.refresh(booking)
    return booking, "Cancelled"
