// ============================================================
// BboxWireframe Render Contract
// Shared type definitions for GPU buffer layout, vertex LUT,
// and render data flowing between C# → HLSL.
// ============================================================

// ---- GPU Buffer Layout (C# side) ---------------------------

/**
 * Per-instance data uploaded to StructuredBuffer<BboxWireframeInstance>.
 * Stride: 112 bytes (8 × float3 corners + half4 color + 8 bytes padding).
 *
 * HLSL equivalent in BboxWireframe.shader:
 *   struct BboxWireframeInstance {
 *       float3 corner0;
 *       float3 corner1;
 *       float3 corner2;
 *       float3 corner3;
 *       float3 corner4;
 *       float3 corner5;
 *       float3 corner6;
 *       float3 corner7;
 *       half4  color;
 *   };
 *
 * C# struct (in BboxWireframeManager.cs):
 *   [StructLayout(LayoutKind.Sequential)]
 *   struct BboxWireframeInstance {
 *       Vector3 corner0; // 12
 *       Vector3 corner1; // 12
 *       Vector3 corner2; // 12
 *       Vector3 corner3; // 12
 *       Vector3 corner4; // 12
 *       Vector3 corner5; // 12
 *       Vector3 corner6; // 12
 *       Vector3 corner7; // 12
 *       // half4 color: 4 × System.Half = 8 bytes, stored as 4 × ushort for padding
 *       ushort cr, cg, cb, ca; // 8  (encoded half-precision)
 *       // implicit 8-byte padding → total stride = 112
 *   }
 *
 * Byte layout (offsets):
 *   0-11:   corner0  (float3)
 *   12-23:  corner1  (float3)
 *   24-35:  corner2  (float3)
 *   36-47:  corner3  (float3)
 *   48-59:  corner4  (float3)
 *   60-71:  corner5  (float3)
 *   72-83:  corner6  (float3)
 *   84-95:  corner7  (float3)
 *   96-103: color    (half4)
 *   104-111: padding (8 bytes, implicit)
 *   Total stride: 112
 */
export const GPU_STRIDE_BYTES = 112;
export const GPU_CORNER_COUNT = 8;       // 8 corners per bbox
export const GPU_CORNER_OFFSET_BYTES = 96; // offset of color field
export const GPU_LINE_VERTEX_COUNT = 24;  // 12 edges × 2 vertices

// ---- Vertex LUT: corner index for each of 24 vertices -------

/**
 * Edge pair LUT: maps SV_VertexID (0–23) → corner index (0–7).
 * 12 line segments = 24 vertices.
 *
 * Box corner convention (right-handed, Y-up):
 *   0: (minX, minY, minZ)  — bottom-near-left
 *   1: (maxX, minY, minZ)  — bottom-near-right
 *   2: (minX, minY, maxZ)  — bottom-far-left
 *   3: (maxX, minY, maxZ)  — bottom-far-right
 *   4: (minX, maxY, minZ)  — top-near-left
 *   5: (maxX, maxY, minZ)  — top-near-right
 *   6: (minX, maxY, maxZ)  — top-far-left
 *   7: (maxX, maxY, maxZ)  — top-far-right
 *
 * Edges (12): 4 bottom + 4 top + 4 vertical
 */
export const EDGE_PAIR_LUT: readonly number[] = [
  // Bottom face (4 edges)
  0, 1,   // bottom front edge  (near)
  0, 2,   // bottom left edge    (far left / near-far-left)
  1, 3,   // bottom right edge   (far right / near-far-right)
  2, 3,   // bottom back edge    (far)
  // Top face (4 edges)
  4, 5,   // top front edge
  4, 6,   // top left edge
  5, 7,   // top right edge
  6, 7,   // top back edge
  // Vertical edges (4)
  0, 4,   // front-left vertical
  1, 5,   // front-right vertical
  2, 6,   // back-left vertical
  3, 7,   // back-right vertical
];

/**
 * Describes the input corner data required to build one bbox instance.
 * Comes from VisionReceiverModule: 4 pixel xyxy corners → 3D world corners.
 */
export interface BboxCornerInput {
  objectId: number;
  /** 8 world-space corners (order per convention above) */
  corners: [
    [number, number, number], // corner0
    [number, number, number], // corner1
    [number, number, number], // corner2
    [number, number, number], // corner3
    [number, number, number], // corner4
    [number, number, number], // corner5
    [number, number, number], // corner6
    [number, number, number], // corner7
  ];
  /** Per-instance line color (RGB, alpha from shader uniform) */
  color: [number, number, number]; // r, g, b (0–1 range)
}

// ---- Object Color Table ------------------------------------

/** Default color palette for object IDs (mod 16 cycling) */
export const OBJECT_COLOR_TABLE: readonly (readonly [number, number, number])[] = [
  [1.0, 0.0, 0.0],   // red
  [0.0, 1.0, 0.0],   // green
  [0.0, 0.0, 1.0],   // blue
  [1.0, 1.0, 0.0],   // yellow
  [1.0, 0.0, 1.0],   // magenta
  [0.0, 1.0, 1.0],   // cyan
  [1.0, 0.5, 0.0],   // orange
  [0.5, 0.0, 1.0],   // purple
  [0.0, 1.0, 0.5],   // spring green
  [1.0, 0.75, 0.8],  // pink
  [0.5, 0.5, 1.0],   // lavender
  [0.0, 0.75, 1.0],  // sky blue
  [1.0, 0.4, 0.4],   // salmon
  [0.4, 1.0, 0.4],   // lime
  [0.4, 0.4, 1.0],   // cornflower
  [0.8, 0.8, 0.2],   // gold
];

// ---- Render Queue Entry ------------------------------------

export interface BboxRenderEntry {
  objectId: number;
  corners: BboxCornerInput["corners"];
  colorIndex: number; // index into OBJECT_COLOR_TABLE
}

// ---- Shader Properties -------------------------------------

export const SHADER_PATH = "Assets/Shaders/BboxWireframe.shader";
export const MATERIAL_PATH = "Assets/Resources/BboxWireframe.mat";
export const SHADER_GUID = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6";
export const SHADER_NAME = "SmartRoom/BboxWireframe";
export const RENDER_QUEUE = 3000;       // Transparent
export const MAX_INSTANCES = 64;
export const DEFAULT_LINE_ALPHA = 0.8;
