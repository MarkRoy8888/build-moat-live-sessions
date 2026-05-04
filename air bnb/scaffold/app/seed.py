"""Seed homes + per-day inventory rows.

Two scales:
- small (default, fast boot): 50 homes x 30 days = 1.5K rows. Good for UI play.
- large (benchmark): 10K homes x 365 days = 3.65M rows. For Q3 EXPLAIN demos.

Boots the small scale automatically on first start. Large scale via
POST /api/admin/seed?scale=large.
"""

from datetime import date, timedelta
from random import Random

from sqlalchemy import text
from sqlalchemy.orm import Session

from .database import engine
from .models import Home, Inventory

CITIES = [
    "Honolulu",
    "Tokyo",
    "Taipei",
    "Bangkok",
    "Bali",
    "Paris",
    "Lisbon",
    "Barcelona",
    "Reykjavik",
    "Cape Town",
]
TYPES = ["entire_home", "private_room", "shared_room"]
AMENITIES_POOL = [
    "wifi",
    "kitchen",
    "washer",
    "ac",
    "pool",
    "hot_tub",
    "parking",
    "tv",
    "workspace",
    "pets_ok",
]


def _rng_amenities(rng: Random) -> list[str]:
    n = rng.randint(2, 6)
    return rng.sample(AMENITIES_POOL, n)


def has_data(db: Session) -> bool:
    return db.query(Home).limit(1).first() is not None


def seed_small(db: Session, *, days_ahead: int = 30, homes_per_city: int = 5):
    """Small seed for the interactive UI."""
    if has_data(db):
        return {"skipped": True, "reason": "already seeded"}

    rng = Random(42)
    homes_created = 0
    today = date.today()

    homes_to_add = []
    inv_rows = []

    for city in CITIES:
        for i in range(homes_per_city):
            home_id = f"H_{city[:3].upper()}_{i:03d}"
            homes_to_add.append(
                Home(
                    id=home_id,
                    city=city,
                    address=f"{rng.randint(1, 999)} {city} St.",
                    type=rng.choice(TYPES),
                    amenities=_rng_amenities(rng),
                )
            )
            for d in range(days_ahead):
                inv_rows.append(
                    {
                        "home_id": home_id,
                        "city": city,
                        "date": today + timedelta(days=d),
                        "status": "available",
                    }
                )
            homes_created += 1

    db.add_all(homes_to_add)
    db.commit()

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO inventory (home_id, city, date, status) "
                "VALUES (:home_id, :city, :date, :status)"
            ),
            inv_rows,
        )

    return {"homes": homes_created, "inventory_rows": len(inv_rows)}


def seed_large(db: Session, *, days_ahead: int = 365, homes_per_city: int = 1000):
    """Large seed for benchmark / EXPLAIN demonstrations.

    1000 homes x 10 cities x 365 days = 3.65M inventory rows. ~30s to seed.
    """
    rng = Random(7)
    today = date.today()

    homes_to_add = []
    for city in CITIES:
        for i in range(homes_per_city):
            home_id = f"L_{city[:3].upper()}_{i:05d}"
            homes_to_add.append(
                Home(
                    id=home_id,
                    city=city,
                    address=f"{rng.randint(1, 9999)} {city} Blvd.",
                    type=rng.choice(TYPES),
                    amenities=_rng_amenities(rng),
                )
            )

    db.add_all(homes_to_add)
    db.commit()

    home_count = len(homes_to_add)
    batch_size = 5000
    inserted = 0

    with engine.begin() as conn:
        for home in homes_to_add:
            rows = [
                {
                    "home_id": home.id,
                    "city": home.city,
                    "date": today + timedelta(days=d),
                    "status": "available",
                }
                for d in range(days_ahead)
            ]
            for i in range(0, len(rows), batch_size):
                chunk = rows[i : i + batch_size]
                conn.execute(
                    text(
                        "INSERT INTO inventory (home_id, city, date, status) "
                        "VALUES (:home_id, :city, :date, :status)"
                    ),
                    chunk,
                )
                inserted += len(chunk)

    return {"homes": home_count, "inventory_rows": inserted}


def reset(db: Session):
    """Wipe all data. Used for benchmark resets."""
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM webhook_events"))
        conn.execute(text("DELETE FROM bookings"))
        conn.execute(text("DELETE FROM inventory"))
        conn.execute(text("DELETE FROM homes"))
    return {"reset": True}
