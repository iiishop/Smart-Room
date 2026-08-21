using System;
using System.Collections.Generic;

#nullable enable

namespace SmartRoom.Networking
{
    [Serializable]
    public sealed class VisionMaskRlePayload
    {
        public int[]? size;
        public int[]? counts;
    }

    [Serializable]
    public sealed class VisionTrackedMaskPayload
    {
        public int object_id;
        public string? label;
        public float score;
        public int[]? box_xyxy;
        public int area;
        public VisionMaskRlePayload? mask_rle;
    }

    [Serializable]
    public sealed class VisionFramePayload
    {
        public int frame_id;
        public long timestamp_ms;
        public int frame_width;
        public int frame_height;
        public string? prompt;
        public string? source;
        public VisionTrackedMaskPayload[]? objects;
    }

    public readonly struct VisionMaskSamplePoint
    {
        public VisionMaskSamplePoint(int pixelX, int pixelY, float viewportU, float viewportV)
        {
            PixelX = pixelX;
            PixelY = pixelY;
            ViewportU = viewportU;
            ViewportV = viewportV;
        }

        public int PixelX { get; }
        public int PixelY { get; }
        public float ViewportU { get; }
        public float ViewportV { get; }
    }

    public static class VisionMaskSampling
    {
        public static VisionMaskSamplePoint[] SampleMaskPixels(
            VisionFramePayload frame,
            VisionTrackedMaskPayload trackedMask,
            int requestedSampleCount)
        {
            if (frame == null || trackedMask?.mask_rle == null || requestedSampleCount <= 0)
            {
                return Array.Empty<VisionMaskSamplePoint>();
            }

            if (!TryGetMaskSize(trackedMask.mask_rle, out int maskHeight, out int maskWidth))
            {
                return Array.Empty<VisionMaskSamplePoint>();
            }

            int[] counts = trackedMask.mask_rle.counts ?? Array.Empty<int>();
            int totalForeground = CountForegroundPixels(counts);
            if (totalForeground <= 0)
            {
                return Array.Empty<VisionMaskSamplePoint>();
            }

            int sampleCount = Math.Min(requestedSampleCount, totalForeground);
            int frameWidth = frame.frame_width > 0 ? frame.frame_width : maskWidth;
            int frameHeight = frame.frame_height > 0 ? frame.frame_height : maskHeight;
            var targetForegroundOrdinals = BuildTargetForegroundOrdinals(totalForeground, sampleCount);
            var samples = new List<VisionMaskSamplePoint>(sampleCount);

            int targetCursor = 0;
            int seenForeground = 0;
            bool isForegroundRun = false;
            int flatIndex = 0;
            for (int runIndex = 0; runIndex < counts.Length && targetCursor < targetForegroundOrdinals.Length; runIndex++)
            {
                int runLength = counts[runIndex];
                if (runLength < 0)
                {
                    return Array.Empty<VisionMaskSamplePoint>();
                }

                if (!isForegroundRun)
                {
                    flatIndex += runLength;
                    isForegroundRun = true;
                    continue;
                }

                int runStartForeground = seenForeground;
                int runEndForeground = seenForeground + runLength;
                while (targetCursor < targetForegroundOrdinals.Length && targetForegroundOrdinals[targetCursor] < runEndForeground)
                {
                    int ordinalInRun = targetForegroundOrdinals[targetCursor] - runStartForeground;
                    int pixelIndex = flatIndex + ordinalInRun;
                    int pixelY = pixelIndex / maskWidth;
                    int pixelX = pixelIndex % maskWidth;
                    float viewportU = ((float)pixelX + 0.5f) / frameWidth;
                    float viewportV = 1f - (((float)pixelY + 0.5f) / frameHeight);
                    samples.Add(new VisionMaskSamplePoint(pixelX, pixelY, viewportU, viewportV));
                    targetCursor++;
                }

                seenForeground = runEndForeground;
                flatIndex += runLength;
                isForegroundRun = false;
            }

            return samples.ToArray();
        }

        private static bool TryGetMaskSize(VisionMaskRlePayload maskRle, out int height, out int width)
        {
            height = 0;
            width = 0;

            if (maskRle?.size == null || maskRle.size.Length != 2)
            {
                return false;
            }

            height = maskRle.size[0];
            width = maskRle.size[1];
            return height > 0 && width > 0;
        }

        private static int CountForegroundPixels(int[] counts)
        {
            if (counts == null || counts.Length == 0)
            {
                return 0;
            }

            int total = 0;
            for (int i = 1; i < counts.Length; i += 2)
            {
                if (counts[i] < 0)
                {
                    return 0;
                }

                total += counts[i];
            }

            return total;
        }

        private static int[] BuildTargetForegroundOrdinals(int totalForeground, int sampleCount)
        {
            var targets = new int[sampleCount];
            if (sampleCount == 1)
            {
                targets[0] = totalForeground / 2;
                return targets;
            }

            for (int i = 0; i < sampleCount; i++)
            {
                float t = (float)i / (sampleCount - 1);
                int ordinal = (int)Math.Round(t * (totalForeground - 1));
                if (ordinal < 0)
                {
                    ordinal = 0;
                }
                else if (ordinal >= totalForeground)
                {
                    ordinal = totalForeground - 1;
                }

                targets[i] = ordinal;
            }

            return targets;
        }
    }
}
