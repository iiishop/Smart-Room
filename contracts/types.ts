export type VisionFrameMode = "detection" | "propagation";

export type VisionMaskSize = [height: number, width: number];

export type VisionBoxXyxy = [
  left: number,
  top: number,
  right: number,
  bottom: number,
];

export interface VisionMaskRle {
  size: VisionMaskSize;
  counts: number[];
}

export interface VisionColor32 {
  r: number;
  g: number;
  b: number;
  a: number;
}

export interface VisionOverlayConfig {
  enabled: boolean;
  showBoundingBoxes: boolean;
  showLabels: boolean;
  showAnchors: boolean;
  boxLineWidth: number;
  labelHeightOffset: number;
  objectColors: VisionColor32[];
}

export interface VisionTrackedObject {
  object_id: number;
  label: string;
  score: number;
  box_xyxy: VisionBoxXyxy;
  area: number;
  mask_rle: VisionMaskRle;
}

export interface VisionGpuMemoryMb {
  allocated: number;
  max_allocated: number;
}

export interface VisionFrameResult {
  frame_id: number;
  timestamp_ms: number;
  frame_width: number;
  frame_height: number;
  prompt: string;
  source: string;
  objects: VisionTrackedObject[];
  mode?: VisionFrameMode;
  process_time_ms?: number;
  gpu_memory_mb?: VisionGpuMemoryMb;
}

export type VisionVector3 = [x: number, y: number, z: number];

export interface VisionProcessedObject {
  object_id: number;
  label: string;
  score: number;
  corners_3d: VisionVector3[];
  center_3d: VisionVector3;
  contour_3d: VisionVector3[];
  corners_valid: boolean;
  center_valid: boolean;
}

export interface VisionProcessedFrame {
  frame_id: number;
  timestamp_ms: number;
  objects: VisionProcessedObject[];
}
