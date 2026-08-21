## Pre-Mortem: Two-Stage Depth Component Growth

_Assuming it ships and fails — what went wrong?_

### Tigers

**T1 — local_depth_jump blocks stage-2 too.** The plan removes RGB edge blocking in stage-2, but still checks `local_depth_jump_m` (default 0.06m = 6cm). If the fruit-on-bottle creates a small depth artifact (curved surface, 2-3mm), stage-2 passes. But if the bottle has a ridge or the print is embossed, the depth jump could be larger. **Mitigation**: set `local_depth_jump` slightly higher for stage-2 (1.5x), or relax it when the jump neighbor is within `global_depth_span_m`. **Classification**: Fast-Follow — test on real data, tune threshold.

**T2 — stage-2 overshoots into wrong objects.** Without RGB edges, same-depth table/wall/chair all get merged. The `global_depth_span_m` (0.55m) and `local_depth_jump_m` should catch depth transitions, but walls at same distance will be included. The downstream `max_component_area_ratio` (0.08) rejects giant components, but then stage-2 has nothing to fall back to — the old component was already replaced. **Mitigation**: If stage-2 result exceeds `max_component_area_ratio`, fall back to stage-1 component instead of zeroing. Add in Task 3. **Classification**: Launch-Blocking — must handle graciously.

### Paper Tigers

**P1 — Performance impact.** Stage-2 adds a second BFS, bounded by `stage2_max_radius_px` (250px). BFS is O(pixels) in the crop window (~250K pixels). Negligible (< 5ms on CPU). Not a real concern.

### Elephants

**E1 — `1.5x` area growth threshold might be wrong.** If the fruit is 80% of the bottle area, stage-2 only captures the remaining 20% and fails the check. Is that a problem? Probably not — when fruit covers most of the bottle, SAM2 with bbox padding already captures everything. But the threshold could use real calibration. **Action**: gather before/after area ratios on 5-10 real captures, tune.

### Action Plans

| Risk | Mitigation | Owner | Due |
|------|-----------|-------|-----|
| T2: stage-2 overshoot rejected | If stage-2 area > max ratio, keep stage-1 as-is (don't zero) | OpenCode | Now |
| T1: depth jump blocks stage-2 | Test on real data, tune threshold if needed | User | After merge |
