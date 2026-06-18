# Viewer: Skip local frames in --server mode

> **For Hermes:** Dispatch to OpenCode with `--model openai/gpt-5.4`, load `ponytail` skill.

**Goal:** `--server` mode should not require `--data` or local capture directories. Viewer starts with a blank/placeholder state and populates on first network frame.

**Files:** `tools/quest3_rgbd_align_viewer.py`

---

## Task 1: Make --data optional, skip discover when --server

In `main()`: when `--server`, skip `discover_frames()` entirely, pass empty list to RgbdViewer.

In `RgbdViewer.__init__`: handle `frames=[]` gracefully — don't crash on `self.frames[self.frame_index]`.

In `update_display` or `update_rgb_image`: if `self.frame is None`, show a "Waiting for trigger payload..." placeholder instead of crashing.

In `parse_args`: make `--data` default to `None` (or keep default but don't enforce).

**Verification:**
```bash
uv run python -c "import sys; sys.argv=['test','--server','--no-ui']; from tools.quest3_rgbd_align_viewer import parse_args, main; print('import OK')"
```
