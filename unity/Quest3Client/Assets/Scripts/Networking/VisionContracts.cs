using System;
using System.Globalization;

namespace SmartRoom.Networking
{
    public sealed class WorldPosition
    {
        public int ObjectId { get; }
        public string Label { get; }
        public float Score { get; }
        public float X { get; }
        public float Y { get; }
        public float Z { get; }
        public float DepthM { get; }

        public WorldPosition(
            int objectId,
            string label,
            float score,
            float x,
            float y,
            float z,
            float depthM)
        {
            ObjectId = objectId;
            Label = label ?? string.Empty;
            Score = score;
            X = x;
            Y = y;
            Z = z;
            DepthM = depthM;
        }
    }

    public sealed class VisionFrameResultData
    {
        public int FrameId { get; }
        public long TimestampMs { get; }
        public int FrameWidth { get; }
        public int FrameHeight { get; }
        public string Prompt { get; }
        public string Source { get; }
        public VisionObjectData[] Objects { get; }

        public VisionFrameResultData(
            int frameId,
            long timestampMs,
            int frameWidth,
            int frameHeight,
            string prompt,
            string source,
            VisionObjectData[] objects)
        {
            FrameId = frameId;
            TimestampMs = timestampMs;
            FrameWidth = frameWidth;
            FrameHeight = frameHeight;
            Prompt = prompt ?? string.Empty;
            Source = source ?? string.Empty;
            Objects = objects ?? Array.Empty<VisionObjectData>();
        }
    }

    public sealed class VisionObjectData
    {
        public int ObjectId { get; }
        public string Label { get; }
        public float Score { get; }
        public int[] BoxXyxy { get; }
        public int Area { get; }
        public MaskRleData MaskRle { get; }
        public DecodedBinaryMask DecodedMask { get; }

        public VisionObjectData(
            int objectId,
            string label,
            float score,
            int[] boxXyxy,
            int area,
            MaskRleData maskRle,
            DecodedBinaryMask decodedMask)
        {
            ObjectId = objectId;
            Label = label ?? string.Empty;
            Score = score;
            BoxXyxy = boxXyxy ?? Array.Empty<int>();
            Area = area;
            MaskRle = maskRle;
            DecodedMask = decodedMask;
        }
    }

    public sealed class MaskRleData
    {
        public int Height { get; }
        public int Width { get; }
        public int[] Counts { get; }

        public MaskRleData(int height, int width, int[] counts)
        {
            Height = height;
            Width = width;
            Counts = counts ?? Array.Empty<int>();
        }
    }

    public sealed class DecodedBinaryMask
    {
        public int Height { get; }
        public int Width { get; }
        public byte[] Values { get; }

        public DecodedBinaryMask(int height, int width, byte[] values)
        {
            Height = height;
            Width = width;
            Values = values ?? Array.Empty<byte>();
        }

        public bool IsFilled(int x, int y)
        {
            if (x < 0 || x >= Width || y < 0 || y >= Height)
            {
                throw new ArgumentOutOfRangeException();
            }

            return Values[(y * Width) + x] != 0;
        }
    }

    public static class VisionLabelFormatting
    {
        public static string FormatLabel(string label, float score)
        {
            string safeLabel = string.IsNullOrWhiteSpace(label) ? "unknown" : label.Trim();
            float safeScore = float.IsNaN(score) || float.IsInfinity(score) ? 0f : score;
            return string.Create(
                CultureInfo.InvariantCulture,
                $"{safeLabel} {safeScore:0.00}");
        }
    }
}
