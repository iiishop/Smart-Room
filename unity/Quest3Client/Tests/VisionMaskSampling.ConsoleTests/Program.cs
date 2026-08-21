using SmartRoom.Networking;

static void AssertEqual<T>(T expected, T actual, string message)
{
    if (!Equals(expected, actual))
    {
        throw new Exception($"{message}. Expected={expected}, Actual={actual}");
    }
}

static void AssertNear(float expected, float actual, float tolerance, string message)
{
    if (Math.Abs(expected - actual) > tolerance)
    {
        throw new Exception($"{message}. Expected={expected}, Actual={actual}, Tolerance={tolerance}");
    }
}

void SamplesEvenlyAcrossForegroundPixels()
{
    var frame = new VisionFramePayload
    {
        frame_width = 2,
        frame_height = 2,
    };
    var trackedMask = new VisionTrackedMaskPayload
    {
        object_id = 7,
        mask_rle = new VisionMaskRlePayload
        {
            size = new[] { 2, 2 },
            counts = new[] { 0, 4 },
        },
    };

    VisionMaskSamplePoint[] samples = VisionMaskSampling.SampleMaskPixels(frame, trackedMask, 3);
    AssertEqual(3, samples.Length, "expected three samples");
    AssertEqual(0, samples[0].PixelX, "first sample x");
    AssertEqual(0, samples[0].PixelY, "first sample y");
    AssertEqual(0, samples[1].PixelX, "second sample x");
    AssertEqual(1, samples[1].PixelY, "second sample y");
    AssertEqual(1, samples[2].PixelX, "third sample x");
    AssertEqual(1, samples[2].PixelY, "third sample y");
    AssertNear(0.25f, samples[0].ViewportU, 0.0001f, "first sample viewport u");
    AssertNear(0.75f, samples[0].ViewportV, 0.0001f, "first sample viewport v");
    AssertNear(0.25f, samples[1].ViewportV, 0.0001f, "second sample viewport v");
}

void SingleSampleReturnsForegroundCenter()
{
    var frame = new VisionFramePayload
    {
        frame_width = 3,
        frame_height = 3,
    };
    var trackedMask = new VisionTrackedMaskPayload
    {
        object_id = 9,
        mask_rle = new VisionMaskRlePayload
        {
            size = new[] { 3, 3 },
            counts = new[] { 4, 1, 4 },
        },
    };

    VisionMaskSamplePoint[] samples = VisionMaskSampling.SampleMaskPixels(frame, trackedMask, 1);
    AssertEqual(1, samples.Length, "expected one sample");
    AssertEqual(1, samples[0].PixelX, "single sample x");
    AssertEqual(1, samples[0].PixelY, "single sample y");
    AssertNear(0.5f, samples[0].ViewportU, 0.0001f, "single sample viewport u");
    AssertNear(0.5f, samples[0].ViewportV, 0.0001f, "single sample viewport v");
}

void InvalidMaskReturnsEmpty()
{
    var frame = new VisionFramePayload
    {
        frame_width = 4,
        frame_height = 4,
    };
    var trackedMask = new VisionTrackedMaskPayload
    {
        object_id = 11,
        mask_rle = new VisionMaskRlePayload
        {
            size = new[] { 4, 4 },
            counts = new[] { -1, 3 },
        },
    };

    VisionMaskSamplePoint[] samples = VisionMaskSampling.SampleMaskPixels(frame, trackedMask, 2);
    AssertEqual(0, samples.Length, "invalid mask should return empty");
}

SamplesEvenlyAcrossForegroundPixels();
SingleSampleReturnsForegroundCenter();
InvalidMaskReturnsEmpty();
Console.WriteLine("VisionMaskSampling console tests passed.");
