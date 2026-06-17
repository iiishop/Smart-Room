# RGB-D Cursor-Conditioned Device Segmentation Pipeline

Date: 2026-06-17
Branch: `iiishop/rgbd-object-seg-pipeline`

## Goal

The tracking pipeline targets lab-device association from a Quest 3 cursor
selection. The visual side should first produce a stable device instance mask
and geometric evidence. Recognition models then describe the segmented device
for matching against backend/network-discovered candidates.

This replaces the previous detector-first assumption:

```text
Florence-2 bbox -> SAM2 mask -> label verification
```

with a cursor-first assumption:

```text
Quest cursor + SAM2 point mask + RGB-D geometry -> device proposal
device proposal -> local/VLM visual evidence -> backend association
```

## Rationale

Lab equipment is often not a clean COCO-style object. A 3D printer, Pi cluster,
bench instrument, rack module, cable bundle, or development kit may be detected
as a partial object or not detected at all by a generic detector. The cursor and
aligned RGB-D frame provide stronger supervision for "which physical device is
being selected" than a 2D object detector.

## Implemented Modules

### `tracking/rgbd_final_alignment.py`

Runtime backend copy of the final Quest 3 alignment path. It accepts the same
logical artifacts produced by `Quest3RgbdCaptureFinal.cs`:

- `rgb.jpg`
- `depth.raw` float32 EnvironmentDepth values in `[0, 1]`
- `meta.json` with PCA intrinsics/pose, depth descriptor pose/FOV, and
  `descriptor_reprojection_matrix`

It converts raw depth to metres, unprojects depth pixels to world space,
projects them into the RGB camera frame, and returns a sparse depth map in RGB
pixel coordinates.

### `tracking/rgbd_proposal.py`

`CursorRGBDDeviceProposer` builds a device proposal from:

- SAM2 point mask at the cursor.
- Aligned depth map in RGB pixel coordinates.
- Optional RGB camera intrinsics for 3D center estimates.

Implemented stages:

1. Cursor depth seed estimation from a local valid-depth window.
2. Depth hole filling for sparse aligned depth.
3. Depth flood fill around the cursor using absolute and local continuity.
4. Conservative support-plane rejection using RANSAC when intrinsics exist.
5. Primary mask fusion: `sam_mask` intersected with cursor depth component.
6. Whole-device expansion around the primary mask and depth component.
7. Optional SAM2-refined mask fusion.
8. Output geometry and diagnostics.

### `tracking/part_proposal.py`

`DevicePartProposalGenerator` extracts cheap local part crops inside the device
mask:

- `panel_or_screen`
- `indicator_light`
- `port_or_vent`
- `cable_or_edge`

These are not final semantic labels. They are evidence crops for VLM/OCR and
device-association scoring.

### `tracking/device_evidence.py`

Provides:

- Local structured visual evidence builder.
- Optional OpenAI-compatible VLM provider.

Environment variables:

```text
QUEST3_VLM_ENABLED=1
QUEST3_VLM_BASE_URL=https://.../v1
QUEST3_VLM_API_KEY=...
QUEST3_VLM_MODEL=qwen-vl-plus
QUEST3_VLM_TIMEOUT_S=12
QUEST3_VLM_MAX_PART_CROPS=6
```

The provider sends the masked whole-device crop, context crop, part crops, and
geometry metadata. It expects strict JSON with device category, possible device
types, visual features, visible text, ports, indicators, colors/materials, and
association hints.

## Tracking API Integration

`TrackingEngine.detect()` now accepts:

```python
detect(
    pixel_x,
    pixel_y,
    rgb_bgr,
    aligned_depth_m=None,
    rgb_intrinsics=None,
)
```

The correct live RGB-D entrypoint is:

```text
POST /api/track/start-final-rgbd
```

Payload:

```json
{
  "pixel_x": 512.0,
  "pixel_y": 384.0,
  "rgb_jpeg_b64": "...",
  "depth_raw_f32_le_b64": "...",
  "meta_json": "{... Quest3RgbdCaptureFinal meta.json ...}",
  "trigger_bundle_meta": {}
}
```

The backend decodes the final capture payload, runs the same alignment math as
`backend/tools/quest3_rgbd_align_final.py`, caches the aligned depth for
dashboard/debug endpoints, then calls `TrackingEngine.detect()` with:

```python
aligned_depth_m=alignment.aligned_depth_m
rgb_intrinsics=alignment.rgb_intrinsics
```

For offline replay/debugging:

```text
POST /api/track/start-final-rgbd-capture-dir
```

accepts a directory containing `rgb.jpg`, `depth.raw`, and `meta.json`, then
runs the same backend runtime alignment.

Legacy RGB-D tracking fallbacks are disabled. `/api/track/start`,
`/api/depth/aligned`, and `/api/depth/aligned-v2` return HTTP 410 so accidental
old-path calls are visible immediately. The only supported click-to-segment
RGB-D path is `/api/track/start-final-rgbd`.

The result payload keeps old fields and adds:

```json
{
  "mask_rle": {},
  "mask_area": 12345,
  "center_3d_m": [0.1, 0.2, 1.3],
  "depth_median_m": 1.3,
  "depth_confidence": 0.82,
  "segmentation_source": "rgbd_cursor_sam2+sam2_refined",
  "segmentation_confidence": 0.88,
  "parts": [],
  "visual_evidence": {},
  "diagnostics": {}
}
```

## Runtime Behavior

With aligned depth:

```text
Quest3RgbdCaptureFinal rgb.jpg/depth.raw/meta.json
  -> backend final RGB-D alignment
  -> SAM2 cursor mask
  -> RGB-D cursor depth component
  -> primary device mask
  -> SAM2 proposal refinement
  -> whole-device mask
  -> part proposals
  -> local Florence/SigLIP hint
  -> optional VLM structured evidence
```

Without aligned depth:

```text
SAM2 cursor mask
  -> RGB-only proposal fallback
  -> SAM2 proposal refinement
  -> part proposals
  -> local/VLM evidence
```

## Validation

Implemented tests:

- Cursor depth component is preferred over a neighboring object at a different
  depth even when the SAM2-like mask covers both.
- RGB fallback works when depth is missing.
- `TrackingResult` serializes extended mask/depth/evidence fields.

Command:

```text
python -m pytest tests\test_rgbd_device_proposal.py -p no:cacheprovider
```

## Known Limitations

- Whole-device expansion is conservative. It avoids leaking into tables/walls
  but may under-segment multi-part equipment connected by thin cables or sparse
  depth holes.
- Support-plane rejection is disabled when intrinsics are unavailable.
- The first version uses OpenCV/NumPy rather than Open3D to avoid adding a heavy
  dependency. Open3D can be introduced later for richer point-cloud clustering.
- VLM evidence is optional and disabled unless configured through environment
  variables.
- Florence-2 still loads during current model warm-up because existing local
  semantic helpers depend on it. A later optimization can split SAM2-only warmup
  from semantic model warmup.

## Next Iterations

1. Persist the last `mask_rle` and expose `/api/track/last-mask-overlay`.
2. Add OCR-focused crops for labels, small screens, and printed model numbers.
3. Add operation feedback: after backend toggles a candidate device, measure
   visual change inside the device mask and part masks.
4. Add association scoring between `visual_evidence` and discovered devices.
5. Add scene-level device map by accumulating cursor-conditioned proposals over
   time.
