using SmartRoom.Networking;

static void AssertEqual<T>(T expected, T actual, string message)
{
    if (!Equals(expected, actual))
    {
        throw new Exception($"{message}. Expected={expected}, Actual={actual}");
    }
}

static void AssertTrue(bool condition, string message)
{
    if (!condition)
    {
        throw new Exception(message);
    }
}

void DequeuesLatestFrameOnly()
{
    var parser = new VisionMessageParser();

    parser.Enqueue("""{"frame_id":1,"timestamp_ms":100,"frame_width":4,"frame_height":3,"objects":[]}""");
    parser.Enqueue("""{"frame_id":2,"timestamp_ms":200,"frame_width":5,"frame_height":6,"objects":[{"object_id":9,"label":"chair","score":0.75,"box_xyxy":[0,1,2,3],"area":4,"mask_rle":{"size":[2,2],"counts":[0,4]}}]}""");

    bool dequeued = parser.TryDequeueLatest(out VisionFramePayload? frame, out string? errorMessage);

    AssertTrue(dequeued, "expected latest frame to dequeue");
    AssertEqual<string?>(null, errorMessage, "expected no parse error");
    AssertTrue(frame != null, "expected frame payload");
    AssertEqual(2, frame.frame_id, "latest frame id");
    AssertEqual(200L, frame.timestamp_ms, "latest timestamp");
    AssertEqual(5, frame.frame_width, "frame width");
    AssertEqual(6, frame.frame_height, "frame height");
    AssertEqual(1, frame.objects.Length, "object count");
    AssertEqual(9, frame.objects[0].object_id, "object id");
    AssertEqual("chair", frame.objects[0].label, "object label");
    AssertEqual(4, frame.objects[0].area, "object area");

    bool secondDequeue = parser.TryDequeueLatest(out VisionFramePayload? emptyFrame, out string? secondError);
    AssertTrue(!secondDequeue, "queue should be empty after latest dequeue");
    AssertEqual<VisionFramePayload?>(null, emptyFrame, "empty dequeue frame");
    AssertEqual<string?>(null, secondError, "empty dequeue error");
}

void IgnoresWhitespaceAndCanClearQueue()
{
    var parser = new VisionMessageParser();

    parser.Enqueue("   ");
    bool dequeuedWhitespace = parser.TryDequeueLatest(out VisionFramePayload? emptyFrame, out string? emptyError);
    AssertTrue(!dequeuedWhitespace, "whitespace input should be ignored");
    AssertEqual<VisionFramePayload?>(null, emptyFrame, "frame should stay null");
    AssertEqual<string?>(null, emptyError, "error should stay null");

    parser.Enqueue("""{"frame_id":3,"timestamp_ms":300,"frame_width":2,"frame_height":2,"objects":[]}""");
    parser.Clear();

    bool dequeuedAfterClear = parser.TryDequeueLatest(out VisionFramePayload? clearedFrame, out string? clearedError);
    AssertTrue(!dequeuedAfterClear, "clear should drain pending messages");
    AssertEqual<VisionFramePayload?>(null, clearedFrame, "cleared frame");
    AssertEqual<string?>(null, clearedError, "cleared error");
}

void ReturnsParseErrorsFromJsonUtility()
{
    var parser = new VisionMessageParser();

    parser.Enqueue("{\"frame_id\":");
    bool dequeued = parser.TryDequeueLatest(out VisionFramePayload? frame, out string? errorMessage);

    AssertTrue(!dequeued, "invalid json should not parse");
    AssertEqual<VisionFramePayload?>(null, frame, "invalid json frame");
    AssertTrue(!string.IsNullOrWhiteSpace(errorMessage), "invalid json should surface parse error");
}

DequeuesLatestFrameOnly();
IgnoresWhitespaceAndCanClearQueue();
ReturnsParseErrorsFromJsonUtility();
Console.WriteLine("VisionMessageParser console tests passed.");
