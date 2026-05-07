"""Runtime-toggleable settings for the playground.

Each toggle maps to a Q1-Q5 design decision. Flipping these at runtime
demonstrates how the system behaves under different choices.
"""

from threading import Lock


class Settings:
    """In-memory, single-instance, mutable. Resets on server restart."""

    def __init__(self):
        self._lock = Lock()
        # Q3: index strategy (which index covers the search query)
        # 'none'      -> drop all custom indexes (full scan)
        # 'simple'    -> single-column index on city
        # 'compound'  -> (city, date, status)
        # 'covering'  -> (city, date, status, home_id) — covering only
        # 'partial'   -> (city, date) WHERE status='available' — partial only (回表)
        # 'killer'    -> (city, date, home_id) WHERE status='available' — partial+covering
        self.index_strategy: str = "compound"

        # Q5: cache home detail in memory?
        self.cache_home_detail: bool = True

        # Q2: concurrency control mode (for didactic comparison)
        # 'logical'  -> conditional UPDATE with logical availability check (recommended)
        # 'naive'    -> SELECT then UPDATE (race condition demonstration)
        self.concurrency_mode: str = "logical"

        # Q2 demo: how does an expired reservation get released?
        # 'pessimistic' -> hold a process-wide lock for the full TTL, blocking
        #                  any other reservation attempt (anti-pattern demo)
        # 'cron'        -> reservation only releases when the background cron
        #                  sweep runs; reservations strictly require status=
        #                  'available' (cron is on the critical path)
        # 'logical'     -> next booker self-services takeover via conditional
        #                  UPDATE that accepts (reserved AND expired); cron is
        #                  optional janitor only
        self.unlock_mode: str = "logical"

        # Background cron interval for the cron-sweep demo. Long enough that
        # users can see the gap between TTL expiry and cron sweep.
        self.cron_interval_seconds: int = 5

        # Q4: enforce idempotency on confirm endpoint?
        self.idempotency_enforced: bool = True

        # Reservation hold duration in seconds (10 min default)
        self.reservation_ttl_seconds: int = 600

    def to_dict(self) -> dict:
        return {
            "index_strategy": self.index_strategy,
            "cache_home_detail": self.cache_home_detail,
            "concurrency_mode": self.concurrency_mode,
            "idempotency_enforced": self.idempotency_enforced,
            "reservation_ttl_seconds": self.reservation_ttl_seconds,
            "unlock_mode": self.unlock_mode,
            "cron_interval_seconds": self.cron_interval_seconds,
        }

    def update(self, **kwargs):
        valid_index = {"none", "simple", "compound", "covering", "partial", "killer"}
        valid_concurrency = {"logical", "naive"}
        valid_unlock = {"pessimistic", "cron", "logical"}

        with self._lock:
            if "index_strategy" in kwargs:
                v = kwargs["index_strategy"]
                if v not in valid_index:
                    raise ValueError(f"index_strategy must be one of {valid_index}")
                self.index_strategy = v
            if "cache_home_detail" in kwargs:
                self.cache_home_detail = bool(kwargs["cache_home_detail"])
            if "concurrency_mode" in kwargs:
                v = kwargs["concurrency_mode"]
                if v not in valid_concurrency:
                    raise ValueError(f"concurrency_mode must be one of {valid_concurrency}")
                self.concurrency_mode = v
            if "idempotency_enforced" in kwargs:
                self.idempotency_enforced = bool(kwargs["idempotency_enforced"])
            if "reservation_ttl_seconds" in kwargs:
                ttl = int(kwargs["reservation_ttl_seconds"])
                if ttl < 5 or ttl > 3600:
                    raise ValueError("reservation_ttl_seconds must be between 5 and 3600")
                self.reservation_ttl_seconds = ttl
            if "unlock_mode" in kwargs:
                v = kwargs["unlock_mode"]
                if v not in valid_unlock:
                    raise ValueError(f"unlock_mode must be one of {valid_unlock}")
                self.unlock_mode = v
            if "cron_interval_seconds" in kwargs:
                iv = int(kwargs["cron_interval_seconds"])
                if iv < 1 or iv > 60:
                    raise ValueError("cron_interval_seconds must be between 1 and 60")
                self.cron_interval_seconds = iv


settings = Settings()
