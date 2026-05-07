"""Benchmark: run search query N times under different index strategies,
report per-strategy avg/min/max ms.

Used by the UI to power 'Run Benchmark' button — visible ms differences
even on small data, dramatic differences on large data.
"""

from datetime import date, timedelta
from time import perf_counter

from sqlalchemy import text
from sqlalchemy.orm import Session

from .database import engine
from .indexes import apply_index_strategy
from .schemas import BenchResult
from .search import search_homes

STRATEGIES = ["none", "simple", "compound", "covering", "partial", "killer"]


def _bench_one(db: Session, runs: int, city: str, start: date, end: date) -> tuple[float, float, float]:
    timings = []
    for _ in range(runs):
        t0 = perf_counter()
        search_homes(db, city, start, end, page=1, page_size=20)
        timings.append((perf_counter() - t0) * 1000)
    return sum(timings), min(timings), max(timings)


def run_benchmark(
    db: Session,
    *,
    city: str = "Honolulu",
    runs: int = 50,
    nights: int = 5,
    restore_strategy: str | None = None,
) -> list[BenchResult]:
    today = date.today()
    start = today
    end = today + timedelta(days=nights - 1)

    results: list[BenchResult] = []
    for strategy in STRATEGIES:
        apply_index_strategy(engine, strategy)
        # warm-up: 1 query so SQLite parses & caches statement
        search_homes(db, city, start, end, page=1, page_size=20)

        total, mn, mx = _bench_one(db, runs, city, start, end)
        results.append(
            BenchResult(
                label=strategy,
                runs=runs,
                total_ms=round(total, 2),
                avg_ms=round(total / runs, 3),
                min_ms=round(mn, 3),
                max_ms=round(mx, 3),
            )
        )

    if restore_strategy is not None:
        apply_index_strategy(engine, restore_strategy)
    return results
