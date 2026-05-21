using System;
using System.Collections.Generic;
using System.Text.Json;

namespace SmartRoom.Networking
{
    public static class VisionMessageParser
    {
        public static VisionFrameResultData ParseFrameResult(string json)
        {
            if (string.IsNullOrWhiteSpace(json))
            {
                throw new ArgumentException("vision payload must not be empty", nameof(json));
            }

            using JsonDocument document = JsonDocument.Parse(json);
            JsonElement root = document.RootElement;

            int frameId = ReadInt32(root, "frame_id");
            long timestampMs = ReadInt64(root, "timestamp_ms");
            int frameWidth = ReadInt32(root, "frame_width");
            int frameHeight = ReadInt32(root, "frame_height");
            string prompt = ReadString(root, "prompt");
            string source = ReadString(root, "source");

            var objects = new List<VisionObjectData>();
            if (root.TryGetProperty("objects", out JsonElement objectArray) &&
                objectArray.ValueKind == JsonValueKind.Array)
            {
                foreach (JsonElement item in objectArray.EnumerateArray())
                {
                    int objectId = ReadInt32(item, "object_id");
                    string label = ReadString(item, "label");
                    float score = ReadSingle(item, "score");
                    int[] boxXyxy = ReadIntArray(item, "box_xyxy", expectedLength: 4);
                    int area = ReadInt32(item, "area");
                    MaskRleData maskRle = ReadMaskRle(item);
                    DecodedBinaryMask decodedMask = DecodeMask(maskRle);

                    objects.Add(
                        new VisionObjectData(
                            objectId,
                            label,
                            score,
                            boxXyxy,
                            area,
                            maskRle,
                            decodedMask));
                }
            }

            return new VisionFrameResultData(
                frameId,
                timestampMs,
                frameWidth,
                frameHeight,
                prompt,
                source,
                objects.ToArray());
        }

        public static DecodedBinaryMask DecodeMask(MaskRleData maskRle)
        {
            if (maskRle == null)
            {
                throw new ArgumentNullException(nameof(maskRle));
            }

            if (maskRle.Height <= 0 || maskRle.Width <= 0)
            {
                throw new InvalidOperationException("mask size must be positive");
            }

            int total = checked(maskRle.Height * maskRle.Width);
            var values = new byte[total];
            int writeIndex = 0;
            byte current = 0;

            foreach (int runLength in maskRle.Counts)
            {
                if (runLength < 0)
                {
                    throw new InvalidOperationException("mask run length must not be negative");
                }

                if (writeIndex + runLength > total)
                {
                    throw new InvalidOperationException("mask run lengths exceed declared size");
                }

                if (current != 0)
                {
                    Array.Fill(values, current, writeIndex, runLength);
                }

                writeIndex += runLength;
                current = current == 0 ? (byte)1 : (byte)0;
            }

            if (writeIndex != total)
            {
                throw new InvalidOperationException("mask run lengths do not cover the declared size");
            }

            return new DecodedBinaryMask(maskRle.Height, maskRle.Width, values);
        }

        private static MaskRleData ReadMaskRle(JsonElement item)
        {
            if (!item.TryGetProperty("mask_rle", out JsonElement maskRleElement) ||
                maskRleElement.ValueKind != JsonValueKind.Object)
            {
                throw new JsonException("mask_rle object is required");
            }

            int[] size = ReadIntArray(maskRleElement, "size", expectedLength: 2);
            int[] counts = ReadCounts(maskRleElement);
            return new MaskRleData(size[0], size[1], counts);
        }

        private static int[] ReadCounts(JsonElement maskRleElement)
        {
            if (!maskRleElement.TryGetProperty("counts", out JsonElement countsElement))
            {
                throw new JsonException("mask_rle.counts is required");
            }

            if (countsElement.ValueKind != JsonValueKind.Array)
            {
                throw new NotSupportedException("Only array-based COCO RLE counts are supported.");
            }

            var counts = new List<int>();
            foreach (JsonElement value in countsElement.EnumerateArray())
            {
                counts.Add(value.GetInt32());
            }

            return counts.ToArray();
        }

        private static int[] ReadIntArray(JsonElement parent, string propertyName, int expectedLength)
        {
            if (!parent.TryGetProperty(propertyName, out JsonElement element) ||
                element.ValueKind != JsonValueKind.Array)
            {
                throw new JsonException($"{propertyName} array is required");
            }

            var values = new List<int>();
            foreach (JsonElement value in element.EnumerateArray())
            {
                values.Add(value.GetInt32());
            }

            if (values.Count != expectedLength)
            {
                throw new JsonException($"{propertyName} must contain {expectedLength} integers");
            }

            return values.ToArray();
        }

        private static int ReadInt32(JsonElement parent, string propertyName)
        {
            if (!parent.TryGetProperty(propertyName, out JsonElement element))
            {
                throw new JsonException($"{propertyName} is required");
            }

            return element.GetInt32();
        }

        private static long ReadInt64(JsonElement parent, string propertyName)
        {
            if (!parent.TryGetProperty(propertyName, out JsonElement element))
            {
                throw new JsonException($"{propertyName} is required");
            }

            return element.GetInt64();
        }

        private static float ReadSingle(JsonElement parent, string propertyName)
        {
            if (!parent.TryGetProperty(propertyName, out JsonElement element))
            {
                throw new JsonException($"{propertyName} is required");
            }

            return element.GetSingle();
        }

        private static string ReadString(JsonElement parent, string propertyName)
        {
            if (!parent.TryGetProperty(propertyName, out JsonElement element))
            {
                throw new JsonException($"{propertyName} is required");
            }

            return element.GetString() ?? string.Empty;
        }
    }
}
