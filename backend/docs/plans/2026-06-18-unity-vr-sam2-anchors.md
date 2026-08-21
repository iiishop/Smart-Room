# VR Interactive SAM2 — Unity Anchor Placement & Contour Rendering

> **For OpenCode:** Use `--model openai/gpt-5.5-high`, load `ponytail` skill.
> **TDD relaxed. Work directly in current directory. Do NOT write any guide documents — only .cs files.**

**Goal:** Add VR anchor placement (green/red 3D spheres) and contour LineRenderer to the existing trigger flow. A-button toggles mode. Uploaded anchors feed SAM2's interactive segmentation. Response contour_3d renders as 3D line.

**Architecture:** Minimal changes — modify `TrackingManager.cs` (anchor list + contour renderer), reuse existing `DepthCursor` for raycast. One new static utility for anchor→JSON. No new MonoBehaviour files.

**Tech Stack:** Unity C#, existing `DepthCursor`/`ControllerRaycaster`/`Quest3RgbdCaptureFinal`, `UnityEngine.Networking` HttpClient.

**Key rule: `GetField("missing")` THROWS on IL2CPP — all reflection must be try-catch.**

---

## Existing architecture (do not break)

`TrackingManager` (on GameObject, wired in Inspector):
- Press trigger → `finalRgbdCapture.CaptureOnceToPayload()` → `UploadCaptureAsync()`
- `UploadCaptureAsync` builds `MultipartFormDataContent`:
  - `rgb_raw`/`rgb_jpeg`, `depth_raw`, `meta_json`, `cursor_json`
- Posts to `http://127.0.0.1:8500/api/track/start-final-rgbd`
- `DepthCursor` provides `IsHitting`, `HitPoint`, `HitNormal`, `HitDistance`

---

## Task 1: Add anchor data structures and A-button toggle to TrackingManager

**Objective:** Maintain a list of 3D world-anchors. A-button toggles between positive (label=1, green) and negative (label=0, red). Right trigger places anchor at current `DepthCursor.HitPoint`.

**Files:**
- Modify: `Assets/Scripts/Tracking/TrackingManager.cs` (line 22 area — add serialized fields)

**Step 1: Add serialized fields to TrackingManager class**

Add after the existing `[SerializeField]` lines (~line 22):

```csharp
[Header("VR Interactive Anchors")]
[SerializeField] private bool enableAnchors = true;
[SerializeField] private Material anchorPositiveMaterial;
[SerializeField] private Material anchorNegativeMaterial;
[SerializeField] private float anchorRadius = 0.025f;
[SerializeField] private int maxAnchors = 16;
```

**Step 2: Add runtime state to TrackingManager class**

Add as private fields:

```csharp
private List<Vector3> _anchorPositions = new List<Vector3>();
private List<int> _anchorLabels = new List<int>();
private List<GameObject> _anchorSpheres = new List<GameObject>();
private int _currentLabel = 1; // 1=positive, 0=negative, toggled by A-button
```

**Step 3: Add A-button toggle in Update()**

In `TrackingManager.Update()` (or create one if missing), before trigger logic:

```csharp
// A-button toggles anchor mode
if (OVRInput.GetDown(OVRInput.RawButton.A))
{
    _currentLabel = _currentLabel == 1 ? 0 : 1;
    Debug.Log($"[TrackingManager] Anchor mode: {(_currentLabel == 1 ? "positive (green)" : "negative (red)")}");
}
```

**Step 4: Place anchor on trigger press (if anchors enabled)**

Modify the trigger handler: when A is NOT held (trigger used for anchor), AND enableAnchors:

```csharp
// In the trigger handler, BEFORE the existing capture logic:
if (enableAnchors && depthCursor != null && depthCursor.IsHitting && _anchorPositions.Count < maxAnchors)
{
    Vector3 hit = depthCursor.GetHitPoint();
    PlaceAnchor(hit, _currentLabel);
    // Re-predict with updated anchors immediately
    _ = RePredictAsync();
    return; // Don't do full capture on anchor placement
}
```

**Step 5: Add PlaceAnchor() method**

```csharp
private void PlaceAnchor(Vector3 worldPos, int label)
{
    _anchorPositions.Add(worldPos);
    _anchorLabels.Add(label);
    
    Material mat = label == 1 ? anchorPositiveMaterial : anchorNegativeMaterial;
    if (mat == null) mat = new Material(Shader.Find("Universal Render Pipeline/Unlit"));
    
    GameObject sphere = GameObject.CreatePrimitive(PrimitiveType.Sphere);
    sphere.transform.position = worldPos;
    sphere.transform.localScale = Vector3.one * (anchorRadius * 2f);
    sphere.GetComponent<Renderer>().material = mat;
    sphere.name = $"Anchor_{label}_{_anchorPositions.Count}";
    _anchorSpheres.Add(sphere);
    
    Debug.Log($"[TrackingManager] Placed {(label == 1 ? "positive" : "negative")} anchor at {worldPos}");
}
```

---

## Task 2: Add anchor_points_json to UploadCaptureAsync multipart

**Objective:** Serialize anchor list and include in the same POST.

**Files:**
- Modify: `Assets/Scripts/Tracking/TrackingManager.cs` — `UploadCaptureAsync()` (line 150 area)

**Step 1: Add anchor JSON to multipart form**

After line 153 (`cursor_json` addition), add:

```csharp
if (enableAnchors && _anchorPositions.Count > 0)
{
    string anchorsJson = BuildAnchorsJson();
    form.Add(new StringContent(anchorsJson, Encoding.UTF8, "application/json"), "anchor_points_json");
}
```

**Step 2: Add BuildAnchorsJson() method**

```csharp
private string BuildAnchorsJson()
{
    var sb = new System.Text.StringBuilder();
    sb.Append("[");
    for (int i = 0; i < _anchorPositions.Count; i++)
    {
        if (i > 0) sb.Append(",");
        Vector3 p = _anchorPositions[i];
        sb.Append($"{{\"x\":{p.x:F4},\"y\":{p.y:F4},\"z\":{p.z:F4},\"label\":{_anchorLabels[i]}}}");
    }
    sb.Append("]");
    return sb.ToString();
}
```

---

## Task 3: Add ClearAnchors() and re_predict flag for incremental updates

**Objective:** Support clearing anchors and marking re-predict (not full capture) requests.

**Files:**
- Modify: `Assets/Scripts/Tracking/TrackingManager.cs`

**Step 1: Add ClearAnchors() method**

```csharp
private void ClearAnchors()
{
    foreach (var sphere in _anchorSpheres)
        if (sphere != null) Destroy(sphere);
    _anchorSpheres.Clear();
    _anchorPositions.Clear();
    _anchorLabels.Clear();
}
```

**Step 2: Add RePredictAsync() for incremental anchor updates**

```csharp
private async Task RePredictAsync()
{
    // Only send re-predict if we have anchors AND a previous capture exists
    if (_anchorPositions.Count == 0 || _lastCapturePayload == null) return;
    
    try
    {
        using var http = new HttpClient { Timeout = TimeSpan.FromSeconds(requestTimeoutSeconds) };
        using var form = new MultipartFormDataContent();
        
        // Re-send RGB (needed for SAM2 to set_image if embedding expired)
        var capture = _lastCapturePayload;
        if (capture.rgbRawBytes != null && capture.rgbRawBytes.Length > 0)
            AddBinaryPart(form, "rgb_raw", "rgb.raw", capture.rgbRawBytes, "application/octet-stream");
        else if (capture.rgbJpegBytes != null && capture.rgbJpegBytes.Length > 0)
            AddBinaryPart(form, "rgb_jpeg", "rgb.jpg", capture.rgbJpegBytes, "image/jpeg");
        
        AddBinaryPart(form, "depth_raw", "depth.raw", capture.depthRawBytes, "application/octet-stream");
        form.Add(new StringContent(capture.metaJson, Encoding.UTF8, "application/json"), "meta_json");
        
        string anchorsJson = BuildAnchorsJson();
        form.Add(new StringContent(anchorsJson, Encoding.UTF8, "application/json"), "anchor_points_json");
        form.Add(new StringContent("true", Encoding.UTF8, "text/plain"), "re_predict");
        
        string url = BuildUrl(backendBaseUrl, uploadPath);
        using HttpResponseMessage response = await http.PostAsync(url, form);
        string body = await response.Content.ReadAsStringAsync();
        
        if (response.IsSuccessStatusCode)
            HandleSegmentationResponse(body);
    }
    catch (Exception ex)
    {
        Debug.LogWarning($"[TrackingManager] Re-predict error: {ex.Message}");
    }
}
```

**Step 3: Add _lastCapturePayload field**

```csharp
private Quest3RgbdCaptureFinal.CapturePayload _lastCapturePayload;
```

**Step 4: Save capture in existing trigger flow**

After `finalRgbdCapture.CaptureOnceToPayload(out var capture)` succeeds, add:

```csharp
_lastCapturePayload = capture;
```

---

## Task 4: Parse contour_3d response and render LineRenderer

**Objective:** Extract `device.contour_3d` from JSON response, create/update a LineRenderer loop.

**Files:**
- Modify: `Assets/Scripts/Tracking/TrackingManager.cs`

**Step 1: Add contour renderer fields**

```csharp
[Header("Contour Rendering")]
[SerializeField] private Material contourMaterial;
[SerializeField] private float contourWidth = 0.005f;
[SerializeField] private Color contourColor = Color.white;
private LineRenderer _contourRenderer;
private GameObject _contourObject;
```

**Step 2: Add HandleSegmentationResponse() method**

```csharp
private void HandleSegmentationResponse(string jsonBody)
{
    try
    {
        var response = JsonUtility.FromJson<ViewerResponse>(jsonBody);
        if (response?.device?.contour_3d == null || response.device.contour_3d.Length == 0)
        {
            Debug.Log("[TrackingManager] No contour in response");
            ClearContour();
            return;
        }
        RenderContour(response.device.contour_3d);
    }
    catch (Exception ex)
    {
        Debug.LogWarning($"[TrackingManager] Parse response error: {ex.Message}");
    }
}

[Serializable]
private class ViewerResponse
{
    public DeviceInfo device;
}

[Serializable]
private class DeviceInfo
{
    public ContourPoint[] contour_3d;
    public bool segmented;
}

[Serializable]
private class ContourPoint
{
    public float x, y, z;
}
```

**Step 3: Add RenderContour() method**

```csharp
private void RenderContour(ContourPoint[] points)
{
    if (points.Length < 3) { ClearContour(); return; }
    
    if (_contourObject == null)
    {
        _contourObject = new GameObject("SAM2_Contour");
        _contourRenderer = _contourObject.AddComponent<LineRenderer>();
        _contourRenderer.material = contourMaterial ?? new Material(Shader.Find("Universal Render Pipeline/Unlit"));
        _contourRenderer.startColor = contourColor;
        _contourRenderer.endColor = contourColor;
        _contourRenderer.startWidth = contourWidth;
        _contourRenderer.endWidth = contourWidth;
        _contourRenderer.loop = true;
        _contourRenderer.positionCount = 0;
    }
    
    _contourRenderer.positionCount = points.Length;
    for (int i = 0; i < points.Length; i++)
        _contourRenderer.SetPosition(i, new Vector3(points[i].x, points[i].y, points[i].z));
    
    Debug.Log($"[TrackingManager] Contour rendered: {points.Length} points");
}

private void ClearContour()
{
    if (_contourRenderer != null) _contourRenderer.positionCount = 0;
}
```

**Step 4: Call HandleSegmentationResponse() from UploadCaptureAsync()**

After the existing `Debug.Log("Uploaded RGB-D...")` line (~line 164):

```csharp
HandleSegmentationResponse(body);
```

---

## Task 5: Wire anchor initialize/clear in OnTriggerPressed

**Objective:** Add button to clear anchors (B button press-and-hold, or long press).

**Files:**
- Modify: `Assets/Scripts/Tracking/TrackingManager.cs`

```csharp
// In Update(), after A-button toggle:
if (OVRInput.GetDown(OVRInput.RawButton.B))
{
    ClearAnchors();
    Debug.Log("[TrackingManager] Anchors cleared");
}
```

---

## Task 6: Commit

```bash
git add Assets/Scripts/Tracking/TrackingManager.cs
git commit -m "feat(unity): VR interactive SAM2 anchor placement and contour rendering"
```
