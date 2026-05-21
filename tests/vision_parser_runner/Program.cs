using System.Net.WebSockets;
using SmartRoom.Networking;

try
{
    ParseFrameResult_DecodesFrameAndMask();
    DecodeMask_ThrowsWhenCountsDoNotMatchSize();
    FormatLabel_UsesInvariantScoreAndFallbackLabel();
    SocketOwnership_DoesNotClearNewSocketWhenOldLoopDisposes();
    WorldPositionFactory_CreatesPayloadWithCoordinates();
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

static void SocketOwnership_DoesNotClearNewSocketWhenOldLoopDisposes()
{
    using var oldSocket = new ClientWebSocket();
    using var newSocket = new ClientWebSocket();

    AssertTrue(
        !VisionSocketOwnership.ShouldClearCurrentSocket(newSocket, oldSocket),
        "old loop should not clear replacement socket");
    AssertTrue(
        VisionSocketOwnership.ShouldClearCurrentSocket(newSocket, newSocket),
        "current loop should clear owned socket");
}

static void FormatLabel_UsesInvariantScoreAndFallbackLabel()
{
    AssertEqual("chair 0.99", VisionLabelFormatting.FormatLabel("chair", 0.99f), "formatted score");
    AssertEqual("unknown 0.00", VisionLabelFormatting.FormatLabel("  ", float.NaN), "fallback label");
}

static void WorldPositionFactory_CreatesPayloadWithCoordinates()
{
    WorldPosition position = VisionWorldPositionFactory.Create(7, "chair", 0.8f, 1.5f, 2.5f, 3.5f, 4.5f);
    AssertEqual(7, position.ObjectId, "world position object id");
    AssertEqual("chair", position.Label, "world position label");
    AssertEqual(0.8f, position.Score, "world position score");
    AssertEqual(1.5f, position.X, "world position x");
    AssertEqual(2.5f, position.Y, "world position y");
    AssertEqual(3.5f, position.Z, "world position z");
    AssertEqual(4.5f, position.DepthM, "world position depth");
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
