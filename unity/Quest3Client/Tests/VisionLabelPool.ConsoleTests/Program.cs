using System.Text.RegularExpressions;

string repoRoot = Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "..", "..", "..", ".."));
string labelPoolPath = Path.Combine(repoRoot, "unity", "Quest3Client", "Assets", "Scripts", "Vision", "VisionLabelPool.cs");
string overlayManagerPath = Path.Combine(repoRoot, "unity", "Quest3Client", "Assets", "Scripts", "Vision", "VisionOverlayManager.cs");

AssertFileContains(
    labelPoolPath,
    [
        "private const float DefaultExtraCapacityRatio = 0.2f;",
        "private const int DefaultFallbackMaxObjects = 60;",
        "public void SyncObjects(VisionObjectProcessedData[] objects)",
        "private bool TryAcquire(VisionObjectProcessedData processedObject)",
        "private void ReleaseAll()",
        "public void UpdateBillboards()",
        "if (_pool.Length > 0)",
        "processedObject.Center3D + ResolveLabelOffset()",
        "VisionLabelFormatting.FormatLabel(processedObject.Label, processedObject.Score)",
        "int capacity = Mathf.CeilToInt(maxObjects * (1f + DefaultExtraCapacityRatio));",
    ]);

AssertFileContains(
    overlayManagerPath,
    [
        "[SerializeField] private VisionLabelPool labelPool;",
        "labelPool ??= GetComponent<VisionLabelPool>();",
        "labelPool?.SetLabelCamera(labelCamera);",
        "receiverModule.OnFrameProcessed += HandleFrameProcessed;",
        "labelPool?.SyncObjects(objects);",
        "labelPool?.UpdateBillboards();",
    ]);

AssertMetaFile($"{labelPoolPath}.meta");
AssertMetaFile($"{overlayManagerPath}.meta");
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
