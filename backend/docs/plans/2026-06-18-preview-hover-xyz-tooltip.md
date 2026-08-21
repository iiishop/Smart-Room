# Dashboard Preview Floating XYZ Tooltip — Implementation Plan

> **For Hermes:** Dispatch to OpenCode with `--model openai/gpt-5.4` and load `ponytail` skill.
> OpenCode prompt: "Load skill ponytail, then execute this plan."

**Goal:** Replace the Preview tab's status bar hover text with a floating tooltip that follows the mouse on the RGB-D overlay, showing XYZ spatial coordinates (meters) of the nearest depth pixel, drawn on the topmost layer.

**Architecture:** Add a child QLabel (`_hover_tooltip`) to the existing `_lbl_rgbd_overlay`. Position it at mouse coordinates on MouseMove, hide on Leave. Reuse existing `/api/depth/at` backend endpoint (already returns XYZ). Remove the old `_lbl_preview_hover` status bar from the layout — it becomes redundant.

**Tech Stack:** PySide6 QLabel with `setWindowFlags(Qt.ToolTip)` or `raise_()` for z-order, styled with dark semi-transparent background.

---

## Task 1: Create floating tooltip widget in Preview tab

**Objective:** Add a hidden QLabel child to `_lbl_rgbd_overlay` that will serve as the XYZ tooltip.

**Files:**
- Modify: `backend/quest3server/run_dashboard.py` — `_build_preview_tab()` (around line 426)

**Step 1: Add tooltip QLabel after `_lbl_rgbd_overlay` setup**

In `_build_preview_tab()`, after line 445 (`overlay_layout.addWidget(self._lbl_rgbd_overlay)`), add:

```python
        # Floating XYZ tooltip (child of overlay, hidden by default)
        self._hover_tooltip = QLabel(self._lbl_rgbd_overlay)
        self._hover_tooltip.hide()
        self._hover_tooltip.setStyleSheet("""
            QLabel {
                background-color: rgba(0, 0, 0, 200);
                color: #e0e0e0;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 12px;
                padding: 4px 8px;
                border: 1px solid #444;
                border-radius: 4px;
            }
        """)
        self._hover_tooltip.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
```

**Step 2: Remove the old status bar label from layout**

Comment out or remove lines 431-432:
```python
        # REMOVED: self._lbl_preview_hover = QLabel(...)
        # REMOVED: layout.addWidget(self._lbl_preview_hover)
```
Keep the variable as `None` to avoid attribute errors elsewhere.

**Step 3: Verify**

Run: `uv run python -c "from PySide6.QtWidgets import QApplication; app = QApplication([]); from quest3server.run_dashboard import DashboardWindow; w = DashboardWindow(); print('OK')"`

Expected: no crash on import/construction.

---

## Task 2: Position tooltip on mouse move, hide on leave

**Objective:** In `eventFilter`, show/position the tooltip on MouseMove, hide on Leave.

**Files:**
- Modify: `backend/quest3server/run_dashboard.py` — `eventFilter()` (line 1027) and `_update_preview_hover()` (line 1062)

**Step 1: Update `eventFilter` MouseMove**

Replace lines 1030-1032:
```python
            if etype == QEvent.Type.MouseMove:
                pos = event.position().toPoint()
                self._update_preview_hover(pos.x(), pos.y())
```
With:
```python
            if etype == QEvent.Type.MouseMove:
                pos = event.position().toPoint()
                self._update_preview_hover(pos.x(), pos.y())
                # Position tooltip near cursor
                if self._hover_tooltip is not None and not self._hover_tooltip.isHidden():
                    tip_x = pos.x() + 16
                    tip_y = pos.y() + 16
                    # Keep tooltip inside overlay bounds
                    overlay_w = self._lbl_rgbd_overlay.width()
                    overlay_h = self._lbl_rgbd_overlay.height()
                    tip_w = self._hover_tooltip.width()
                    tip_h = self._hover_tooltip.height()
                    if tip_x + tip_w > overlay_w:
                        tip_x = pos.x() - tip_w - 8
                    if tip_y + tip_h > overlay_h:
                        tip_y = pos.y() - tip_h - 8
                    self._hover_tooltip.move(tip_x, tip_y)
```

**Step 2: Update `eventFilter` Leave**

Replace lines 1036-1039:
```python
            elif etype == QEvent.Type.Leave:
                self._lbl_preview_hover.setText(
                    "Hover over image for pixel coords and depth"
                )
```
With:
```python
            elif etype == QEvent.Type.Leave:
                if self._hover_tooltip is not None:
                    self._hover_tooltip.hide()
```

**Step 3: Update `_update_preview_hover` to set tooltip text instead of status bar**

Replace the entire method body (lines 1062-1097) with:
```python
    def _update_preview_hover(self, mx: int, my: int) -> None:
        mapped = self._map_preview_pos_to_source_pixel(mx, my)
        if mapped is None:
            if self._hover_tooltip is not None:
                self._hover_tooltip.hide()
            return
        px, py = mapped

        text = f"({px},{py})  no depth"
        try:
            req = urllib.request.Request(
                f"{API_BASE}/api/depth/at?px={px}&py={py}", method="GET",
            )
            with urllib.request.urlopen(req, timeout=0.3) as res:
                d = json.loads(res.read().decode("utf-8"))
            dm = d.get("depth_m")
            if dm is not None and d.get("valid"):
                src = d.get("source", "?")
                x = d.get("rgb_cam_x", 0)
                y = d.get("rgb_cam_y", 0)
                z = d.get("rgb_cam_z", 0)
                # Format XYZ in meters
                text = f"XYZ = ({x:.3f}, {y:.3f}, {z:.3f}) m"
                if src == "nearest":
                    dist = d.get("distance_px", 0)
                    text += f"  (~{dist:.0f}px)"
        except Exception:
            text = f"({px},{py})  (query failed)"

        if self._hover_tooltip is not None:
            self._hover_tooltip.setText(text)
            self._hover_tooltip.adjustSize()
            self._hover_tooltip.raise_()
            self._hover_tooltip.show()
```

**Step 4: Remove residual references to `_lbl_preview_hover`**

Search for any remaining `_lbl_preview_hover` references in the file and guard them:
- Line 1037/1065: old setText calls already replaced above
- Ensure no bare access without `hasattr` check

**Step 5: Verify**

Run: `uv run python -c "from PySide6.QtWidgets import QApplication; app = QApplication([]); from quest3server.run_dashboard import DashboardWindow; w = DashboardWindow(); assert hasattr(w, '_hover_tooltip'); print('OK')"`

Expected: `OK` — tooltip widget created, no crashes.

---

## Task 3: Verify backend XYZ correctness

**Objective:** Confirm `/api/depth/at` returns correct RGB-camera XYZ in meters. Spot-check the formula.

**Files:**
- Read only: `backend/quest3server/main.py` (lines 896-915)

**Step 1: Formula review**

The formula at lines 902-904:
```python
sensor_y = (h - 1) - float(sample_py)  # flip image->sensor
x_cam = (float(sample_px) - cx) * depth / max(fx, 1e-6)
y_cam = (sensor_y - cy) * depth / max(fy, 1e-6)
```

This is standard pinhole unprojection: sensor coordinates scaled by depth/focal. Y-flip is correct (image Y down → sensor Y up). Z = depth (along optical axis, not Euclidean). Units are meters because `aligned_depth_m` is in meters and `fx/fy` are in pixels. Verified against the reference doc `dashboard-hover-xyz-implementation.md`.

No changes needed. Formula is correct.

**Step 2: Add unit test (ponytail: one runnable check)**

Create a quick self-check script that verifies the formula with known values:

```python
# test_xyz_formula.py (temporary — verify then delete)
fx, fy, cx, cy = 866.79, 866.79, 645.47, 640.50  # Quest 3 PCA intrinsics
h = 1280
# Center pixel at 1m depth should give XYZ ~ (0, 0, 1)
px, py = 645, 640
depth = 1.0
sensor_y = (h - 1) - py
x = (px - cx) * depth / fx
y = (sensor_y - cy) * depth / fy
assert abs(x) < 0.01, f"Center X should be ~0, got {x}"
assert abs(y) < 0.01, f"Center Y should be ~0, got {y}"
print(f"Center pixel XYZ at 1m: ({x:.4f}, {y:.4f}, {depth}) — OK")
```

Run: `uv run python test_xyz_formula.py`
Expected: `Center pixel XYZ at 1m: (0.0000, 0.0000, 1.0) — OK`

Delete `test_xyz_formula.py` after verification.

---

## Task 4: Commit

```bash
git add backend/quest3server/run_dashboard.py
git commit -m "feat: floating XYZ tooltip on Preview tab hover, replacing status bar"
```
