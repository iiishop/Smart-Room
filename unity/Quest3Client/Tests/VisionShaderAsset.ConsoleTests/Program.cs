using System.Text.RegularExpressions;

string repoRoot = Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "..", "..", "..", ".."));
string shaderDir = Path.Combine(repoRoot, "unity", "Quest3Client", "Assets", "Scripts", "Rendering");

AssertShader(
    Path.Combine(shaderDir, "VisionLineUnlit.shader"),
    [
        "Shader \"SmartRoom/VisionLineUnlit\"",
        "StructuredBuffer<LineVertexData> _LineVertices;",
        "TransformWorldToHClip(vertex.positionWS)",
        "Blend SrcAlpha OneMinusSrcAlpha",
        "ZTest Always",
    ]);

AssertShader(
    Path.Combine(shaderDir, "VisionOverlayUnlit.shader"),
    [
        "Shader \"SmartRoom/VisionOverlayUnlit\"",
        "[PerRendererData] _MainTex (\"Sprite Texture\", 2D) = \"white\" {}",
        "#pragma multi_compile_local _ UNITY_UI_CLIP_RECT",
        "ZTest [unity_GUIZTestMode]",
        "\"CanUseSpriteAtlas\" = \"True\"",
    ]);

Console.WriteLine("VisionShaderAsset console tests passed.");

static void AssertShader(string path, IReadOnlyList<string> expectedSnippets)
{
    if (!File.Exists(path))
    {
        throw new FileNotFoundException($"Missing shader asset: {path}");
    }

    string content = File.ReadAllText(path);
    foreach (string snippet in expectedSnippets)
    {
        if (!content.Contains(snippet, StringComparison.Ordinal))
        {
            throw new InvalidOperationException($"Shader {Path.GetFileName(path)} missing snippet: {snippet}");
        }
    }

    string metaPath = $"{path}.meta";
    if (!File.Exists(metaPath))
    {
        throw new FileNotFoundException($"Missing meta file: {metaPath}");
    }

    string metaContent = File.ReadAllText(metaPath);
    if (!Regex.IsMatch(metaContent, @"guid:\s*[0-9a-f]{32}", RegexOptions.CultureInvariant))
    {
        throw new InvalidOperationException($"Meta file {Path.GetFileName(metaPath)} does not contain a valid guid.");
    }
}
