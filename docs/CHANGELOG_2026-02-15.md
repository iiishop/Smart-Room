# Change Log - 2026-02-15

## Why this update

The project direction is now explicitly focused on a Quest 3 MVP for RGB/depth streaming to a Python backend. Existing mixed historical docs caused scope confusion.

## What changed

1. Rewrote top-level `README.md` to reflect current MVP objective.
2. Added focused docs:
   - `docs/01_MVP_SCOPE.md`
   - `docs/02_QUEST3_UNITY_STREAMING_PLAN.md`
   - `docs/03_SDK_REFERENCE.md`
   - `docs/DIRECTORY_GUIDE.md`
3. Moved old design docs into `docs/archive/` and marked them legacy.
4. Moved old top-level setup summaries into `docs/archive/`.

## Legacy materials retained

Legacy files are preserved for reference only and should not drive current implementation decisions.

## Current source of truth

- MVP requirements: `docs/01_MVP_SCOPE.md`
- Engineering plan: `docs/02_QUEST3_UNITY_STREAMING_PLAN.md`
- SDK links: `docs/03_SDK_REFERENCE.md`

## Additional restructuring (same day)

1. Archived old code directories:
   - `archive_code/backend_legacy_2026-02-15`
   - `archive_code/frontend_legacy_2026-02-15`
2. Removed root `.env` and `.env.example` to switch toward future visual configuration flow.
3. Recreated clean `backend/` with minimal starter files:
   - `backend/main.py`
   - `backend/requirements.txt`
   - `backend/README.md`
