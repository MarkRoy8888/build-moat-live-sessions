"""Index strategy management. Lets the playground swap indexes at runtime
to demonstrate Q3 (compound / partial / covering) trade-offs.

Drops all custom inventory indexes, then creates the one matching the
current Settings.index_strategy. Schema-affecting, run inside an admin handler.
"""

from sqlalchemy import text
from sqlalchemy.engine import Engine

CUSTOM_INDEX_NAMES = (
    "idx_inv_simple",
    "idx_inv_compound",
    "idx_inv_covering",
    "idx_inv_partial",
    "idx_inv_killer",
)


def drop_all_custom_indexes(engine: Engine) -> None:
    with engine.begin() as conn:
        for name in CUSTOM_INDEX_NAMES:
            conn.execute(text(f"DROP INDEX IF EXISTS {name}"))


def apply_index_strategy(engine: Engine, strategy: str) -> None:
    drop_all_custom_indexes(engine)
    if strategy != "none":
        with engine.begin() as conn:
            if strategy == "simple":
                conn.execute(text("CREATE INDEX idx_inv_simple ON inventory(city)"))
            elif strategy == "compound":
                conn.execute(
                    text("CREATE INDEX idx_inv_compound ON inventory(city, date, status)")
                )
            elif strategy == "covering":
                conn.execute(
                    text(
                        "CREATE INDEX idx_inv_covering "
                        "ON inventory(city, date, status, home_id)"
                    )
                )
            elif strategy == "partial":
                # Partial only — does NOT include home_id, so SELECT home_id
                # still requires a 回表 step to fetch home_id from main table.
                conn.execute(
                    text(
                        "CREATE INDEX idx_inv_partial ON inventory(city, date) "
                        "WHERE status = 'available'"
                    )
                )
            elif strategy == "killer":
                # Partial + Covering combo: includes home_id (covering) AND
                # only indexes available rows (partial). Smallest index, no 回表.
                conn.execute(
                    text(
                        "CREATE INDEX idx_inv_killer ON inventory(city, date, home_id) "
                        "WHERE status = 'available'"
                    )
                )
            else:
                raise ValueError(f"unknown strategy: {strategy}")
    # Dispose connection pool so subsequent queries pick up schema changes.
    # SQLite caches prepared statements per-connection; without dispose, an
    # EXPLAIN can still report a dropped index.
    engine.dispose()


def explain_search(engine: Engine, city: str, start: str, end: str) -> list[str]:
    sql = text(
        """
        EXPLAIN QUERY PLAN
        SELECT home_id FROM inventory
        WHERE city = :city
          AND date BETWEEN :start AND :end
          AND status = 'available'
        GROUP BY home_id
        HAVING COUNT(*) = :nights
        """
    )
    nights = (
        (int(end[5:7]) - int(start[5:7])) * 30
        + (int(end[8:10]) - int(start[8:10]))
        + 1
    )
    with engine.connect() as conn:
        rows = conn.execute(
            sql, {"city": city, "start": start, "end": end, "nights": nights}
        ).fetchall()
    return [" | ".join(str(c) for c in row) for row in rows]
