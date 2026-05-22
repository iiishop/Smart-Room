using System.Net.WebSockets;
using System.Text.RegularExpressions;
using SmartRoom.Networking;
using SmartRoom.Vision;
using UnityEngine;

try
{
    ParseFrameResult_DecodesFrameAndMask();
    DecodeMask_ThrowsWhenCountsDoNotMatchSize();
    FormatLabel_UsesInvariantScoreAndFallbackLabel();
    SocketOwnership_DoesNotClearNewSocketWhenOldLoopDisposes();
    WorldPositionFactory_CreatesPayloadWithCoordinates();
    VisionOverlayShaders_ExposeExpectedShaderContracts();
    VisionBboxGeometry_ExpandsTwelveEdgesIntoTwentyFourVertices();
    VisionBboxGeometry_ClosesContoursIntoLinePairs();
    VisionObjectColorTable_MatchesTypescriptContract();
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

static void VisionOverlayShaders_ExposeExpectedShaderContracts()
{
    string repoRoot = Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "..", ".."));
    string shadersDir = Path.Combine(repoRoot, "unity", "Quest3Client", "Assets", "Shaders");

    AssertShader(
        Path.Combine(shadersDir, "VisionBboxLines.shader"),
        new[]
        {
            "\"RenderPipeline\" = \"UniversalPipeline\"",
            "\"Queue\" = \"Transparent\"",
            "Blend SrcAlpha OneMinusSrcAlpha",
            "StructuredBuffer<LineVertexData> _LineVertices;",
            "float3 position;",
            "uint color;"
        });

    AssertShader(
        Path.Combine(shadersDir, "VisionMaskLines.shader"),
        new[]
        {
            "\"RenderPipeline\" = \"UniversalPipeline\"",
            "\"Queue\" = \"Transparent\"",
            "_DepthOffsetMeters",
            "StructuredBuffer<LineVertexData> _LineVertices;"
        });

    AssertShader(
        Path.Combine(shadersDir, "VisionAnchorSphere.shader"),
        new[]
        {
            "\"RenderPipeline\" = \"UniversalPipeline\"",
            "\"Queue\" = \"Transparent\"",
            "#pragma multi_compile_instancing",
            "UNITY_DEFINE_INSTANCED_PROP(float4, _InstanceColor)"
        });
}

static void VisionBboxGeometry_ExpandsTwelveEdgesIntoTwentyFourVertices()
{
    Vector3[] corners =
    {
        new(0f, 0f, 0f),
        new(1f, 0f, 0f),
        new(1f, 1f, 0f),
        new(0f, 1f, 0f),
        new(0f, 0f, 1f),
        new(1f, 0f, 1f),
        new(1f, 1f, 1f),
        new(0f, 1f, 1f)
    };

    VisionLineVertex[] vertices = VisionBboxGeometry.BuildBboxLineVertices(corners, new Color32(1, 2, 3, 4));
    AssertEqual(24, vertices.Length, "bbox vertex count");
    AssertEqual(corners[0], vertices[0].Position, "edge 0 start");
    AssertEqual(corners[1], vertices[1].Position, "edge 0 end");
    AssertEqual(corners[3], vertices[6].Position, "edge 3 start");
    AssertEqual(corners[0], vertices[7].Position, "edge 3 end");
    AssertEqual(corners[2], vertices[20].Position, "edge 10 start");
    AssertEqual(corners[6], vertices[21].Position, "edge 10 end");
    AssertEqual(0x04030201u, vertices[0].Color, "packed color");
}

static void VisionBboxGeometry_ClosesContoursIntoLinePairs()
{
    Vector3[] contour =
    {
        new(0f, 0f, 0f),
        new(1f, 0f, 0f),
        new(1f, 1f, 0f)
    };

    VisionLineVertex[] vertices = new VisionLineVertex[6];
    int written = VisionBboxGeometry.WriteClosedContourLineVertices(contour, new Color32(10, 20, 30, 40), vertices, 0);
    AssertEqual(6, written, "contour vertex count");
    AssertEqual(contour[0], vertices[0].Position, "contour 0 start");
    AssertEqual(contour[1], vertices[1].Position, "contour 0 end");
    AssertEqual(contour[2], vertices[4].Position, "contour 2 start");
    AssertEqual(contour[0], vertices[5].Position, "contour closes");
    AssertEqual(0x281E140Au, vertices[0].Color, "contour packed color");
}

static void VisionObjectColorTable_MatchesTypescriptContract()
{
    string repoRoot = Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "..", ".."));
    string contractPath = Path.Combine(repoRoot, "contracts", "bbox_wireframe_render.ts");
    if (!File.Exists(contractPath))
    {
        throw new Exception($"missing contract file: {contractPath}");
    }

    string contents = File.ReadAllText(contractPath);
    Match tableMatch = Regex.Match(
        contents,
        @"OBJECT_COLOR_TABLE:\s*readonly\s*\[number,\s*number,\s*number\]\[\]\s*=\s*\[(?<body>[\s\S]*?)\];",
        RegexOptions.CultureInvariant);
    if (!tableMatch.Success)
    {
        throw new Exception("OBJECT_COLOR_TABLE not found in contract");
    }

    MatchCollection colorMatches = Regex.Matches(
        tableMatch.Groups["body"].Value,
        @"\[\s*(?<r>\d+(?:\.\d+)?)\s*,\s*(?<g>\d+(?:\.\d+)?)\s*,\s*(?<b>\d+(?:\.\d+)?)\s*\]",
        RegexOptions.CultureInvariant);

    AssertEqual(VisionObjectColorTable.PaletteSize, colorMatches.Count, "contract palette size");
    for (int index = 0; index < colorMatches.Count; index++)
    {
        Match match = colorMatches[index];
        byte expectedR = FloatChannelToByte(match.Groups["r"].Value);
        byte expectedG = FloatChannelToByte(match.Groups["g"].Value);
        byte expectedB = FloatChannelToByte(match.Groups["b"].Value);
        Color32 actual = VisionObjectColorTable.GetColor(index);
        AssertEqual(expectedR, actual.r, $"palette[{index}] red");
        AssertEqual(expectedG, actual.g, $"palette[{index}] green");
        AssertEqual(expectedB, actual.b, $"palette[{index}] blue");
        AssertEqual((byte)0xFF, actual.a, $"palette[{index}] alpha");
    }

    AssertEqual(VisionObjectColorTable.GetColor(0), VisionObjectColorTable.GetColor(16), "palette wraparound");
}

static byte FloatChannelToByte(string value)
{
    float parsed = float.Parse(value, System.Globalization.CultureInfo.InvariantCulture);
    int rounded = (int)MathF.Round(parsed * 255f, MidpointRounding.AwayFromZero);
    return (byte)Math.Clamp(rounded, 0, 255);
}

static void AssertShader(string path, IEnumerable<string> expectedSnippets)
{
    if (!File.Exists(path))
    {
        throw new Exception($"expected shader to exist: {path}");
    }

    string contents = File.ReadAllText(path);
    foreach (string snippet in expectedSnippets)
    {
        if (!contents.Contains(snippet, StringComparison.Ordinal))
        {
            throw new Exception($"shader {Path.GetFileName(path)} missing snippet: {snippet}");
        }
    }
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
