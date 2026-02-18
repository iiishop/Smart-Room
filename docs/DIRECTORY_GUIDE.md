# Directory Guide

## Current Structure

```text
Smart Room/
├── backend/                      # Fresh Python backend for rebuild
├── archive_code/
│   ├── backend_legacy_2026-02-15
│   └── frontend_legacy_2026-02-15
├── docs/
│   ├── 01_MVP_SCOPE.md           # Current MVP boundary
│   ├── 02_QUEST3_UNITY_STREAMING_PLAN.md
│   ├── 03_SDK_REFERENCE.md
│   ├── CHANGELOG_2026-02-15.md
│   ├── DIRECTORY_GUIDE.md
│   └── archive/                  # Historical/legacy docs
└── README.md
```

## How to use this workspace now

1. Read `README.md` and `docs/01_MVP_SCOPE.md` first.
2. Follow `docs/02_QUEST3_UNITY_STREAMING_PLAN.md` for implementation order.
3. Use `docs/03_SDK_REFERENCE.md` when wiring Unity SDK features.
4. Consult `docs/archive/` only for context/history.

## Next suggested folder additions

- `unity/` for Quest 3 Unity project files.
- `tools/receiver/` for a standalone Python receiving service.
- `samples/` for test payloads and protocol examples.
