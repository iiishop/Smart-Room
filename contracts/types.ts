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

export interface VisionProcessedObject {
  object_id: number;
  label: string;
  score: number;
  corners_3d: [number, number, number][];
  center_3d: [number, number, number];
  contour_3d: [number, number, number][];
  corners_valid: boolean;
  center_valid: boolean;
}

export interface VisionProcessedFrame {
  frame_id: number;
  timestamp_ms: number;
  objects: VisionProcessedObject[];
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
