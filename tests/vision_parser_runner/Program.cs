using SmartRoom.Networking;

try
{
    ParseFrameResult_DecodesFrameAndMask();
    DecodeMask_ThrowsWhenCountsDoNotMatchSize();
    Console.WriteLine("VisionParserRunner: all tests passed.");
}
catch (Exception ex)
{
    Console.Error.WriteLine($"VisionParserRunner failed: {ex.Message}");
    Console.Error.WriteLine(ex);
    Environment.Exit(1);
}

static void ParseFrameResult_DecodesFrameAndMask()
{
    const string json = """
    {
      "frame_id": 7,
      "timestamp_ms": 1234,
      "frame_width": 4,
      "frame_height": 3,
      "prompt": "chair",
      "source": "fake-vision",
      "objects": [
        {
          "object_id": 1,
          "label": "chair",
          "score": 0.99,
          "box_xyxy": [0, 0, 1, 1],
          "area": 4,
          "mask_rle": {
            "size": [2, 2],
            "counts": [0, 4]
          }
        },
        {
          "object_id": 2,
          "label": "table",
          "score": 0.75,
          "box_xyxy": [1, 1, 3, 2],
          "area": 2,
          "mask_rle": {
            "size": [2, 3],
            "counts": [1, 2, 3]
          }
        }
      ]
    }
    """;

    VisionFrameResultData frame = VisionMessageParser.ParseFrameResult(json);
    AssertEqual(7, frame.FrameId, "frame id");
    AssertEqual(1234L, frame.TimestampMs, "timestamp");
    AssertEqual("chair", frame.Prompt, "prompt");
    AssertEqual(2, frame.Objects.Length, "object count");

    VisionObjectData first = frame.Objects[0];
    AssertEqual(2, first.DecodedMask.Width, "first mask width");
    AssertEqual(2, first.DecodedMask.Height, "first mask height");
    AssertTrue(first.DecodedMask.IsFilled(0, 0), "first mask pixel 0,0");
    AssertTrue(first.DecodedMask.IsFilled(1, 1), "first mask pixel 1,1");

    VisionObjectData second = frame.Objects[1];
    AssertEqual(3, second.DecodedMask.Width, "second mask width");
    AssertEqual(2, second.DecodedMask.Height, "second mask height");
    AssertTrue(!second.DecodedMask.IsFilled(0, 0), "second mask pixel 0,0");
    AssertTrue(second.DecodedMask.IsFilled(1, 0), "second mask pixel 1,0");
    AssertTrue(second.DecodedMask.IsFilled(2, 0), "second mask pixel 2,0");
    AssertTrue(!second.DecodedMask.IsFilled(0, 1), "second mask pixel 0,1");
}

static void DecodeMask_ThrowsWhenCountsDoNotMatchSize()
{
    try
    {
        VisionMessageParser.DecodeMask(new MaskRleData(2, 2, new[] { 1, 1 }));
    }
    catch (InvalidOperationException)
    {
        return;
    }

    throw new Exception("expected invalid mask counts to throw");
}

static void AssertEqual<T>(T expected, T actual, string name)
{
    if (!EqualityComparer<T>.Default.Equals(expected, actual))
    {
        throw new Exception($"{name}: expected {expected}, got {actual}");
    }
}

static void AssertTrue(bool condition, string name)
{
    if (!condition)
    {
        throw new Exception($"{name}: expected true");
    }
}
