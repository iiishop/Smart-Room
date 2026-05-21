using System;
using System.Runtime.InteropServices;
using UnityEngine;

namespace SmartRoom.Rendering
{
    [StructLayout(LayoutKind.Sequential)]
    public struct VisionRenderData
    {
        public int objectId;
        public int labelIndex;
        public float confidence;
        public Vector4 boxXyxy;
        public Vector4 colorRgba;
        public Vector2 labelAnchor;
        public Vector2 labelSize;
        public int flags;

        public VisionRenderData(
            int objectId,
            int labelIndex,
            float confidence,
            Vector4 boxXyxy,
            Color32 color,
            Vector2 labelAnchor,
            Vector2 labelSize,
            int flags = 0)
        {
            this.objectId = objectId;
            this.labelIndex = labelIndex;
            this.confidence = confidence;
            this.boxXyxy = boxXyxy;
            this.colorRgba = new Vector4(color.r / 255f, color.g / 255f, color.b / 255f, color.a / 255f);
            this.labelAnchor = labelAnchor;
            this.labelSize = labelSize;
            this.flags = flags;
        }
    }

    public sealed class VisionRenderObjectData
    {
        public int ObjectId { get; }
        public string Label { get; }
        public float Confidence { get; }
        public RectInt BoundingBox { get; }
        public Color32 Color { get; }
        public bool IsVisible { get; }

        public VisionRenderObjectData(
            int objectId,
            string label,
            float confidence,
            RectInt boundingBox,
            Color32 color,
            bool isVisible)
        {
            ObjectId = objectId;
            Label = label ?? string.Empty;
            Confidence = confidence;
            BoundingBox = boundingBox;
            Color = color;
            IsVisible = isVisible;
        }

        public VisionRenderData ToGpuData(int labelIndex, Vector2 labelAnchor, Vector2 labelSize, int flags = 0)
        {
            Vector4 boxXyxy = new Vector4(
                BoundingBox.xMin,
                BoundingBox.yMin,
                BoundingBox.xMax,
                BoundingBox.yMax);

            return new VisionRenderData(
                ObjectId,
                labelIndex,
                Confidence,
                boxXyxy,
                Color,
                labelAnchor,
                labelSize,
                flags);
        }
    }
}
