# Backend (fresh start)

This backend has been reset for the new Quest 3 MVP.

Planned responsibility:

1. Receive RGB/depth stream from Quest 3 Unity app.
2. Expose APIs for dashboard visualization and configuration.
3. Manage session, logging, and optional recording.

Legacy backend was moved to:

- `archive_code/backend_legacy_2026-02-15`

## Current test entrypoint

Run the combined backend + dashboard process:

```bash
uv run python run_dashboard.py
```

What it does:

1. Starts FastAPI on `0.0.0.0:8000`
2. Opens a PySide6 desktop dashboard window
3. Shows live Quest heartbeat status from `/ws/heartbeat`

## API endpoints for heartbeat test

- `GET /health`
- `GET /api/status`
- `WS /ws/heartbeat`
