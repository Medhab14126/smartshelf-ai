"""Standalone worker: `python -m app.workers` runs only the scheduler.

Use this when the API runs as several instances with RUN_SCHEDULER=false.
"""

import signal
import threading

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.workers.expiry_monitor import create_scheduler


def main() -> None:
    configure_logging(get_settings().log_level)
    scheduler = create_scheduler()
    scheduler.start()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    stop.wait()
    scheduler.shutdown()


if __name__ == "__main__":
    main()
