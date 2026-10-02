"""Nightly backup: from 03:00 local time, once per day, if a target exists."""
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from app import backup, operations
from app.config import settings

BACKUP_HOUR = 3
JOB_ID = "_backup"


def tick(now: datetime | None = None) -> int | None:
    """Start the nightly backup job if it is due. Returns the job id if started."""
    now = now or datetime.now(ZoneInfo(settings.tz))
    backup.cleanup_exports()  # exports hold app data: gone after 24 h even without a new export
    today = now.date().isoformat()
    if now.hour < BACKUP_HOUR or backup.get_setting("last_scheduled_backup") == today:
        return None
    if not backup.list_targets() or operations.is_busy(JOB_ID):
        return None
    backup.set_setting("last_scheduled_backup", today)
    return operations.start_job(JOB_ID, "backup", lambda log: backup.backup_all(log, "scheduled"))


def _loop() -> None:
    while True:
        try:
            tick()
        except Exception as exc:  # never let the scheduler thread die
            print(f"scheduler: {exc!r}")
        time.sleep(300)


def start() -> None:
    threading.Thread(target=_loop, name="scheduler", daemon=True).start()
