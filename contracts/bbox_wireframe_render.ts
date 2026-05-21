// Contract for DEA-231: ComputeBuffer + DrawProcedural BBox wireframe rendering
// Shared between VisionReceiverModule (data producer), BboxWireframeManager (CPU consumer),
// and BboxWireframe.shader (GPU consumer).

// --- GPU ComputeBuffer layout (C# System.Runtime.InteropServices struct) ---
// LayoutKind.Sequential, align to 4/16 bytes as GPU expects.
// Max instances: 64 (allocated once, resized if needed).
// Vertex shader reads StructuredBuffer<BboxWireframeInstance> _BboxInstances.

export interface BboxWireframeInstance {
  /** 8 corners in world space, vec3 → 12 float pairs (3*8=24 floats, 96 bytes) */
  corners: Float32Array; // length 24, interleaved [c0.x,c0.y,c0.z, c1.x,... c7.x,c7.y,c7.z]
  /** per-instance color, rgba half-precision (4 floats, 16 bytes) */
  color: [number, number, number, number]; // r,g,b,a
}
// Total stride per instance: 112 bytes (96 + 16)

// --- Shader vertex index LUT (SV_VertexID 0..23 → corner pair) ---
// 12 line segments forming a 3D cuboid wireframe.
// Segment → corner pair mapping:
//   Bottom face (y=-1): [0→1, 1→2, 2→3, 3→0]
//   Top face    (y=+1): [4→5, 5→6, 6→7, 7→4]
//   Vertical pillars:    [0→4, 1→5, 2→6, 3→7]
// Corner numbering:
//   4-----5
//   |\    |\
//   | 7-----6
//   0--|--1 |
//    \ |   \|
//     3-----2
// (Bottom: 0,1,2,3 clockwise; Top: 4,5,6,7 above respective bottom)

export const BBOX_EDGE_CORNER_PAIRS: [number, number][] = [
  [0, 1], [1, 2], [2, 3], [3, 0], // bottom face
  [4, 5], [5, 6], [6, 7], [7, 4], // top face
  [0, 4], [1, 5], [2, 6], [3, 7], // vertical pillars
];

// --- Per-object metadata (C# side, before GPU upload) ---
export interface BboxRenderQueueEntry {
  objectId: number;
  /** 8 world-space corners [front-bottom-left, front-bottom-right, ...] */
  corners: [number, number, number][]; // length 8
  color: [number, number, number, number]; // r,g,b,a from object_id color table
  label: string;
  score: number;
}

// --- Object ID → color lookup table (C# static) ---
// Assigns distinct hues per object_id (cycling palette for >8 objects).
// exported for label/billboard components to use the same colors.
export const OBJECT_ID_COLOR_TABLE: Record<number, string> = {
  0: "#FF4444", 1: "#44FF44", 2: "#4488FF",
  3: "#FFD700", 4: "#FF44FF", 5: "#44FFFF",
  6: "#FF8800", 7: "#00FF88", 8: "#8844FF",
  9: "#FF4488", 10: "#88FF44", 11: "#448844",
  12: "#FFAA00", 13: "#AAFF00", 14: "#00AAFF",
  15: "#AA00FF",
};

// --- Shader properties (Material inspector) ---
export interface BboxWireframeMaterialProperties {
  _LineAlpha: number;     // default 0.6, range [0,1]
  _LineWidthScale: number; // screen-space scale, default 1.0
}
