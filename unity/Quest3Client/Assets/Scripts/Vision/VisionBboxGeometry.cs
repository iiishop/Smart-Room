using System;
using System.Runtime.InteropServices;
using UnityEngine;

namespace SmartRoom.Vision
{
    [StructLayout(LayoutKind.Sequential)]
    public struct VisionLineVertex
    {
        public Vector3 Position;
        public uint Color;

        public VisionLineVertex(Vector3 position, uint color)
        {
            Position = position;
            Color = color;
        }

        public static uint PackColor(Color32 color)
        {
            return (uint)(color.r | (color.g << 8) | (color.b << 16) | (color.a << 24));
        }
    }

    public static class VisionBboxGeometry
    {
        public const int CornerCount = 8;
        public const int EdgeCount = 12;
        public const int VerticesPerEdge = 2;
        public const int VerticesPerBox = EdgeCount * VerticesPerEdge;

        private static readonly int[,] EdgeCornerPairs = new int[,]
        {
            { 0, 1 },
            { 1, 2 },
            { 2, 3 },
            { 3, 0 },
            { 4, 5 },
            { 5, 6 },
            { 6, 7 },
            { 7, 4 },
            { 0, 4 },
            { 1, 5 },
            { 2, 6 },
            { 3, 7 }
        };

        public static VisionLineVertex[] BuildBboxLineVertices(Vector3[] corners, Color32 color)
        {
            if (corners == null)
            {
                throw new ArgumentNullException(nameof(corners));
            }

            var vertices = new VisionLineVertex[VerticesPerBox];
            WriteBboxLineVertices(corners, color, vertices, 0);
            return vertices;
        }

        public static int WriteBboxLineVertices(
            Vector3[] corners,
            Color32 color,
            VisionLineVertex[] destination,
            int destinationIndex)
        {
            if (corners == null)
            {
                throw new ArgumentNullException(nameof(corners));
            }

            if (destination == null)
            {
                throw new ArgumentNullException(nameof(destination));
            }

            if (corners.Length < CornerCount)
            {
                throw new ArgumentException("Expected 8 bbox corners.", nameof(corners));
            }

            if (destinationIndex < 0 || destinationIndex + VerticesPerBox > destination.Length)
            {
                throw new ArgumentOutOfRangeException(nameof(destinationIndex));
            }

            uint packedColor = VisionLineVertex.PackColor(color);
            int writeIndex = destinationIndex;
            for (int edgeIndex = 0; edgeIndex < EdgeCount; edgeIndex++)
            {
                destination[writeIndex++] = new VisionLineVertex(corners[EdgeCornerPairs[edgeIndex, 0]], packedColor);
                destination[writeIndex++] = new VisionLineVertex(corners[EdgeCornerPairs[edgeIndex, 1]], packedColor);
            }

            return VerticesPerBox;
        }

        public static int WriteClosedContourLineVertices(
            Vector3[] contour,
            Color32 color,
            VisionLineVertex[] destination,
            int destinationIndex)
        {
            if (contour == null)
            {
                throw new ArgumentNullException(nameof(contour));
            }

            if (destination == null)
            {
                throw new ArgumentNullException(nameof(destination));
            }

            if (contour.Length < 2)
            {
                return 0;
            }

            int requiredVertexCount = contour.Length * VerticesPerEdge;
            if (destinationIndex < 0 || destinationIndex + requiredVertexCount > destination.Length)
            {
                throw new ArgumentOutOfRangeException(nameof(destinationIndex));
            }

            uint packedColor = VisionLineVertex.PackColor(color);
            int writeIndex = destinationIndex;
            for (int pointIndex = 0; pointIndex < contour.Length; pointIndex++)
            {
                Vector3 start = contour[pointIndex];
                Vector3 end = contour[(pointIndex + 1) % contour.Length];
                destination[writeIndex++] = new VisionLineVertex(start, packedColor);
                destination[writeIndex++] = new VisionLineVertex(end, packedColor);
            }

            return requiredVertexCount;
        }
    }
}
