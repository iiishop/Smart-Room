#if VISION_TEST_ONLY_PARSER
using System;
using System.Collections.Generic;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

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

            JObject root = JObject.Parse(json);

            int frameId = ReadInt32(root, "frame_id");
            long timestampMs = ReadInt64(root, "timestamp_ms");
            int frameWidth = ReadInt32(root, "frame_width");
            int frameHeight = ReadInt32(root, "frame_height");
            string prompt = ReadString(root, "prompt");
            string source = ReadString(root, "source");

            var objects = new List<VisionObjectData>();
            JToken objectToken = root["objects"];
            if (objectToken is JArray objectArray)
            {
                foreach (JToken item in objectArray)
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

        private static MaskRleData ReadMaskRle(JToken item)
        {
            JToken maskRleToken = item["mask_rle"];
            if (!(maskRleToken is JObject))
            {
                throw new JsonException("mask_rle object is required");
            }

            int[] size = ReadIntArray(maskRleToken, "size", expectedLength: 2);
            int[] counts = ReadCounts(maskRleToken);
            return new MaskRleData(size[0], size[1], counts);
        }

        private static int[] ReadCounts(JToken maskRleToken)
        {
            JToken countsToken = maskRleToken["counts"];
            if (countsToken == null)
            {
                throw new JsonException("mask_rle.counts is required");
            }

            if (!(countsToken is JArray countsArray))
            {
                throw new NotSupportedException("Only array-based COCO RLE counts are supported.");
            }

            var counts = new List<int>();
            foreach (JToken value in countsArray)
            {
                counts.Add(value.Value<int>());
            }

            return counts.ToArray();
        }

        private static int[] ReadIntArray(JToken parent, string propertyName, int expectedLength)
        {
            JToken element = parent[propertyName];
            if (!(element is JArray array))
            {
                throw new JsonException($"{propertyName} array is required");
            }

            var values = new List<int>();
            foreach (JToken value in array)
            {
                values.Add(value.Value<int>());
            }

            if (values.Count != expectedLength)
            {
                throw new JsonException($"{propertyName} must contain {expectedLength} integers");
            }

            return values.ToArray();
        }

        private static int ReadInt32(JToken parent, string propertyName)
        {
            JToken element = parent[propertyName];
            if (element == null)
            {
                throw new JsonException($"{propertyName} is required");
            }

            return element.Value<int>();
        }

        private static long ReadInt64(JToken parent, string propertyName)
        {
            JToken element = parent[propertyName];
            if (element == null)
            {
                throw new JsonException($"{propertyName} is required");
            }

            return element.Value<long>();
        }

        private static float ReadSingle(JToken parent, string propertyName)
        {
            JToken element = parent[propertyName];
            if (element == null)
            {
                throw new JsonException($"{propertyName} is required");
            }

            return element.Value<float>();
        }

        private static string ReadString(JToken parent, string propertyName)
        {
            JToken element = parent[propertyName];
            if (element == null)
            {
                throw new JsonException($"{propertyName} is required");
            }

            return element.Value<string>() ?? string.Empty;
        }
    }
}
#endif
