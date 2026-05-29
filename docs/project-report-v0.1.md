# Smart Room — Project Report

**MSc Connected Environments · University College London**
**Hardware: Meta Quest 3 (RGB + Depth Cameras, IMU)**
**v0.1 — May 2026**

---

## 1. Project Vision

The Smart Room project aims to build a real-time spatial understanding system on a consumer AR headset. At its core, the system enables a user wearing Quest 3 to point at any object in their physical environment and receive immediate semantic feedback — the object's identity, its precise spatial bounds, and its 3D position — overlaid directly onto the passthrough view.

This maps to the Connected Environments dissertation framework of **Sense → Communicate → Display**:

| Layer | What | Implementation |
|-------|------|----------------|
| **Sense** | Real-time RGB-D perception of the indoor environment | Quest 3 Passthrough Camera API + EnvironmentDepthManager → WebSocket streaming to PC backend |
| **Communicate** | Open-vocabulary object detection, segmentation, and semantic labeling | Florence-2 + SAM2 two-stage inference pipeline (GPU backend) |
| **Display** | 3D-anchored AR overlay of detection results | Unity LineRenderer bounding boxes + TextMesh labels at world coordinates |

---

## 2. Current Implementation

### 2.1 What Works Today

- **RGB-D streaming pipeline:** Quest 3 → WebSocket binary protocol → Python backend (FastAPI, port 8500). RGB JPEG frames + raw depth arrays + camera intrinsics streamed at configurable rates.
- **Depth point cloud rendering:** EnvironmentDepthManager GPU readback → 3D world-space point cloud with EDL luminance shading and normal-map coloring. Temporal smoothing + 5-frame miss tolerance for depth cursor stability.
- **TriggerDepthProbe v2:** Controller raycast → EnvironmentRaycastManager.Raycast → 3D Euclidean-thinned depth probe with sphere boundary ring and normal→RGB coloring.
- **Trigger-to-track detection pipeline:** Two-stage Florence-2 `<OD>` → SAM2 box-prompt inference. User points depth cursor, pulls trigger, backend detects object at that pixel coordinate in 1.8s.
- **PixelProjector:** Dynamic resolution synchronization between RGB stream and depth frame — crucial for coordinate alignment.
- **Dashboard:** Multi-tab web UI (Status/Preview/Tracking/Logs) for monitoring and manual trigger testing.

### 2.2 Architecture

```
Quest 3 (Unity 6.3, URP17, Meta SDK v85)
  ├─ RGB stream (Passthrough Camera API → JPEG, 640×360)
  ├─ Depth stream (EnvironmentDepthManager → raw float32 arrays)
  ├─ Camera intrinsics (PCA → scaled to target resolution)
  ├─ DepthCursor (raycast-based pointing)
  └─ TrackingManager (WebSocket client for bbox/label overlay)
         │
    WebSocket (port 8500, adb reverse)
         │
         ▼
Python Backend (FastAPI + uvicorn, RTX 3070Ti)
  ├─ main.py: WebSocket ingest → parse RGB/Depth/Heartbeat
  ├─ tracking/engine.py: Florence-2 <OD> → SAM2 box-prompt
  └─ run_dashboard.py: multi-tab monitoring UI
```

---

## 3. Research Context & Identified Gaps

### 3.1 Indoor Spatial AI is Underdeveloped

The 2025 Semantic Mapping survey ([arXiv:2501.05750](https://arxiv.org/html/2501.05750v1)) identifies the key frontier: *"open-vocabulary, queryable, task-agnostic map representations"* — with high memory demands and computational inefficiency as open challenges. Most 3D scene understanding research uses offline datasets (ScanNet, SUN RGB-D, ARKitScenes). Real-time indoor spatial AI on consumer headsets remains virtually unexplored in published work.

### 3.2 Open-Vocabulary + Depth Fusion is a Real Gap

Open-YOLO 3D (ICLR 2025), OpenM3D (2025), and RAZER (2025) all tackle open-vocabulary 3D object detection from RGB-D — but all operate offline on pre-recorded datasets. BoxFusion ([arXiv:2506.15610](https://arxiv.org/abs/2506.15610v1)) is the first reconstruction-free real-time approach, but targets autonomous driving scale (>1000m²). Room-scale consumer AR is a distinct, unexplored setting.

### 3.3 Pointing-Based Interaction is a Frontier

EgoPoint-Ground ([arXiv:2603.26646](https://arxiv.org/pdf/2603.26646), March 2026) published the first large-scale first-person hand-pointing visual grounding dataset (15k+ samples). Their core argument: *"In physical interactions, the synergy between hand gestures and language constitutes the most natural and efficient referring mechanism."* However, their work is dataset+benchmark — no real-time AR implementation exists.

Pointing3D (CoRL 2025) and Pointing-Based Object Recognition ([arXiv:2603.15403](https://arxiv.org/abs/2603.15403v1)) both demonstrate that depth information significantly improves pointing-based target identification. Our system's depth cursor is a direct implementation of this principle — but the literature lacks end-to-end AR systems that combine pointing + depth + open-vocabulary detection.

### 3.4 Indoor Navigation Remains Challenging

A systematic review in Virtual Reality (2025) confirms: *"Performance improvements associated with AR have been consistently demonstrated in indoor environments, but researchers have not found similar advantages in outdoor environments"* — and identifies localization accuracy, real-time performance, and accessibility as persistent challenges. The HoloLens-based navigation literature (2024) shows that existing solutions rely on pre-built spatial maps (Azure Spatial Anchors) or QR codes — none support navigation in unmapped, unknown rooms.

---

## 4. Proposed Project Blocks

### Block 1 — Sense: Real-Time RGB-D Spatial Capture Pipeline ✓ (Complete)

**Problem:** Quest 3 Passthrough Camera API only went public in v76 (2025). No established open-source pipeline exists for synchronized RGB + depth + pose streaming to an external processing backend.

**Method:** Unity Passthrough Camera API → JPEG compression → WebSocket binary protocol → Python FastAPI ingestion. Depth via EnvironmentDepthManager GPU readback. Coordinate alignment via dynamic resolution synchronization (PixelProjector).

**Status:** Complete. Point cloud rendering, depth cursor, and data streaming all operational.

---

### Block 2 — Sense: Open-Vocabulary Object Detection & Semantic Labeling ✓ (Complete, v1)

**Problem:** Traditional object detectors are closed-set. Indoor environments contain arbitrary, unpredictable objects. The system must identify objects without pre-registered categories. Florence-2, Grounding DINO, and YOLO-World enable this — but their real-time trigger-based integration with a headset pointing interface is novel.

**Method:** Two-stage pipeline:
1. Florence-2 `<OD>` → all detection bboxes + class labels
2. Find smallest bbox containing the cursor point → SAM2 box-prompt → refined mask → tight bbox

Fallback: `<OPEN_VOCABULARY_DETECTION>` if `<OD>` returns nothing. Florence-2 `<REGION_TO_DESCRIPTION>` for label generation in point-prompt fallback mode.

**Status:** Complete. ~1.8s inference on RTX 3070Ti. Clean labels after `<LOC_xxx>` token filtering.

**Remaining issues (see Section 5):**
- Detection recall — `<OD>` sometimes misses small/occluded objects
- Bbox accuracy — Florence-2 bboxes can be loose; SAM2 refinement helps but edge cases exist
- Label quality — generic labels ("furniture") instead of descriptive ones ("wooden desk")

---

### Block 3 — Communicate: 2D Detection → 3D Spatial Anchoring (In Progress)

**Problem:** 2D bbox + label is useful for image understanding, but an AR system needs 3D world coordinates. Converting a single-frame 2D detection to a stable 3D position using depth data — without pre-built 3D maps — is an open problem highlighted by Open-YOLO 3D and BoxFusion.

**Method:** Depth cursor hit position → back-project bbox center using depth map sampling → 3D world coordinate. Head pose tracking for coordinate updates. Potential: Spatial Anchor persistence across sessions.

**Status:** Partial. Center pixel → 3D coordinate conversion works. Multi-view consistency, temporal stability, and occlusion handling need validation.

---

### Block 4 — Communicate: Object Tracking Across Frames (Planned)

**Problem:** After initial detection, the user moves their head or the object moves. The system must maintain object identity and bbox without re-running the expensive detection pipeline every frame.

**Method candidates:**
1. SAM2 Video Predictor propagation (high accuracy, ~200ms/frame, memory overhead)
2. Lightweight 2D tracker (KCF/NanoTrack/optical flow) + periodic Florence-2 re-detection (low latency, lower accuracy)

**Status:** Not implemented in current v1. SAM2.1 video tracking was attempted and rolled back due to API compatibility issues with streaming (vs. pre-recorded video). Needs re-evaluation with alternative approach.

---

### Block 5 — Display: 3D-Anchored AR Semantic Overlay ✓ (Complete, v1)

**Problem:** Rendering detection results as spatially coherent 3D overlays requires correct depth ordering, perspective alignment, and readable text in passthrough AR. Misaligned overlays degrade user trust and situational awareness (Virtual Reality, 2025).

**Method:** LineRenderer bounding box + TextMesh/TextMeshPro label at 3D world coordinates. "TRACK LOST" state with red visual feedback.

**Status:** Complete. Bbox rendering works. 3D bbox sizing from 2D-pixel-to-world conversion needs multi-scenario validation.

---

### Block 6 — Display (Advanced): Indoor Semantic Map (Planned)

**Problem:** The system currently handles one object at a time. For indoor navigation (a core Connected Environments concern), it needs to understand what objects exist in the room, where they are, and how to navigate among them. This is the exact gap identified by the Semantic Mapping survey: building queryable, open-vocabulary spatial maps in real-time.

**Method:** Cumulative multi-frame detection → depth back-projection → 3D semantic point cloud. Instance merging via spatial proximity + CLIP feature similarity. Optional: NavMesh path planning between detected objects.

**Research contribution:** First real-time open-vocabulary semantic mapping system on consumer AR hardware. Directly addresses the supervisor's indoor navigation focus and the literature's identified gaps around "open-vocabulary, queryable, task-agnostic map representations."

**Status:** Planned. Depends on Block 3 (2D→3D anchoring) being robust first.

---

## 5. Accuracy Improvement Plan (Current Priority)

> **Constraint:** Base models (Florence-2-base, SAM2.1-hiera-tiny) must NOT be modified or replaced. New models/techniques can be added as supplementary pipeline components.

### 5.1 Florence-2 Detection Accuracy

**Problem:** `<OD>` task sometimes misses objects or returns imprecise bboxes, especially for small, partially occluded, or unusual objects.

**Proposed improvements:**

1. **Multi-task ensemble detection.** Run multiple Florence-2 tasks on the same frame and merge results:
   - `<OD>` → coarse bboxes + class labels
   - `<DENSE_REGION_CAPTION>` → descriptive labels with bboxes (often catches objects `<OD>` misses)
   - `<CAPTION>` + `<REGION_TO_DESCRIPTION>` → full-scene understanding for context
   - Merge bboxes via weighted IoU voting; prefer `<DENSE_REGION_CAPTION>` labels over `<OD>` labels when both exist (richer descriptions)

2. **CLIP-based label verification.** After Florence-2 returns a label, crop the bbox region → encode with CLIP → compare cosine similarity against label text embedding. Reject or re-label detections below a confidence threshold. This catches cases where Florence-2 mislabels (e.g., "chair" for a stool).

3. **Confidence calibration.** Florence-2 `<OD>` returns no native confidence scores — we currently hardcode 0.85. Use the CLIP similarity score as a calibrated confidence proxy instead.

### 5.2 SAM2 Segmentation & Bbox Accuracy

**Problem:** Single box-prompt to SAM2 gives decent masks, but the bbox derived from the mask can still be imprecise at boundaries, especially for objects with complex shapes or low contrast against background.

**Proposed improvements:**

1. **Multi-point positive prompts from bbox interior.** Instead of one bounding box → one mask:
   - Sample N positive points from within the Florence-2 detection bbox (uniform grid or random)
   - Run SAM2 with `point_coords` + `point_labels=1` (N points, all positive)
   - This gives SAM2 richer spatial cues about which region to segment
   - Refer to SAM2 docs: multi-point prompts are officially supported and improve edge accuracy

2. **Negative point prompts from bbox exterior.** Sample negative points just outside the bbox boundary → pass as `point_labels=0`. This explicitly tells SAM2 "not this region," reducing over-segmentation into background.

3. **Mask confidence thresholding.** SAM2's `predict()` returns `scores`. Filter masks below a quality threshold (e.g., `score < 0.7` → use detection bbox as-is instead of mask-derived bbox).

4. **GrabCut post-refinement (optional).** If SAM2 mask boundaries are visibly wrong, apply OpenCV GrabCut with the SAM2 mask as initialization + Florence-2 bbox as the bounding rectangle. This is a classic CV technique that can refine edges using color distribution — useful for furniture against walls.

### 5.3 Depth-Guided Accuracy

**Problem:** Our system has depth data but doesn't use it for detection/segmentation — only for 3D back-projection. Depth can disambiguate objects that are visually similar but at different distances.

**Proposed improvements:**

1. **Depth-based non-maximum suppression (NMS).** When two Florence-2 detection bboxes overlap significantly but have different depth values at their centers → they're likely different objects (e.g., a chair in front of a table). Traditional 2D NMS would suppress one. Depth-aware NMS keeps both.

2. **Depth consistency check on SAM2 mask.** After SAM2 generates a mask, sample depth values within the mask region. If depth variance is too high (> threshold suggesting the mask spans multiple depth planes) → the mask likely includes background → either:
   - Re-run SAM2 with stricter prompts
   - Erode the mask to keep only depth-consistent pixels
   - Flag as low-confidence

3. **Depth edge alignment for bbox refinement.** If the mask-derived bbox boundary sits on a depth edge (large depth gradient), the boundary is likely correct. If it sits in a region of uniform depth, it might be cutting through the object. Use Canny edge detection on the depth map as an additional cue to expand/contract bbox edges.

### 5.4 Temporal Consistency

**Problem:** Currently single-frame detection only. No mechanism to improve results by leveraging information from previous frames.

**Proposed improvements:**

1. **Running average bbox smoothing.** After detecting the object across N frames, maintain an exponentially weighted moving average of bbox coordinates. This reduces jitter from frame-to-frame detection variance.

2. **Label consensus voting.** If the user points at the same object region across multiple frames, accumulate Florence-2 labels and take the majority vote (or highest CLIP-similarity label). This catches single-frame mislabeling.

### 5.5 Implementation Priority

| Priority | Technique | Expected Impact | Effort |
|----------|-----------|----------------|--------|
| 1 | Multi-task ensemble (`<OD>` + `<DENSE_REGION_CAPTION>`) | +Recall, +Label quality | Medium |
| 2 | CLIP label verification | +Label accuracy | Low |
| 3 | SAM2 multi-point positive prompts | +Mask/Bbox precision | Low |
| 4 | Depth consistency check | +Mask/Bbox precision | Medium |
| 5 | Depth-aware NMS | +Recall (multi-object) | Medium |
| 6 | Temporal bbox smoothing | +Stability | Low |
| 7 | SAM2 negative point prompts | +Edge precision | Low |
| 8 | GrabCut post-refinement | +Edge precision (edge cases) | Low |

---

## 6. References

1. Xiao, B. et al. (2024). *Florence-2: Advancing a Unified Representation for a Variety of Vision Tasks.* CVPR 2024. [arXiv:2311.06242](https://arxiv.org/abs/2311.06242)
2. Ravi, N. et al. (2024). *SAM 2: Segment Anything in Images and Videos.* [arXiv:2408.00714](https://arxiv.org/html/2408.00714v2)
3. IDEA Research. (2024). *Grounded SAM 2: Ground and Track Anything in Videos.* [GitHub](https://github.com/IDEA-Research/Grounded-SAM-2)
4. Mahasneh, M. (2025). *Florence-2 + SAM 2 Pipeline.* [GitHub](https://github.com/MjdMahasneh/florance2-sam2)
5. Singh, K. et al. (2025). *Semantic Mapping in Indoor Embodied AI — A Comprehensive Survey.* [arXiv:2501.05750](https://arxiv.org/html/2501.05750v1)
6. Hajd, L. (2026). *Pointing-Based Object Recognition.* [arXiv:2603.15403](https://arxiv.org/abs/2603.15403v1)
7. EgoPoint-Ground. (2026). *First Large-Scale Multimodal Dataset for Egocentric Deictic Visual Grounding.* [arXiv:2603.26646](https://arxiv.org/pdf/2603.26646)
8. Arslanoglu, E. et al. (2025). *Pointing3D: A Benchmark for 3D Object Referral via Pointing Gestures.* CoRL 2025. [PMLR](https://proceedings.mlr.press/v305/arslanoglu25a.html)
9. Nguyen, T. et al. (2025). *Open-YOLO 3D: Towards Fast and Accurate Open-Vocabulary 3D Instance Segmentation.* ICLR 2025.
10. BoxFusion. (2025). *Reconstruction-Free Open-Vocabulary 3D Object Detection via Real-Time Multi-View Box Fusion.* [arXiv:2506.15610](https://arxiv.org/abs/2506.15610v1)
11. RAZER. (2025). *Robust Accelerated Zero-Shot 3D Open-Vocabulary Panoptic Reconstruction.* [arXiv:2505.15373](https://arxiv.org/html/2505.15373)
12. OpenM3D. (2025). *Open Vocabulary Multi-view Indoor 3D Object Detection.* [arXiv:2508.20063](https://arxiv.org/pdf/2508.20063)
13. Shulgach, J. (2025). *Grounded SAM 2 Stream.* [GitHub](https://github.com/Jshulgach/Grounded-SAM-2-Stream)
14. SAMRefiner. (2025). *Taming Segment Anything Model for Universal Mask Refinement.* [arXiv:2502.06756](https://arxiv.org/html/2502.06756v1)
15. Fang, C. et al. (2024). *WatchThis: A Wearable Point-and-Ask Interface powered by Vision-Language Models.* UIST 2024.
16. ObjectFinder. (2024). *An Open-Vocabulary Assistive System for Interactive Object Search by Blind People.* [arXiv:2412.03118](https://arxiv.org/html/2412.03118)
17. Meta. (2025). *Passthrough Camera API v76.* [Developer Blog](https://developers.meta.com/horizon/blog/new-era-mixed-reality-passthrough-camera-api-machine-learning-computer-vision/)
18. Virtual Reality (Springer). (2025). *Use of Augmented Reality in Human Wayfinding: A Systematic Review.*
