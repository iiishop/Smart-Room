from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import inspect
from threading import Lock
from typing import Any


_MAX_LOGS = 2000
_LOGS: deque[dict[str, Any]] = deque(maxlen=_MAX_LOGS)
_LOCK = Lock()
_NEXT_ID = 1


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


def list_logs(since_id: int = 0, limit: int = 200) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 1000))
    with _LOCK:
        items = [item for item in _LOGS if item["id"] > since_id]
    return items[-limit:]
