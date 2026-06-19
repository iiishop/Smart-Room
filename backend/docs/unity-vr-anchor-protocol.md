# VR Anchor Protocol - Unity <-> Viewer

## POST /api/track/start-final-rgbd

Existing fields:
- `rgb_jpeg` or `rgb_raw`
- `depth_raw`
- `meta_json`
- `cursor_json`

New optional fields:
- `anchor_points_json`: JSON string like `[{"x": 1.2, "y": 0.5, "z": -0.8, "label": 1}]`
- `re_predict`: `"true"` or `"false"`

Anchor rules:
- `label > 0` means positive prompt
- `label <= 0` means negative prompt
- coordinates are Unity world-space metres

## Response

Existing response fields stay the same.

New field:
- `contour_3d`: `[[x, y, z], ...]` world-space contour points for a Unity `LineRenderer`

## Unity Notes

1. Keep placed anchors in world space.
2. Send the current RGB-D payload plus `anchor_points_json` on each trigger.
3. Use `re_predict="true"` while reusing the same RGB frame embedding.
4. Reset with `re_predict="false"` when you want a fresh encode.
5. Update the `LineRenderer` from `contour_3d`.
