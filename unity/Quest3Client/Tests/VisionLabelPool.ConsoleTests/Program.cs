using System.Text.RegularExpressions;

string repoRoot = Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "..", "..", "..", ".."));
string labelPoolPath = Path.Combine(repoRoot, "unity", "Quest3Client", "Assets", "Scripts", "Vision", "VisionLabelPool.cs");
string receiverPath = Path.Combine(repoRoot, "unity", "Quest3Client", "Assets", "Scripts", "Networking", "VisionReceiverModule.cs");

AssertFileContains(
    labelPoolPath,
    [
        "private const float DefaultExtraCapacityRatio = 0.2f;",
        "private const int DefaultFallbackMaxObjects = 60;",
        "private void LateUpdate()",
        "private bool TryAcquire(VisionWorldObject worldObject)",
        "private void ReleaseAll()",
        "if (_pool.Length > 0)",
        "pooledLabel.Text.text = VisionLabelFormatting.FormatLabel(worldObject.Label, worldObject.Score);",
        "int capacity = Mathf.CeilToInt(maxObjects * (1f + DefaultExtraCapacityRatio));",
    ]);

AssertFileContains(
    receiverPath,
    [
        "[SerializeField] private VisionLabelPool labelPool;",
        "labelPool = FindFirstObjectByType<VisionLabelPool>();",
        "labelPool = gameObject.AddComponent<VisionLabelPool>();",
        "labelPool.SetLabelCamera(rayCamera);",
        "int labelCapacity = labelPool != null ? labelPool.Capacity : maxObjectsPerFrame;",
        "labelPool?.Sync(_latestWorldObjects);",
    ]);

AssertMetaFile($"{labelPoolPath}.meta");
Console.WriteLine("VisionLabelPool console tests passed.");

static void AssertFileContains(string path, IReadOnlyList<string> snippets)
{
    if (!File.Exists(path))
    {
        throw new FileNotFoundException($"Missing file: {path}");
    }

    string content = File.ReadAllText(path);
    foreach (string snippet in snippets)
    {
        if (!content.Contains(snippet, StringComparison.Ordinal))
        {
            throw new InvalidOperationException($"File {Path.GetFileName(path)} missing snippet: {snippet}");
        }
    }
}

static void AssertMetaFile(string metaPath)
{
    if (!File.Exists(metaPath))
    {
        throw new FileNotFoundException($"Missing meta file: {metaPath}");
    }

    string content = File.ReadAllText(metaPath);
    if (!Regex.IsMatch(content, @"guid:\s*[0-9a-f]{32}", RegexOptions.CultureInvariant))
    {
        throw new InvalidOperationException($"Meta file {Path.GetFileName(metaPath)} does not contain a valid guid.");
    }
}
