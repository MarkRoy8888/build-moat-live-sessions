"""Search query implementation. Single SQL that the active index strategy
either accelerates or doesn't — the query itself stays the same."""

from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from .models import Home


def _nights(start: date, end: date) -> int:
    return (end - start).days + 1


def search_homes(
    db: Session,
    city: str,
    start_date: date,
    end_date: date,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Home], int]:
    """Find homes in `city` available for ALL nights in [start_date, end_date].

    Strategy: per-day rows + GROUP BY HAVING COUNT == nights. This forces the
    query to use the (city, date, status) compound index when present.
    """
    nights = _nights(start_date, end_date)
    offset = (page - 1) * page_size

    sql = text(
        """
        SELECT home_id
        FROM inventory
        WHERE city = :city
          AND date BETWEEN :start AND :end
          AND status = 'available'
        GROUP BY home_id
        HAVING COUNT(*) = :nights
        ORDER BY home_id
        LIMIT :limit OFFSET :offset
        """
    )
    rows = db.execute(
        sql,
        {
            "city": city,
            "start": start_date,
            "end": end_date,
            "nights": nights,
            "limit": page_size,
            "offset": offset,
        },
    ).fetchall()

    home_ids = [r[0] for r in rows]
    if not home_ids:
        return [], 0

    homes = db.query(Home).filter(Home.id.in_(home_ids)).all()
    homes_by_id = {h.id: h for h in homes}
    ordered = [homes_by_id[hid] for hid in home_ids if hid in homes_by_id]

    count_sql = text(
        """
        SELECT COUNT(*) FROM (
          SELECT home_id
          FROM inventory
          WHERE city = :city
            AND date BETWEEN :start AND :end
            AND status = 'available'
          GROUP BY home_id
          HAVING COUNT(*) = :nights
        )
        """
    )
    total = db.execute(
        count_sql,
        {"city": city, "start": start_date, "end": end_date, "nights": nights},
    ).scalar() or 0

    return ordered, total
