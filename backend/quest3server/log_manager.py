from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import inspect
import logging
from threading import Lock
from typing import Any


_MAX_LOGS = 2000
_LOGS: deque[dict[str, Any]] = deque(maxlen=_MAX_LOGS)
_LOCK = Lock()
_NEXT_ID = 1
_LOGGING_BRIDGE_INSTALLED = False


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def add_log(
    *,
    level: str,
    source: str,
    message: str,
    script: str | None = None,
    line: int | None = None,
    stack_trace: str | None = None,
) -> dict[str, Any]:
    global _NEXT_ID

    record = {
        "id": 0,
        "timestamp_utc": _utc_now_iso(),
        "level": level.upper(),
        "source": source,
        "script": script,
        "line": line,
        "message": message,
        "stack_trace": stack_trace,
    }

    with _LOCK:
        record["id"] = _NEXT_ID
        _NEXT_ID += 1
        _LOGS.append(record)

    return record


def add_python_log(
    level: str, message: str, stack_trace: str | None = None
) -> dict[str, Any]:
    frame = inspect.currentframe()
    caller = frame.f_back if frame else None
    script = caller.f_code.co_filename if caller else None
    line = caller.f_lineno if caller else None
    return add_log(
        level=level,
        source="python",
        message=message,
        script=script,
        line=line,
        stack_trace=stack_trace,
    )


class DashboardLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)

        stack_trace = None
        if record.exc_info:
            formatter = logging.Formatter()
            stack_trace = formatter.formatException(record.exc_info)

        add_log(
            level=record.levelname,
            source=f"python:{record.name}",
            message=message,
            script=record.pathname,
            line=record.lineno,
            stack_trace=stack_trace,
        )


def install_python_logging_bridge() -> None:
    global _LOGGING_BRIDGE_INSTALLED
    if _LOGGING_BRIDGE_INSTALLED:
        return

    handler = DashboardLogHandler()
    handler.setLevel(logging.INFO)

    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    root_logger.setLevel(min(root_logger.level or logging.INFO, logging.INFO))

    for logger_name in ("uvicorn", "uvicorn.error"):
        logger = logging.getLogger(logger_name)
        logger.addHandler(handler)
        logger.setLevel(min(logger.level or logging.INFO, logging.INFO))

    _LOGGING_BRIDGE_INSTALLED = True


def list_logs(since_id: int = 0, limit: int = 200) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 1000))
    with _LOCK:
        items = [item for item in _LOGS if item["id"] > since_id]
    return items[-limit:]
