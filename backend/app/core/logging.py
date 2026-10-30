"""Logging setup.

Logs never include passwords, session tokens, SMTP credentials or full
document text. Use `redact()` before logging anything user-supplied that
might contain secrets (e.g. SMTP error strings that echo credentials).
"""

import logging
import re
import sys

_SECRET_PATTERN = re.compile(r"(password|passwd|token|secret|authorization)\s*[=:]\s*\S+", re.IGNORECASE)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # Third-party loggers that are too chatty at INFO.
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def redact(text: str | None, max_length: int = 500) -> str:
    if not text:
        return ""
    cleaned = _SECRET_PATTERN.sub(r"\1=***", str(text))
    return cleaned[:max_length]
