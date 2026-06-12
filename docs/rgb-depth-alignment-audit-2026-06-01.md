# RGB/Depth Alignment Audit

Date: 2026-06-01

Scope:
- Unity trigger -> RGB capture -> depth capture -> aligned depth upload
- Backend aligned-depth storage and dashboard preview
- Dashboard log aggregation path
- Meta XR SDK baseline: `com.meta.xr.sdk.all 85.0.0`

## Current Fact Table

| Topic | Current behavior | Source |
|---|---|---|
| Meta XR SDK baseline | Project is pinned to `com.meta.xr.sdk.all 85.0.0` | [unity/Quest3Client/Packages/manifest.json](/D:/FromGithub/UCL/CASA0022/Smart%20Room/unity/Quest3Client/Packages/manifest.json) |
| Dashboard RGB preview mode | Trigger-only, not live streaming | [backend/quest3server/run_dashboard.py](/D:/FromGithub/UCL/CASA0022/Smart%20Room/backend/quest3server/run_dashboard.py) |
| Active alignment side | Unity pre-aligns depth and POSTs `/api/depth/aligned` | [backend/quest3server/main.py](/D:/FromGithub/UCL/CASA0022/Smart%20Room/backend/quest3server/main.py) |
| Current aligned depth density | Sparse scatter result, not dense warp | [unity/Quest3Client/Assets/Scripts/Networking/DepthStreamModule.cs](/D:/FromGithub/UCL/CASA0022/Smart%20Room/unity/Quest3Client/Assets/Scripts/Networking/DepthStreamModule.cs) |
| Trigger pixel pose | Cached at trigger time via `GetCameraPose()` | [unity/Quest3Client/Assets/Scripts/Tracking/TrackingManager.cs](/D:/FromGithub/UCL/CASA0022/Smart%20Room/unity/Quest3Client/Assets/Scripts/Tracking/TrackingManager.cs) |
| Aligned depth pose | Re-read later through `BuildRgbCameraPose()` | [unity/Quest3Client/Assets/Scripts/Tracking/TrackingManager.cs](/D:/FromGithub/UCL/CASA0022/Smart%20Room/unity/Quest3Client/Assets/Scripts/Tracking/TrackingManager.cs) |
| RGB CPU capture path | `GetTexture() -> Graphics.Blit() -> ReadPixels()` | [unity/Quest3Client/Assets/Scripts/Networking/RgbStreamModule.cs](/D:/FromGithub/UCL/CASA0022/Smart%20Room/unity/Quest3Client/Assets/Scripts/Networking/RgbStreamModule.cs) |
| Depth source path | Global depth texture + zbuffer params + reprojection matrix | [unity/Quest3Client/Assets/Scripts/Networking/DepthStreamModule.cs](/D:/FromGithub/UCL/CASA0022/Smart%20Room/unity/Quest3Client/Assets/Scripts/Networking/DepthStreamModule.cs) |
| Topdown trust level | Not trustworthy yet due to approximate intrinsics | [backend/quest3server/main.py](/D:/FromGithub/UCL/CASA0022/Smart%20Room/backend/quest3server/main.py) |

## Data Flow

```text
Trigger press
  -> DepthCursor.HitPoint
  -> TrackingManager caches triggerPose and computes RGB pixel
  -> TrackingManager asks DepthStreamModule.BuildAlignedDepth(...)
  -> DepthStreamModule reads latest local depth buffer
  -> DepthStreamModule uses reprojection matrix + zbuffer params + PCA WorldToViewportPoint
  -> Unity uploads sparse aligned depth to /api/depth/aligned
  -> Unity sends pixel to /api/track/start
  -> backend stores trigger RGB frame, trigger pixel, aligned depth
  -> dashboard preview fetches /api/track/last-original
  -> dashboard preview overlays /api/depth/aligned-heatmap
```

## Dashboard Trust Table

| Surface | Current trust level | Why |
|---|---|---|
| `Logs` tab | High | Now aggregates Unity forwarded logs + backend logs + Python logger output |
| Trigger RGB preview | Medium | Trigger-correct as a saved frame, but RGB CPU capture may still lag one render-thread frame |
| Aligned depth heatmap overlay | Medium-low | Same trigger cycle, but sparse and not yet backed by a frozen trigger bundle |
| Pixel depth query `/api/depth/at` | Medium-low | Reads current sparse aligned map only; holes are expected |
| Topdown view | Low | Uses approximate RGB intrinsics in backend |

## Confirmed Facts

1. The project is pinned to Meta XR SDK `v85`.
   - Source: [unity/Quest3Client/Packages/manifest.json](/D:/FromGithub/UCL/CASA0022/Smart%20Room/unity/Quest3Client/Packages/manifest.json)

2. The dashboard preview is trigger-based, not live RGB streaming.
   - Source: [backend/quest3server/run_dashboard.py](/D:/FromGithub/UCL/CASA0022/Smart%20Room/backend/quest3server/run_dashboard.py)
   - The preview fetches `/api/track/last-original` and overlays `/api/depth/aligned-heatmap`.

3. Unity-side aligned depth is the active alignment path.
   - Source: [backend/quest3server/main.py](/D:/FromGithub/UCL/CASA0022/Smart%20Room/backend/quest3server/main.py)
   - `/api/depth/aligned` receives pre-aligned depth from Unity.

4. The current aligned depth is sparse by construction.
   - Source: [unity/Quest3Client/Assets/Scripts/Networking/DepthStreamModule.cs](/D:/FromGithub/UCL/CASA0022/Smart%20Room/unity/Quest3Client/Assets/Scripts/Networking/DepthStreamModule.cs)
   - `BuildAlignedDepth()` projects each depth sample to one RGB pixel and stores the nearest hit.
   - No splat, interpolation, or hole filling is currently applied.

5. Trigger pixel projection and aligned-depth projection do not yet share a single frozen capture bundle.
   - Source: [unity/Quest3Client/Assets/Scripts/Tracking/TrackingManager.cs](/D:/FromGithub/UCL/CASA0022/Smart%20Room/unity/Quest3Client/Assets/Scripts/Tracking/TrackingManager.cs)
   - Trigger pixel uses a cached `triggerPose`.
   - Aligned depth still calls `BuildRgbCameraPose()` later, which re-reads `GetCameraPose()`.

6. Backend top-down rendering is currently not a trustworthy geometry validator.
   - Source: [backend/quest3server/main.py](/D:/FromGithub/UCL/CASA0022/Smart%20Room/backend/quest3server/main.py)
   - `_last_rgb_intrinsics` is still populated with an approximate matrix.

## Meta XR SDK v85 Constraints Used As Baseline

1. `PassthroughCameraAccess` v85 exposes:
   - `RequestedResolution`
   - `CurrentResolution`
   - `Intrinsics`
   - `Timestamp`
   - `GetCameraPose()`
   - `WorldToViewportPoint(..., cameraPose)`
   - `ViewportPointToRay(..., cameraPose)`
   - `GetSupportedResolutions(...)`

2. `GetTexture()` is not a safe basis for "same-frame" CPU capture if paired with blocking `Graphics.Blit()`.
   - Meta v85 explicitly documents that `GetTexture()` is updated on the render thread and blocking operations can pick the previous frame.
   - This makes the current RGB capture path potentially one frame late.

3. Current project usage against v85 APIs:

| v85 API / field | Current project use | Where |
|---|---|---|
| `PassthroughCameraAccess.Timestamp` | Logged for RGB capture metadata and trigger seed timestamp | [unity/Quest3Client/Assets/Scripts/Networking/RgbStreamModule.cs](/D:/FromGithub/UCL/CASA0022/Smart%20Room/unity/Quest3Client/Assets/Scripts/Networking/RgbStreamModule.cs), [unity/Quest3Client/Assets/Scripts/Tracking/TrackingManager.cs](/D:/FromGithub/UCL/CASA0022/Smart%20Room/unity/Quest3Client/Assets/Scripts/Tracking/TrackingManager.cs) |
| `PassthroughCameraAccess.CurrentResolution` | Logged for current PCA resolution | [unity/Quest3Client/Assets/Scripts/Networking/RgbStreamModule.cs](/D:/FromGithub/UCL/CASA0022/Smart%20Room/unity/Quest3Client/Assets/Scripts/Networking/RgbStreamModule.cs) |
| `PassthroughCameraAccess.RequestedResolution` | Logged for requested PCA resolution | [unity/Quest3Client/Assets/Scripts/Networking/RgbStreamModule.cs](/D:/FromGithub/UCL/CASA0022/Smart%20Room/unity/Quest3Client/Assets/Scripts/Networking/RgbStreamModule.cs) |
| `PassthroughCameraAccess.Intrinsics.SensorResolution` | Logged for RGB sensor metadata | [unity/Quest3Client/Assets/Scripts/Networking/RgbStreamModule.cs](/D:/FromGithub/UCL/CASA0022/Smart%20Room/unity/Quest3Client/Assets/Scripts/Networking/RgbStreamModule.cs) |
| `PassthroughCameraAccess.Intrinsics.FocalLength` | Logged and used to build stream-space intrinsics | [unity/Quest3Client/Assets/Scripts/Networking/RgbStreamModule.cs](/D:/FromGithub/UCL/CASA0022/Smart%20Room/unity/Quest3Client/Assets/Scripts/Networking/RgbStreamModule.cs), [unity/Quest3Client/Assets/Scripts/Networking/PixelProjector.cs](/D:/FromGithub/UCL/CASA0022/Smart%20Room/unity/Quest3Client/Assets/Scripts/Networking/PixelProjector.cs) |
| `PassthroughCameraAccess.Intrinsics.PrincipalPoint` | Logged and used to build stream-space intrinsics | [unity/Quest3Client/Assets/Scripts/Networking/RgbStreamModule.cs](/D:/FromGithub/UCL/CASA0022/Smart%20Room/unity/Quest3Client/Assets/Scripts/Networking/RgbStreamModule.cs), [unity/Quest3Client/Assets/Scripts/Networking/PixelProjector.cs](/D:/FromGithub/UCL/CASA0022/Smart%20Room/unity/Quest3Client/Assets/Scripts/Networking/PixelProjector.cs) |
| `PassthroughCameraAccess.GetCameraPose()` | Used at trigger time and again later for aligned depth path | [unity/Quest3Client/Assets/Scripts/Tracking/TrackingManager.cs](/D:/FromGithub/UCL/CASA0022/Smart%20Room/unity/Quest3Client/Assets/Scripts/Tracking/TrackingManager.cs) |
| `PassthroughCameraAccess.WorldToViewportPoint(world, pose)` | Used to convert world hit and depth-reprojected world points into RGB viewport | [unity/Quest3Client/Assets/Scripts/Tracking/TrackingManager.cs](/D:/FromGithub/UCL/CASA0022/Smart%20Room/unity/Quest3Client/Assets/Scripts/Tracking/TrackingManager.cs), [unity/Quest3Client/Assets/Scripts/Networking/DepthStreamModule.cs](/D:/FromGithub/UCL/CASA0022/Smart%20Room/unity/Quest3Client/Assets/Scripts/Networking/DepthStreamModule.cs) |
| `PassthroughCameraAccess.ViewportPointToRay(...)` | Used by `PixelProjector` for derived pose/projection helpers | [unity/Quest3Client/Assets/Scripts/Networking/PixelProjector.cs](/D:/FromGithub/UCL/CASA0022/Smart%20Room/unity/Quest3Client/Assets/Scripts/Networking/PixelProjector.cs) |

References:
- [PassthroughCameraAccess v85](https://developers.meta.com/horizon/reference/mruk/v85/class_meta_x_r_passthrough_camera_access/)
- [Passthrough Camera API Overview](https://developers.meta.com/horizon/documentation/unity/unity-pca-overview/)

## Current Log Aggregation Chain

### Unity -> Dashboard

Current intended path:

`Unity log` -> `Application.logMessageReceived` -> `BackendCommunicationManager.QueueUnityLog(...)` -> `/ws/heartbeat` -> backend `client_log` -> `/api/logs` -> dashboard Logs tab

Notes:
- This path now lives in `BackendCommunicationManager`, not `HeartbeatModule`.
- Result: Unity log forwarding no longer depends on `HeartbeatModule` being active.

### Backend -> Dashboard

Current intended path:

`add_python_log(...)` or standard Python `logging` -> `log_manager` -> `/api/logs` -> dashboard Logs tab

Notes:
- A Python logging bridge is now installed at backend startup.
- This captures normal module loggers such as `logging.getLogger(__name__)`.
- It also attaches to `uvicorn` and `uvicorn.error`.

## What Should Be Visible In Dashboard Logs After This Audit Pass

1. Unity transport and websocket connection logs.
2. Unity `Debug.Log/Warning/Error` messages emitted after `BackendCommunicationManager` is alive.
3. Backend `add_python_log(...)` records.
4. Backend standard `logger.info/warning/error` records from modules such as:
   - `tracking.engine`
   - `tracking.depth_alignment`
   - `vision.runtime`
5. Uvicorn error-side logs routed through Python logging.

## Required Metadata Coverage For Milestone 1

The following items are now explicitly emitted into logs:

1. `RGB Timestamp`
2. `RGB CurrentResolution`
3. `RGB Intrinsics.SensorResolution`
4. `RGB Intrinsics.FocalLength`
5. `RGB Intrinsics.PrincipalPoint`
6. `trigger`-time `cameraPose`
7. depth texture actual `width/height`
8. reprojection matrix acquisition status

## Known Remaining Limits After Milestone 1

1. This audit pass improves observability, but does not yet create a strict `TriggerCaptureBundle`.
2. Dashboard still visualizes trigger-time RGB plus sparse aligned-depth overlay, not a dedicated verification UI.
3. Top-down view still depends on approximate RGB intrinsics.
4. RGB capture still uses `GetTexture() -> Graphics.Blit() -> ReadPixels()`, so same-frame fidelity is not yet guaranteed.

## Milestone 1 Exit Criteria

1. Facts about trigger-vs-live behavior are written down and source-backed.
2. Unity logs, backend logs, and Python module logger output are unified into dashboard `Logs`.
3. Trigger/RGB/depth metadata is visible enough in logs to support Milestone 2 bundle freezing work.
