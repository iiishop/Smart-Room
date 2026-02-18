# Smart Room - Quest 3 MVP Workspace

Current focus: build the smallest end-to-end pipeline for Quest 3 data streaming.

## Current MVP Goal

Build a working prototype with three capabilities:

1. Quest 3 Unity app captures camera RGB and depth data.
2. Quest 3 sends data to a computer over network.
3. Python backend on computer receives and stores/displays incoming frames.

This repo previously explored MQTT and Home Assistant directions. Those materials are preserved under `docs/archive/` and marked as legacy.

## What To Read First

- `docs/01_MVP_SCOPE.md` - exact MVP boundaries and acceptance criteria
- `docs/02_QUEST3_UNITY_STREAMING_PLAN.md` - implementation plan and milestones
- `docs/03_SDK_REFERENCE.md` - Quest 3 Unity SDK/API reference links
- `docs/CHANGELOG_2026-02-15.md` - latest cleanup and restructuring notes
- `docs/DIRECTORY_GUIDE.md` - how this workspace is organized now

## Quick Start (backend only)

```bash
cd backend
pip install -r requirements.txt
python main.py
```

## Notes

- Old backend/frontend code has been archived to `archive_code/`.
- Current `backend/` is reset for clean rebuild (Python receiver + dashboard API).
- Unity project for Quest 3 is planned under `unity/` (to be created next).
