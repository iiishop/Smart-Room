/**
 * BboxWireframe 渲染管线契约
 *
 * 定义 GPU buffer layout、corner pair LUT、C# 结构体与渲染参数。
 * 后端 (C#) 与前端 (Shader) 的所有实现必须对齐此契约。
 */

/** GPU StructuredBuffer 元素 — per-instance 数据，stride = 112 bytes */
export interface BboxWireframeInstanceGpu {
  /** 8 个 3D 世界空间角点坐标，每个 float3 (12 bytes) */
  corner0: [x: number, y: number, z: number]; // offset 0, 12 bytes
  corner1: [x: number, y: number, z: number]; // offset 12, 12 bytes
  corner2: [x: number, y: number, z: number]; // offset 24, 12 bytes
  corner3: [x: number, y: number, z: number]; // offset 36, 12 bytes
  corner4: [x: number, y: number, z: number]; // offset 48, 12 bytes
  corner5: [x: number, y: number, z: number]; // offset 60, 12 bytes
  corner6: [x: number, y: number, z: number]; // offset 72, 12 bytes
  corner7: [x: number, y: number, z: number]; // offset 84, 12 bytes
  /** per-instance RGBA 颜色，float4 (16 bytes) */
  color: [r: number, g: number, b: number, a: number]; // offset 96, 16 bytes
}
// Total stride: 112 bytes

/**
 * 线段 corner pair LUT
 * 12 条线段 × 2 顶点 = 24 vertices per instance
 * SV_VertexID (0–23) 映射:
 *   segmentIndex = vertexID / 2
 *   cornerIndex  = (vertexID % 2 == 0) ? lut[segmentIndex].x : lut[segmentIndex].y
 */
export const CORNER_PAIR_LUT: readonly [number, number][] = [
  [0, 1], [1, 2], [2, 3], [3, 0], // front face 4 edges
  [4, 5], [5, 6], [6, 7], [7, 4], // back face 4 edges
  [0, 4], [1, 5], [2, 6], [3, 7], // vertical connecting edges
];

/** 每个 instance 的顶点数 */
export const VERTICES_PER_INSTANCE = 24;

/** GPU buffer stride (bytes) */
export const INSTANCE_STRIDE_BYTES = 112;

/** 最大 instance 数 (预分配) */
export const MAX_INSTANCES = 64;

/** Object ID → 颜色的色表 (16 色循环) */
export const OBJECT_COLOR_TABLE: readonly [number, number, number][] = [
  [1.0, 0.2, 0.2], // red
  [0.2, 1.0, 0.2], // green
  [0.2, 0.4, 1.0], // blue
  [1.0, 1.0, 0.1], // yellow
  [1.0, 0.4, 0.1], // orange
  [0.1, 1.0, 1.0], // cyan
  [1.0, 0.1, 1.0], // magenta
  [0.5, 1.0, 0.2], // lime
  [0.2, 0.6, 1.0], // sky
  [1.0, 0.3, 0.6], // pink
  [0.6, 0.8, 1.0], // light blue
  [0.9, 0.9, 0.2], // pale yellow
  [0.3, 1.0, 0.5], // mint
  [1.0, 0.6, 0.1], // amber
  [0.5, 0.4, 1.0], // purple
  [0.1, 0.9, 0.3], // emerald
];

/** 渲染队列 entry (CPU 侧内部使用) */
export interface BboxRenderQueueEntry {
  objectId: number;
  /** 8 个 3D 世界角点 (front 4 + back 4) */
  corners: [
    [number, number, number],
    [number, number, number],
    [number, number, number],
    [number, number, number],
    [number, number, number],
    [number, number, number],
    [number, number, number],
    [number, number, number],
  ];
  /** RGBA color */
  color: [number, number, number, number];
}
