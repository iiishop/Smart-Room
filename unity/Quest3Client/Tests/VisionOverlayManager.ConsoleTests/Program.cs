using System.Text.RegularExpressions;

string repoRoot = Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "..", "..", "..", ".."));
string overlayManagerPath = Path.Combine(repoRoot, "unity", "Quest3Client", "Assets", "Scripts", "Vision", "VisionOverlayManager.cs");
string receiverPath = Path.Combine(repoRoot, "unity", "Quest3Client", "Assets", "Scripts", "Networking", "VisionReceiverModule.cs");
string prefabPath = Path.Combine(repoRoot, "unity", "Quest3Client", "Assets", "Scenes", "VisionOverlayRig.prefab");
string scenePath = Path.Combine(repoRoot, "unity", "Quest3Client", "Assets", "Scenes", "SampleScene.unity");

AssertFileContains(
    overlayManagerPath,
    [
        "public sealed class VisionOverlayManager : MonoBehaviour",
        "receiverModule.OnFrameProcessed += HandleFrameProcessed;",
        "bboxRenderer?.UpdateBuffers(objects);",
        "maskRenderer?.UpdateContours(objects);",
        "labelPool?.SyncObjects(objects);",
        "labelPool?.UpdateBillboards();",
    ]);

AssertFileContains(
    receiverPath,
    [
        "contourSamplesPerObject = Mathf.Clamp(contourSamplesPerObject, 4, 32);",
        "trackedObjectMap.TryGetValue(worldObject.ObjectId, out VisionTrackedMaskPayload trackedMask);",
        "CornersValid = cornersValid,",
        "Contour3D = contour,",
        "private Vector3[] BuildContourWorldPoints(VisionFramePayload frame, VisionTrackedMaskPayload trackedMask)",
        "private Vector2[] DecodeMaskContourPixels(VisionTrackedMaskPayload trackedMask)",
    ]);

AssertFileContains(
    prefabPath,
    [
        "m_Name: VisionOverlayRig",
        "Assembly-CSharp::SmartRoom.Vision.VisionOverlayManager",
        "Assembly-CSharp::SmartRoom.Vision.VisionBboxRenderer",
        "Assembly-CSharp::SmartRoom.Vision.VisionMaskRenderer",
        "Assembly-CSharp::SmartRoom.Vision.VisionLabelPool",
    ]);

AssertFileContains(
    scenePath,
    [
        "m_Name: VisionOverlayRig",
        "receiverModule: {fileID: 1313628478}",
        "bboxRenderer: {fileID: 2100000004}",
        "maskRenderer: {fileID: 2100000005}",
        "labelPool: {fileID: 2100000006}",
    ]);

AssertMetaFile($"{overlayManagerPath}.meta");
AssertMetaFile($"{prefabPath}.meta");
Console.WriteLine("VisionOverlayManager console smoke passed.");

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
