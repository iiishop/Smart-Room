# Two-Stage Depth Component Growth — Implementation Plan

> **For Hermes:** Dispatch to OpenCode with `--model openai/gpt-5.5-medium`, load `ponytail` skill.
> OpenCode prompt: "Load skill ponytail, then execute this plan. Work directly in current directory. TDD relaxed."

**Goal:** When SAM2 click-point (e.g. fruit on a bottle) produces a mask that's just the texture region, add a second BFS stage that grows past weak RGB texture edges to capture the enclosing object.

**Architecture:** Add post-processing in `build_rgbd_device_prompt()` — after the first (RGB-edge-blocked) component, dilate it, then re-grow without RGB edge blocking to find the "container" object. Use the stage-2 component for SAM2 points and box prompt. Controlled by a new config flag `enable_stage2_growth`.

**Tech Stack:** Pure numpy + cv2, no new deps. Changes only in `rgbd_device_prompt_builder.py`.

---

## Task 1: Add `enable_stage2_growth` and `stage2_dilate_px` to `RgbdPromptConfig`

**Objective:** Add two new config fields to control the two-stage growth.

**Files:**
- Modify: `viewer/rgbd_device_prompt_builder.py` — `RgbdPromptConfig` dataclass (line 13)

**Step 1: Add fields**

After line 26 (`rgb_edge_requires_depth_jump: bool = False`), add:

```python
    enable_stage2_growth: bool = True
    stage2_dilate_px: int = 8
    stage2_max_radius_px: int = 250
```

**Step 2: Verify**

```bash
uv run python -c "from rgbd_device_prompt_builder import RgbdPromptConfig; c = RgbdPromptConfig(); assert c.enable_stage2_growth; print('OK')"
```

---

## Task 2: Add `_grow_depth_component_stage2()` function

**Objective:** A BFS variant that ignores RGB edges, grows past texture boundaries to capture same-depth enclosing object.

**Files:**
- Modify: `viewer/rgbd_device_prompt_builder.py` — new function after `_grow_depth_component` (after line 133)

**Step 1: Write function**

```python
def _grow_depth_component_stage2(
    depth: np.ndarray,
    seed_mask: np.ndarray,
    seed_depth: float,
    config: RgbdPromptConfig,
) -> np.ndarray:
    """Grow *past* RGB edges to find enclosing object at same depth.

    Unlike _grow_depth_component, this ignores texture edges entirely.
    Seed_mask provides the starting region (e.g. dilated stage-1 component).
    Growth stops at: depth out-of-range, local depth jumps, image boundary.
    """
    h, w = depth.shape
    x0 = max(0, seed_mask.any(axis=0).argmax() - config.stage2_max_radius_px)
    x1 = min(w, w - seed_mask[:, ::-1].any(axis=0).argmax() + config.stage2_max_radius_px)
    y0 = max(0, seed_mask.any(axis=1).argmax() - config.stage2_max_radius_px)
    y1 = min(h, h - seed_mask[::-1, :].any(axis=1).argmax() + config.stage2_max_radius_px)
    
    crop = depth[y0:y1, x0:x1].astype(np.float32, copy=False)
    valid = np.isfinite(crop) & (crop > 0)
    global_ok = valid & (np.abs(crop - np.float32(seed_depth)) <= np.float32(config.global_depth_span_m))
    if not global_ok.any():
        return seed_mask.copy()
    
    local_jump = max(float(config.local_depth_jump_m), abs(float(seed_depth)) * float(config.local_depth_jump_rel))
    h_crop, w_crop = crop.shape
    
    component = seed_mask[y0:y1, x0:x1].copy()
    # Initialize queue from all seed pixels at the component border
    border = cv2.dilate(component.astype(np.uint8), np.ones((3, 3), np.uint8)) & ~component
    queue = list(zip(*np.where(border & global_ok)))
    for bx, by in queue:
        if not component[by, bx]:
            component[by, bx] = True
    
    head = 0
    while head < len(queue):
        cx, cy = queue[head]
        head += 1
        current_depth = float(crop[cy, cx])
        for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
            if nx < 0 or nx >= w_crop or ny < 0 or ny >= h_crop or component[ny, nx]:
                continue
            if not global_ok[ny, nx]:
                continue
            if abs(float(crop[ny, nx]) - current_depth) > local_jump:
                continue
            # NOTE: no RGB edge check — this is the key difference from stage 1
            component[ny, nx] = True
            queue.append((nx, ny))
    
    full = seed_mask.copy()
    full[y0:y1, x0:x1] = component
    return full
```

**Step 2: Verify import/compile**

```bash
uv run python -c "from rgbd_device_prompt_builder import _grow_depth_component_stage2; print('OK')"
```

---

## Task 3: Wire two-stage into `build_rgbd_device_prompt()`

**Objective:** After getting the stage-1 component (RGB-edge-blocked), if `enable_stage2_growth`, dilate it and run stage-2 growth to capture the enclosing object.

**Files:**
- Modify: `viewer/rgbd_device_prompt_builder.py` — `build_rgbd_device_prompt()` (line 249)

**Step 1: Add stage-2 logic after line 267**

After `component = _largest_or_seed_component(component, seed_x, seed_y)`, add:

```python
    # Stage 2: grow past texture edges to capture enclosing object
    if config.enable_stage2_growth and component.any():
        stage1_component = component.copy()
        stage1_area = component_area
        dilated = cv2.dilate(
            component.astype(np.uint8),
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (config.stage2_dilate_px * 2 + 1, config.stage2_dilate_px * 2 + 1)),
            iterations=1,
        ).astype(bool)
        stage2 = _grow_depth_component_stage2(depth, dilated, seed_depth, config)
        stage2_area = int(np.count_nonzero(stage2))
        # Only use stage-2 if meaningfully larger AND not exceeding max area ratio
        stage2_bbox = _mask_bbox(stage2, config.bbox_pad_px)
        stage2_bbox_ratio = 0.0
        if stage2_bbox is not None:
            x0, y0, x1, y1 = stage2_bbox
            stage2_bbox_ratio = float((x1 - x0 + 1) * (y1 - y0 + 1)) / float(depth.shape[0] * depth.shape[1])
        if (stage2_area > int(stage1_area * 1.5)
                and stage2_bbox_ratio <= float(config.max_component_area_ratio)):
            component = stage2
            component_area = stage2_area
        else:
            # Fall back to stage-1 — don't lose the original component
            component = stage1_component
            component_area = stage1_area
```

**Step 2: Verify full pipeline import**

```bash
uv run python -c "from rgbd_device_prompt_builder import build_rgbd_device_prompt, RgbdPromptConfig; print('OK')"
```

---

## Task 4: Add `--seg-enable-stage2` and `--seg-stage2-dilate-px` CLI args to viewer

**Objective:** Expose the new config as CLI flags in the viewer.

**Files:**
- Modify: `viewer/quest3_rgbd_align_viewer.py` — `parse_args()` and `run_device_segmentation()`

**Step 1: Add args to `parse_args()`**

Find the `--seg-depth-ignore-texture-edges` argument and add after it:

```python
    parser.add_argument("--seg-enable-stage2", action="store_true", default=True,
                        help="Enable stage-2 BFS growth past texture edges (default: on)")
    parser.add_argument("--seg-disable-stage2", action="store_false", dest="seg_enable_stage2",
                        help="Disable stage-2 BFS growth")
    parser.add_argument("--seg-stage2-dilate-px", type=int, default=8,
                        help="Dilation radius before stage-2 growth (default: 8)")
```

**Step 2: Pass to RgbdPromptConfig in `run_device_segmentation()`**

Find the `RgbdPromptConfig(` call (around line 984) and add:

```python
            enable_stage2_growth=args.seg_enable_stage2,
            stage2_dilate_px=args.seg_stage2_dilate_px,
```

**Step 3: Verify**

```bash
uv run python tools/quest3_rgbd_align_viewer.py --help 2>&1 | grep -c stage2
```

---

## Task 5: Commit

```bash
git add viewer/rgbd_device_prompt_builder.py viewer/quest3_rgbd_align_viewer.py
git commit -m "feat(viewer): two-stage depth component growth past texture edges

Stage 1: RGB-edge-blocked BFS (existing behavior, captures texture region)
Stage 2: dilated → re-grown without RGB edge blocking (captures enclosing object)

Controlled by --seg-enable-stage2 (default: on) and --seg-stage2-dilate-px (default: 8).
Fixes fruit-on-bottle case where click on texture region should select whole object."
```
