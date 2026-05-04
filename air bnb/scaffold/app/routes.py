from datetime import date, datetime
from time import perf_counter

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from .booking import cancel_booking, confirm_payment, finalize_booking, try_reserve
from .cache import home_cache
from .database import engine, get_db
from .indexes import apply_index_strategy, explain_search
from .models import Booking, Home, Inventory
from .schemas import (
    BenchResponse,
    BookRequest,
    BookResponse,
    CancelResponse,
    ConfirmRequest,
    ConfirmResponse,
    ExplainResponse,
    HomeDetailResponse,
    HomeOut,
    SearchHit,
    SearchResponse,
    SettingsResponse,
    SettingsUpdate,
)
from .search import search_homes
from .seed import reset as seed_reset
from .seed import seed_large, seed_small
from .settings import settings

router = APIRouter()


@router.get("/home/search", response_model=SearchResponse)
def http_search(
    city: str,
    startDate: date,
    endDate: date,
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    if endDate < startDate:
        raise HTTPException(422, "endDate must be >= startDate")

    t0 = perf_counter()
    homes, total = search_homes(db, city, startDate, endDate, page, pageSize)
    elapsed_ms = (perf_counter() - t0) * 1000

    return SearchResponse(
        results=[
            SearchHit(
                home_id=h.id,
                city=h.city,
                address=h.address,
                type=h.type,
                amenities=h.amenities or [],
            )
            for h in homes
        ],
        total=total,
        page=page,
        page_size=pageSize,
        elapsed_ms=round(elapsed_ms, 3),
        cache_hit=False,
        index_strategy=settings.index_strategy,
    )


@router.get("/home/{home_id}", response_model=HomeDetailResponse)
def http_get_home(home_id: str, db: Session = Depends(get_db)):
    t0 = perf_counter()

    if settings.cache_home_detail:
        cached = home_cache.get(home_id)
        if cached is not None:
            elapsed_ms = (perf_counter() - t0) * 1000
            return HomeDetailResponse(
                home=HomeOut(**cached),
                elapsed_ms=round(elapsed_ms, 3),
                cache_hit=True,
            )

    home = db.get(Home, home_id)
    if home is None:
        raise HTTPException(404, "Home not found")

    payload = {
        "id": home.id,
        "city": home.city,
        "address": home.address,
        "type": home.type,
        "amenities": home.amenities or [],
    }
    if settings.cache_home_detail:
        home_cache.set(home_id, payload)

    elapsed_ms = (perf_counter() - t0) * 1000
    return HomeDetailResponse(
        home=HomeOut(**payload),
        elapsed_ms=round(elapsed_ms, 3),
        cache_hit=False,
    )


@router.post("/home/book", response_model=BookResponse)
def http_book(
    req: BookRequest,
    delay_ms: int = Query(
        0,
        ge=0,
        le=2000,
        description=(
            "DEMO ONLY. Inserts an artificial sleep between SELECT and UPDATE "
            "in naive concurrency mode, to widen the race window so two "
            "concurrent reservations reliably collide. Has no effect in "
            "logical mode. Real production should never set this."
        ),
    ),
    db: Session = Depends(get_db),
):
    home = db.get(Home, req.home_id)
    if home is None:
        raise HTTPException(404, "Home not found")

    t0 = perf_counter()
    booking, reason = try_reserve(
        db,
        req.home_id,
        req.user_id,
        req.start_date,
        req.end_date,
        artificial_delay_ms=delay_ms,
    )
    elapsed_ms = (perf_counter() - t0) * 1000

    if booking is None:
        return BookResponse(
            booking_id="",
            status="rejected",
            expires_at=None,
            elapsed_ms=round(elapsed_ms, 3),
            concurrency_mode=settings.concurrency_mode,
            detail=reason,
        )

    return BookResponse(
        booking_id=booking.id,
        status=booking.status,
        expires_at=booking.expires_at,
        elapsed_ms=round(elapsed_ms, 3),
        concurrency_mode=settings.concurrency_mode,
        detail=reason,
    )


@router.post("/home/book/{booking_id}/confirm", response_model=ConfirmResponse)
def http_confirm(
    booking_id: str,
    req: ConfirmRequest | None = None,
    db: Session = Depends(get_db),
):
    t0 = perf_counter()
    idem = req.idempotency_key if req else None
    booking, msg, replay = confirm_payment(db, booking_id, idem)
    elapsed_ms = (perf_counter() - t0) * 1000

    if booking is None:
        raise HTTPException(404, msg)

    return ConfirmResponse(
        booking_id=booking.id,
        status=booking.status,
        elapsed_ms=round(elapsed_ms, 3),
        idempotent_replay=replay,
    )


@router.post("/home/book/{booking_id}/finalize", response_model=ConfirmResponse)
def http_finalize(booking_id: str, db: Session = Depends(get_db)):
    """Step 2: paid -> booked. Internal transaction, no idempotency key needed."""
    t0 = perf_counter()
    booking, msg = finalize_booking(db, booking_id)
    elapsed_ms = (perf_counter() - t0) * 1000

    if booking is None:
        raise HTTPException(404, msg)

    return ConfirmResponse(
        booking_id=booking.id,
        status=booking.status,
        elapsed_ms=round(elapsed_ms, 3),
        idempotent_replay=False,
    )


@router.post("/home/book/{booking_id}/cancel", response_model=CancelResponse)
def http_cancel(booking_id: str, db: Session = Depends(get_db)):
    t0 = perf_counter()
    booking, msg = cancel_booking(db, booking_id)
    elapsed_ms = (perf_counter() - t0) * 1000

    if booking is None:
        raise HTTPException(404, msg)

    return CancelResponse(
        booking_id=booking.id,
        status=booking.status,
        elapsed_ms=round(elapsed_ms, 3),
    )


@router.get("/api/bookings")
def http_list_bookings(db: Session = Depends(get_db)):
    bookings = (
        db.query(Booking).order_by(Booking.created_at.desc()).limit(50).all()
    )
    return [
        {
            "id": b.id,
            "home_id": b.home_id,
            "user_id": b.user_id,
            "start_date": str(b.start_date),
            "end_date": str(b.end_date),
            "status": b.status,
            "expires_at": b.expires_at.isoformat() if b.expires_at else None,
            "created_at": b.created_at.isoformat() if b.created_at else None,
        }
        for b in bookings
    ]


@router.get("/api/homes")
def http_list_homes_by_city(city: str, db: Session = Depends(get_db)):
    """List all homes in a city, regardless of availability.

    Q4/Q5/Q1 home dropdowns need to show every house even after some get
    reserved, otherwise the UI runs out of options after a few demo runs.
    """
    rows = (
        db.query(Home).filter(Home.city == city).order_by(Home.id).limit(200).all()
    )
    return [
        {"home_id": h.id, "city": h.city, "address": h.address, "type": h.type}
        for h in rows
    ]


@router.get("/api/inventory/{home_id}")
def http_inventory_for_home(home_id: str, db: Session = Depends(get_db)):
    """Peek inventory rows for a home (debug / UI)."""
    rows = (
        db.query(Inventory)
        .filter(Inventory.home_id == home_id)
        .order_by(Inventory.date)
        .limit(60)
        .all()
    )
    return [
        {
            "date": str(r.date),
            "status": r.status,
            "holder": r.holder,
            "expires_at": r.expires_at.isoformat() if r.expires_at else None,
        }
        for r in rows
    ]


@router.get("/api/settings", response_model=SettingsResponse)
def http_get_settings():
    return SettingsResponse(**settings.to_dict())


@router.patch("/api/settings", response_model=SettingsResponse)
def http_update_settings(req: SettingsUpdate, db: Session = Depends(get_db)):
    payload = {k: v for k, v in req.model_dump().items() if v is not None}
    prev_strategy = settings.index_strategy
    try:
        settings.update(**payload)
    except ValueError as e:
        raise HTTPException(422, str(e))

    if (
        "index_strategy" in payload
        and payload["index_strategy"] != prev_strategy
    ):
        apply_index_strategy(engine, settings.index_strategy)

    if "cache_home_detail" in payload and not payload["cache_home_detail"]:
        home_cache.clear()

    return SettingsResponse(**settings.to_dict())


@router.get("/api/cache/stats")
def http_cache_stats():
    return home_cache.stats()


@router.post("/api/cache/clear")
def http_cache_clear():
    home_cache.clear()
    return {"cleared": True}


@router.get("/api/explain", response_model=ExplainResponse)
def http_explain(
    city: str = "Honolulu",
    startDate: date | None = None,
    endDate: date | None = None,
):
    today = date.today()
    s = startDate or today
    e = endDate or today
    plan = explain_search(engine, city, str(s), str(e))
    sql = (
        "EXPLAIN QUERY PLAN\n"
        "SELECT home_id FROM inventory\n"
        f"WHERE city = '{city}'\n"
        f"  AND date BETWEEN '{s}' AND '{e}'\n"
        "  AND status = 'available'\n"
        "GROUP BY home_id HAVING COUNT(*) = (julianday(:end)-julianday(:start)+1)"
    )
    return ExplainResponse(
        sql=sql, plan=plan, index_strategy=settings.index_strategy
    )


@router.post("/api/admin/seed")
def http_seed(scale: str = "small", db: Session = Depends(get_db)):
    if scale == "small":
        result = seed_small(db)
    elif scale == "large":
        seed_reset(db)
        result = seed_large(db)
    else:
        raise HTTPException(422, "scale must be 'small' or 'large'")
    apply_index_strategy(engine, settings.index_strategy)
    return result


@router.post("/api/admin/reset")
def http_reset(db: Session = Depends(get_db)):
    home_cache.clear()
    return seed_reset(db)


@router.post("/api/bench", response_model=BenchResponse)
def http_bench(runs: int = 30, nights: int = 5, db: Session = Depends(get_db)):
    from .bench import run_benchmark

    results = run_benchmark(
        db,
        runs=runs,
        nights=nights,
        restore_strategy=settings.index_strategy,
    )
    return BenchResponse(results=results)


@router.post("/api/q1/compare")
def http_q1_compare(
    city: str = "Honolulu",
    nights: int = 5,
    runs: int = 50,
    db: Session = Depends(get_db),
):
    """Compare per-day rows vs range-table query, both targeting 'find homes
    available for N consecutive nights in :city'.

    Per-day: hits the inventory table with a clean range scan + GROUP BY.
    Range: hits the bookings table with a NOT EXISTS overlap subquery.
    """
    from datetime import date, timedelta
    from time import perf_counter

    today = date.today()
    start_date = today
    end_date = today + timedelta(days=nights - 1)

    bookings_count = db.execute(text("SELECT COUNT(*) FROM bookings")).scalar() or 0
    seeded_extra = 0
    if bookings_count < 30:
        rng_seed = 13
        homes = db.execute(
            text("SELECT id FROM homes WHERE city = :city LIMIT 50"),
            {"city": city},
        ).fetchall()
        if homes:
            from random import Random
            from secrets import token_hex
            from datetime import datetime as dt
            rng = Random(rng_seed)
            for _ in range(50):
                h = rng.choice(homes)[0]
                offset = rng.randint(20, 28)
                length = rng.randint(2, 5)
                s = today + timedelta(days=offset)
                e = s + timedelta(days=length - 1)
                bid = "FAKE_" + token_hex(6)
                try:
                    db.execute(
                        text(
                            "INSERT INTO bookings (id, home_id, user_id, start_date, end_date, status, created_at) "
                            "VALUES (:id, :h, 'demo', :s, :e, 'booked', :now)"
                        ),
                        {"id": bid, "h": h, "s": s, "e": e, "now": dt.utcnow()},
                    )
                    seeded_extra += 1
                except Exception:
                    db.rollback()
            db.commit()

    per_day_sql = text(
        """
        SELECT home_id FROM inventory
        WHERE city = :city
          AND date BETWEEN :start AND :end
          AND status = 'available'
        GROUP BY home_id
        HAVING COUNT(*) = :nights
        """
    )
    range_sql = text(
        """
        SELECT h.id FROM homes h
        WHERE h.city = :city
          AND NOT EXISTS (
            SELECT 1 FROM bookings b
            WHERE b.home_id = h.id
              AND b.status IN ('reserved', 'paid', 'booked')
              AND b.start_date <= :end
              AND b.end_date >= :start
          )
        """
    )

    params = {
        "city": city,
        "start": start_date,
        "end": end_date,
        "nights": nights,
    }

    db.execute(per_day_sql, params).fetchall()
    db.execute(range_sql, params).fetchall()

    per_day_times = []
    for _ in range(runs):
        t0 = perf_counter()
        per_day_rows = db.execute(per_day_sql, params).fetchall()
        per_day_times.append((perf_counter() - t0) * 1000)

    range_times = []
    for _ in range(runs):
        t0 = perf_counter()
        range_rows = db.execute(range_sql, params).fetchall()
        range_times.append((perf_counter() - t0) * 1000)

    def stats(times: list[float]) -> dict:
        return {
            "avg_ms": round(sum(times) / len(times), 4),
            "min_ms": round(min(times), 4),
            "max_ms": round(max(times), 4),
            "total_ms": round(sum(times), 2),
        }

    per_day_stats = stats(per_day_times)
    range_stats = stats(range_times)
    winner = "per_day" if per_day_stats["avg_ms"] <= range_stats["avg_ms"] else "range"
    ratio = round(
        max(per_day_stats["avg_ms"], range_stats["avg_ms"])
        / max(min(per_day_stats["avg_ms"], range_stats["avg_ms"]), 0.0001),
        2,
    )

    return {
        "city": city,
        "start": str(start_date),
        "end": str(end_date),
        "nights": nights,
        "runs_per_query": runs,
        "bookings_in_db": bookings_count + seeded_extra,
        "seeded_demo_bookings": seeded_extra,
        "per_day": {
            **per_day_stats,
            "result_count": len(per_day_rows),
            "sql": per_day_sql.text.strip(),
        },
        "range": {
            **range_stats,
            "result_count": len(range_rows),
            "sql": range_sql.text.strip(),
        },
        "winner": winner,
        "ratio": ratio,
    }


@router.get("/api/stats")
def http_stats(db: Session = Depends(get_db)):
    homes = db.execute(text("SELECT COUNT(*) FROM homes")).scalar()
    inv = db.execute(text("SELECT COUNT(*) FROM inventory")).scalar()
    bookings = db.execute(text("SELECT COUNT(*) FROM bookings")).scalar()
    return {"homes": homes, "inventory_rows": inv, "bookings": bookings}
