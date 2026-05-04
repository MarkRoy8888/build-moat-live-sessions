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
def http_book(req: BookRequest, db: Session = Depends(get_db)):
    home = db.get(Home, req.home_id)
    if home is None:
        raise HTTPException(404, "Home not found")

    t0 = perf_counter()
    booking, reason = try_reserve(
        db, req.home_id, req.user_id, req.start_date, req.end_date
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


@router.get("/api/stats")
def http_stats(db: Session = Depends(get_db)):
    homes = db.execute(text("SELECT COUNT(*) FROM homes")).scalar()
    inv = db.execute(text("SELECT COUNT(*) FROM inventory")).scalar()
    bookings = db.execute(text("SELECT COUNT(*) FROM bookings")).scalar()
    return {"homes": homes, "inventory_rows": inv, "bookings": bookings}
