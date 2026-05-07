"""Background cron sweeper for the Q2 unlock-mode demo.

Three modes share the same scaffold but the cron's role differs:

  pessimistic — cron is irrelevant (lock is held in-process; no expired rows
                exist because the reservation is committed only when the user
                pays or the lock window ends)
  cron        — cron is on the critical path. A reservation is bookable only
                when its inventory row physically reads status='available'.
                If cron is down or slow, expired holds stay 'reserved' and
                block subsequent attempts.
  logical     — cron is just a janitor. The booking SQL accepts
                (status='reserved' AND expires_at < NOW()) as logically
                available, so the system works even if the cron is dead.

The sweeper runs in a daemon thread that wakes every cron_interval_seconds
and counts/clears expired reservations. We keep stats on the last run so
the UI can show 'cron last ran X seconds ago, swept N rows'.
"""

from datetime import datetime
from threading import Event, Thread
from time import time

from sqlalchemy import text

from .database import engine
from .settings import settings


class CronSweeper:
    def __init__(self):
        self._stop_event = Event()
        self._thread: Thread | None = None
        self.last_run_at: float | None = None
        self.last_sweep_count: int = 0
        self.total_runs: int = 0
        self.total_swept: int = 0
        self.enabled: bool = True

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = Thread(target=self._loop, daemon=True, name="cron-sweeper")
        self._thread.start()

    def stop(self):
        self._stop_event.set()

    def _loop(self):
        while not self._stop_event.is_set():
            interval = max(1, settings.cron_interval_seconds)
            self._stop_event.wait(timeout=interval)
            if self._stop_event.is_set():
                break
            if self.enabled:
                self.sweep_once()

    def sweep_once(self) -> int:
        """Single sweep pass — flips expired reserved rows back to available.

        Runs unconditionally when called (used by manual 'trigger now' button).
        Returns the number of rows touched.
        """
        now = datetime.utcnow()
        with engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    UPDATE inventory
                    SET status = 'available',
                        holder = NULL,
                        expires_at = NULL
                    WHERE status = 'reserved'
                      AND expires_at IS NOT NULL
                      AND expires_at < :now
                    """
                ),
                {"now": now},
            )
            count = result.rowcount or 0
        self.last_run_at = time()
        self.last_sweep_count = count
        self.total_runs += 1
        self.total_swept += count
        return count

    def stats(self) -> dict:
        return {
            "enabled": self.enabled,
            "interval_seconds": settings.cron_interval_seconds,
            "last_run_at": self.last_run_at,
            "last_run_seconds_ago": (time() - self.last_run_at) if self.last_run_at else None,
            "last_sweep_count": self.last_sweep_count,
            "total_runs": self.total_runs,
            "total_swept": self.total_swept,
        }


cron_sweeper = CronSweeper()
