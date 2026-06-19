# VR Interactive SAM2 — 3D Anchor Points + Real-Time Re-Prediction

> **For Hermes:** Dispatch to OpenCode with `--model openai/gpt-5.5-xhigh`, load `ponytail` skill.
> OpenCode prompt: "Load skill ponytail, then execute this plan sequentially. Work directly in current directory. TDD relaxed. Only STOP on genuine design conflicts."

**Goal:** Replace depth-heuristic automatic point sampling with VR-controller-placed 3D anchor points. User places green/red spheres in MR space → viewer projects them to current RGB frame → SAM2 re-predicts incrementally → returns 3D world-space contour for Unity LineRenderer.

**Architecture:**

```
VR (Quest 3)                            Viewer (Python)
──────────                              ──────────
Controller raycast → world pos          ┌─ POST: rgb + depth + meta + 3D_anchors + labels
Green/red spheres stuck in world        │
                                        ├─ Project 3D anchors → 2D pixel coords
                                        ├─ SAM2.set_image(rgb)  [once per frame]
                                        ├─ SAM2.predict(points, labels)  [per trigger]
                                        ├─ extract contour → back-project to 3D world
                                        └─ Response: {contour_3d: [[x,y,z],...], mask_area, ...}
                                        
LineRenderer renders 3D contour         ←─
```

**Key insight:** `set_image()` (400ms) runs only when RGB frame changes. `predict()` (150ms) on every incremental point. 3D anchors persist across head movements — re-projected into each new frame.

**Tech Stack:** SAM2.1-hiera-small (already loaded), numpy, cv2. No new deps.

---

## Task 1: Add `world_to_pixel()` and `pixel_to_world()` projection utilities

**Objective:** Convert between world-space 3D points and RGB pixel coordinates using camera pose + intrinsics.

**Files:**
- Create: `viewer/pose_projection.py`

**Step 1: Write `pose_projection.py`**

```python
from __future__ import annotations
import numpy as np

def world_to_rgb_pixel(
    world_points: np.ndarray,       # (N, 3) in meters, Unity world
    camera_pose_world: np.ndarray,  # (4, 4) cam→world matrix
    rgb_intrinsics: np.ndarray,     # (3, 3) fx 0 cx; 0 fy cy; 0 0 1
    rgb_h: int, rgb_w: int,
) -> np.ndarray | None:
    """Project world points to RGB pixel coordinates.
    
    Returns (N, 2) int array of (px, py), or None if all out of frame.
    """
    # World → camera: cam_pts = inv(camera_pose_world) @ world_pts_homog
    cam_pose_inv = np.linalg.inv(camera_pose_world)
    ones = np.ones((world_points.shape[0], 1), dtype=np.float32)
    world_h = np.hstack([world_points.astype(np.float32), ones])
    cam_h = world_h @ cam_pose_inv.T
    cam_pts = cam_h[:, :3]
    
    # Camera → pixels
    fx, fy = rgb_intrinsics[0, 0], rgb_intrinsics[1, 1]
    cx, cy = rgb_intrinsics[0, 2], rgb_intrinsics[1, 2]
    
    # Points behind camera → invalid
    in_front = cam_pts[:, 2] > 0.01
    if not in_front.any():
        return None
    
    u = (cam_pts[:, 0] * fx / cam_pts[:, 2] + cx).round().astype(int)
    v = (cam_pts[:, 1] * fy / cam_pts[:, 2] + cy).round().astype(int)
    
    in_frame = (u >= 0) & (u < rgb_w) & (v >= 0) & (v < rgb_h) & in_front
    
    return np.column_stack([u, v]), in_frame


def rgb_pixel_to_world(
    pixels: np.ndarray,             # (N, 2) int (px, py)
    depth_map: np.ndarray,          # (H, W) aligned depth in meters
    camera_pose_world: np.ndarray,  # (4, 4) cam→world matrix
    rgb_intrinsics: np.ndarray,     # (3, 3)
    depth_scale_m: float = 1.0,
) -> np.ndarray | None:
    """Back-project RGB pixels with depth to world coordinates.
    
    Returns (M, 3) float32 world XYZ, or None if no valid depths.
    """
    px = pixels[:, 0].astype(int)
    py = pixels[:, 1].astype(int)
    h, w = depth_map.shape
    
    valid = (px >= 0) & (px < w) & (py >= 0) & (py < h)
    if not valid.any():
        return None
    
    px, py = px[valid], py[valid]
    depths = depth_map[py, px] * depth_scale_m
    depth_ok = depths > 0
    if not depth_ok.any():
        return None
    
    px, py, depths = px[depth_ok], py[depth_ok], depths[depth_ok]
    
    fx, fy = rgb_intrinsics[0, 0], rgb_intrinsics[1, 1]
    cx, cy = rgb_intrinsics[0, 2], rgb_intrinsics[1, 2]
    
    # Pixel → camera space: X = (u - cx) * Z / fx, Y = (v - cy) * Z / fy
    cam_x = (px.astype(np.float32) - cx) * depths / fx
    cam_y = (py.astype(np.float32) - cy) * depths / fy  # image Y down → sensor Y up? 
    # Wait — we need to handle the Y-flip. RGB image has Y↓, sensor has Y↑.
    # For aligned depth: depth_map[y, x] where y is image row (top=0).
    # Standard: sensor_y = (h-1) - py, then cam_y = (sensor_y - cy) * Z / fy
    sensor_y = (h - 1) - py.astype(np.float32)
    cam_y = (sensor_y - cy) * depths / fy
    cam_z = depths
    
    cam_pts = np.column_stack([cam_x, cam_y, cam_z])
    ones = np.ones((cam_pts.shape[0], 1), dtype=np.float32)
    cam_h = np.hstack([cam_pts, ones])
    world_h = cam_h @ camera_pose_world.T
    return world_h[:, :3].astype(np.float32)
```

**Step 2: Verify**

```bash
uv run python -c "from pose_projection import world_to_rgb_pixel, rgb_pixel_to_world; print('OK')"
```

---

## Task 2: Add `reset_for_image()` and `re_predict()` to `Sam2DeviceSegmenter`

**Objective:** Allow reusing SAM2 image embedding across multiple `predict()` calls. `set_image()` only when the RGB frame changes.

**Files:**
- Modify: `viewer/sam2_device_segment.py`

**Step 1: Add methods to `Sam2DeviceSegmenter`**

After `segment()`, add:

```python
    def reset_for_image(self, rgb: np.ndarray) -> None:
        """Encode a new RGB frame. Call once per frame change."""
        self.load()
        assert self.predictor is not None
        self.predictor.set_image(rgb.astype(np.uint8))
        self._current_rgb_hash = hash(rgb.tobytes())
    
    def re_predict(
        self,
        point_coords: np.ndarray,
        point_labels: np.ndarray,
        box: np.ndarray | None = None,
    ) -> np.ndarray | None:
        """Predict with new points on the already-encoded image.
        
        150ms vs 400ms for full segment().
        Returns (1, H, W) bool mask or None.
        """
        assert self.predictor is not None
        masks, scores, _ = self.predictor.predict(
            point_coords=point_coords,
            point_labels=point_labels,
            box=box,
            multimask_output=True,
            return_logits=False,
        )
        masks = masks.astype(bool)
        scores = np.asarray(scores, dtype=np.float32)
        
        # Pick best mask (highest score that contains any positive point)
        best_idx = int(np.argmax(scores))
        return masks[best_idx]
```

**Step 2: Verify**

```bash
uv run python -c "from sam2_device_segment import Sam2DeviceSegmenter; s = Sam2DeviceSegmenter(); print(hasattr(s, 're_predict'), hasattr(s, 'reset_for_image')); print('OK')"
```

---

## Task 3: Add `extract_world_contour()` function

**Objective:** Mask → contour pixels → back-project to 3D world coordinates for Unity LineRenderer.

**Files:**
- Create new function in `viewer/pose_projection.py`

**Step 1: Add function**

```python
def extract_world_contour(
    mask: np.ndarray,
    depth_map: np.ndarray,
    camera_pose_world: np.ndarray,
    rgb_intrinsics: np.ndarray,
    max_points: int = 500,
    simplify_epsilon: float = 0.005,
) -> list[list[float]]:
    """Extract 3D world-space contour from mask using aligned depth.
    
    Returns list of [x, y, z] world coordinates.
    Simplified and downsampled for LineRenderer.
    """
    contours, _ = cv2.findContours(
        mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    if not contours:
        return []
    
    # Use longest contour
    longest = max(contours, key=cv2.arcLength)
    
    # Downsample
    if longest.shape[0] > max_points:
        indices = np.linspace(0, longest.shape[0] - 1, max_points, dtype=int)
        longest = longest[indices]
    
    pixels = longest.reshape(-1, 2)  # (N, 2) — OpenCV returns (x, y)
    world_pts = rgb_pixel_to_world(pixels, depth_map, camera_pose_world, rgb_intrinsics)
    if world_pts is None:
        return []
    
    return world_pts.tolist()
```

Need to add `import cv2` if not already at top.

**Step 2: Verify**

```bash
uv run python -c "from pose_projection import extract_world_contour; print('OK')"
```

---

## Task 4: Wire into HTTP handler with anchor projection + re-prediction

**Objective:** Modify the HTTP POST handler to accept 3D anchors, project them to 2D, run SAM2 re-prediction, return 3D contour.

**Files:**
- Modify: `viewer/quest3_rgbd_align_viewer.py` — HTTP handler `do_POST()` and `run_device_segmentation()`

**New POST body fields (in addition to existing):**
- `anchor_points_json`: JSON array of `{"x": float, "y": float, "z": float, "label": +1|-1}`
- `re_predict`: bool — if true, reuse existing image embedding, only call re_predict()

**Step 1: Add anchor parsing in `do_POST()` after existing field extraction**

```python
        anchor_points_json = parts.get("anchor_points_json")
        re_predict_raw = parts.get("re_predict")
        re_predict = re_predict_raw is not None and re_predict_raw.strip().lower() == b"true"
        
        anchors = None
        if anchor_points_json is not None:
            anchors = json.loads(anchor_points_json.decode("utf-8"))
```

**Step 2: Pass to `run_device_segmentation()`**

Modify function signature to accept optional `anchors` and `re_predict`:

```python
def run_device_segmentation(
    frame: FrameData,
    args: argparse.Namespace,
    segmenter: Sam2DeviceSegmenter | None,
    anchors: list[dict] | None = None,
    re_predict: bool = False,
) -> FrameData:
```

**Step 3: Anchor-based prediction logic**

After the existing `build_cursor_prompt()` call, before SAM2 call:

```python
    if anchors is not None and segmenter is not None and segmenter.ready:
        # Project 3D anchors to current RGB frame
        from pose_projection import world_to_rgb_pixel, rgb_pixel_to_world, extract_world_contour
        
        depth_pose = np.array(frame.meta["depth"]["pose_matrix"], dtype=np.float32)  # (4,4) cam→world
        rgb_intrinsics_arr = np.array([
            [frame.meta["rgb"]["fx"], 0, frame.meta["rgb"]["cx"]],
            [0, frame.meta["rgb"]["fy"], frame.meta["rgb"]["cy"]],
            [0, 0, 1],
        ], dtype=np.float32)
        
        world_pts = np.array([[a["x"], a["y"], a["z"]] for a in anchors], dtype=np.float32)
        labels_arr = np.array([int(a["label"]) for a in anchors], dtype=np.int32)
        
        result = world_to_rgb_pixel(world_pts, depth_pose, rgb_intrinsics_arr, frame.rgb.shape[0], frame.rgb.shape[1])
        if result is not None:
            pixel_pts, in_frame = result
            if in_frame.any():
                pixel_pts = pixel_pts[in_frame]
                labels_arr = labels_arr[in_frame]
                
                if not re_predict or not hasattr(segmenter, '_current_rgb_hash'):
                    segmenter.reset_for_image(frame.rgb)
                mask = segmenter.re_predict(pixel_pts, labels_arr)
                
                if mask is not None:
                    frame.device_mask = mask
                    contour_3d = extract_world_contour(
                        mask, frame.aligned_depth, depth_pose, rgb_intrinsics_arr
                    )
                    frame.device_contour_3d = contour_3d
                    frame.device_overlay_rgb = overlay_device_mask(
                        frame.any2full_overlay_rgb if frame.any2full_overlay_rgb is not None else frame.overlay_rgb,
                        mask,
                    )
                    frame.device_mask_path = work_dir / "device_mask_vr.png"
                    Image.fromarray(mask.astype(np.uint8) * 255).save(frame.device_mask_path)
                    frame.device_info = {
                        "area_px": int(np.count_nonzero(mask)),
                        "contour_3d_points": len(contour_3d),
                        "anchors_used": int(in_frame.sum()),
                    }
                    return frame
```

**Step 4: Include contour_3d in HTTP response**

In the response JSON (around line 1380), add:
```python
            "contour_3d": frame.device_contour_3d if hasattr(frame, 'device_contour_3d') and frame.device_contour_3d else [],
```

**Step 5: Verify**

```bash
uv run python -c "from quest3_rgbd_align_viewer import run_device_segmentation; print('OK')"
```

---

## Task 5: Unity Protocol Spec (documentation only, no code)

**Files:**
- Create: `docs/unity-vr-anchor-protocol.md`

Document the protocol for Unity integration:

```markdown
# VR Anchor Protocol — Unity ↔ Viewer

## POST /api/track/start-final-rgbd (enhanced)

Existing fields: rgb_jpeg, depth_raw, meta_json, cursor_json

NEW optional fields:
- `anchor_points_json`: JSON string of [{"x": 1.2, "y": 0.5, "z": -0.8, "label": 1}, ...]
  - label: +1 = positive (green), -1 = negative (red)
  - Coordinates in Unity world space (meters)
- `re_predict`: "true" or "false" — whether to reuse existing SAM2 image encoding

## Response (enhanced)

Existing fields + new:
- `contour_3d`: [[x, y, z], ...] — world-space contour points for LineRenderer

## Unity Implementation Notes

1. Accumulate anchors in a List<Vector3> + List<int> on the controller
2. On each trigger (or point-placed event):
   - Capture RGB frame via Quest3RgbdCaptureFinal.CaptureOnceToPayload()
   - Serialize anchors to JSON
   - POST with re_predict="true" (after first frame)
3. On response, parse contour_3d → update LineRenderer.positionCount + SetPositions()
4. Visual feedback: instantiate green/red sphere prefabs at anchor world positions
5. On "commit" button: send all anchors + re_predict="false" for final clean prediction
```

---

## Task 6: Commit

```bash
git add viewer/pose_projection.py viewer/sam2_device_segment.py viewer/quest3_rgbd_align_viewer.py docs/unity-vr-anchor-protocol.md
git commit -m "feat(viewer): VR interactive SAM2 with 3D anchor points and real-time re-prediction

- pose_projection.py: world_to_rgb_pixel, rgb_pixel_to_world, extract_world_contour
- SAM2 re_predict() reuses image embedding (150ms vs 400ms)
- reset_for_image() handles head-movement frame changes
- HTTP handler accepts anchor_points_json + re_predict flag
- Response includes contour_3d for Unity LineRenderer
- Protocol spec in docs/unity-vr-anchor-protocol.md"
```
