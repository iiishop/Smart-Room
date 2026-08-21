# VR SAM2 Fixes + Status Ball — Implementation Plan

> **For Hermes:** Dispatch to OpenCode with `--model openai/gpt-5.4`, load `ponytail` skill.

**Goal:** Fix 4 bugs + 1 feature in the VR interactive SAM2 pipeline.

**Fix 1:** `contour_3d` format mismatch — Python outputs `[[x,y,z],...]`, Unity expects `[{"x":...,"y":...,"z":...},...]` and looks in `response.device.contour_3d`.

**Fix 2:** Start new capture — Grip button triggers fresh `set_image()` while preserving anchors.

**Fix 3:** Delete single anchor — B short press removes last anchor + re-predict.

**Fix 4:** Status indicator ball color — the cursor ball follows A-button toggle (green/red).

**Architecture:** Python fix in `quest3_rgbd_align_viewer.py` response builder. Unity fix in `TrackingManager.cs`. No new files.

---

## Task 1: Fix contour_3d format in Python response

**Files:** Modify `viewer/quest3_rgbd_align_viewer.py` — HTTP handler response (lines ~1380-1394)

**Change:** Build `contour_3d` as array of `{"x","y","z"}` objects, NOT nested arrays. Keep inside `device`.

```python
# REPLACE the device section in response dict:
"device": {
    "segmented": frame.device_mask_path is not None,
    "mask": str(frame.device_mask_path) if frame.device_mask_path is not None else None,
    "area_px": int(frame.device_info.get("area_px", 0)) if frame.device_info else 0,
    "bbox_xyxy": frame.device_info.get("bbox_xyxy") if frame.device_info else None,
    "contour_3d": _build_device_contour(frame),
}

# ADD helper function before _PayloadHandler:
def _build_device_contour(frame: FrameData) -> list[dict] | None:
    """Build contour_3d as [{x,y,z},...] for Unity LineRenderer."""
    contour = getattr(frame, "device_contour_3d", None)
    if contour is None or len(contour) == 0:
        return None
    return [
        {"x": round(float(p[0]), 4), "y": round(float(p[1]), 4), "z": round(float(p[2]), 4)}
        for p in contour
    ]
```

**Verify:** `uv run python -c "from quest3_rgbd_align_viewer import _build_device_contour; print('OK')"`

---

## Task 2: Fix anchor status ball color sync

**Files:** Modify `TrackingManager.cs`

**Change:** After A-button toggle, update the cursor ball material color:

```csharp
// In Update(), after A-button handling:
if (OVRInput.GetDown(OVRInput.RawButton.A))
{
    _currentLabel = _currentLabel == 1 ? 0 : 1;
    Debug.Log($"[TrackingManager] Anchor mode: {(_currentLabel == 1 ? "positive (green)" : "negative (red)")}");
    
    // Update cursor ball color if depthCursor has a visual indicator
    if (depthCursor != null)
    {
        var cursorRenderer = depthCursor.GetComponent<Renderer>();
        // If DepthCursor doesn't have its own Renderer, create a status sphere
        // or use the built-in cursor visual if it exists
    }
}
```

But DepthCursor likely doesn't expose a public method to change its color. The cleaner approach: add a small status sphere that follows the cursor world point, visible in VR. Same color logic as anchor spheres.

**Change in Update():**
```csharp
// In Update(), after A-button toggle, position status sphere:
if (_statusSphere == null)
{
    _statusSphere = GameObject.CreatePrimitive(PrimitiveType.Sphere);
    _statusSphere.transform.localScale = Vector3.one * (anchorRadius * 1.2f);
    Destroy(_statusSphere.GetComponent<Collider>());
    _statusSphere.name = "AnchorStatusIndicator";
}
if (depthCursor != null && depthCursor.IsHitting)
{
    _statusSphere.transform.position = depthCursor.GetHitPoint();
    _statusSphere.GetComponent<Renderer>().material = 
        _currentLabel == 1 ? anchorPositiveMaterial : anchorNegativeMaterial;
    _statusSphere.SetActive(true);
}
else
{
    _statusSphere.SetActive(false);
}
```

**Verify:** `dotnet build "Assembly-CSharp.csproj"` in Unity project — 0 errors.

---

## Task 3: Grip button → new capture (preserves anchors)

**Files:** Modify `TrackingManager.cs`

**Change:** In Update(), add Grip handler:

```csharp
// After A-button handling:
if (OVRInput.GetDown(OVRInput.RawButton.RHandTrigger))  // Grip
{
    _lastTriggerAt = Time.time;
    _uploadInFlight = true;
    _ = CaptureNewFrameAsync();
}
```

**Add method:**
```csharp
private async Task CaptureNewFrameAsync()
{
    try
    {
        var capture = finalRgbdCapture.CaptureOnceToPayload();
        if (!capture.success)
        {
            ShowStatus("Capture failed");
            _uploadInFlight = false;
            return;
        }
        _lastCapturePayload = capture;
        
        // Upload with anchors (re-uses existing capture → viewer re-set_image → re-predict)
        using var http = new HttpClient { Timeout = TimeSpan.FromSeconds(requestTimeoutSeconds) };
        using var form = new MultipartFormDataContent();
        // ... same upload logic as first capture, including anchors ...
        string url = BuildUrl(backendBaseUrl, uploadPath);
        using HttpResponseMessage response = await http.PostAsync(url, form);
        string body = await response.Content.ReadAsStringAsync();
        if (response.IsSuccessStatusCode)
            HandleSegmentationResponse(body);
    }
    catch (Exception ex) { Debug.LogWarning($"[TrackingManager] Re-capture error: {ex.Message}"); }
    finally { _uploadInFlight = false; }
}
```

Wait — this duplicates upload logic. Better: extract upload logic into `UploadAndSegmentAsync(capture, includeRePredict)` and call it from both paths.

**Refactor:** Extract `UploadAndSegmentAsync`:

```csharp
private async Task UploadAndSegmentAsync(Quest3RgbdCaptureFinal.CapturePayload capture, bool rePredict)
{
    using var http = new HttpClient { Timeout = TimeSpan.FromSeconds(requestTimeoutSeconds) };
    using var form = new MultipartFormDataContent();
    
    var canSendRaw = sendRgbRaw && capture.rgbRawBytes != null && capture.rgbRawBytes.Length > 0;
    if (canSendRaw)
        AddBinaryPart(form, "rgb_raw", "rgb.raw", capture.rgbRawBytes, "application/octet-stream");
    if (!canSendRaw || includeJpegFallback)
        AddBinaryPart(form, "rgb_jpeg", "rgb.jpg", capture.rgbJpegBytes, "image/jpeg");
    AddBinaryPart(form, "depth_raw", "depth.raw", capture.depthRawBytes, "application/octet-stream");
    form.Add(new StringContent(capture.metaJson, Encoding.UTF8, "application/json"), "meta_json");
    
    string cursorJson = BuildCursorJson();
    if (includeCursorPrompt && !string.IsNullOrWhiteSpace(cursorJson))
        form.Add(new StringContent(cursorJson, Encoding.UTF8, "application/json"), "cursor_json");
    
    if (enableAnchors && _anchorPositions.Count > 0)
        form.Add(new StringContent(BuildAnchorsJson(), Encoding.UTF8, "application/json"), "anchor_points_json");
    
    if (rePredict)
        form.Add(new StringContent("true", Encoding.UTF8, "text/plain"), "re_predict");
    
    string url = BuildUrl(backendBaseUrl, uploadPath);
    using HttpResponseMessage response = await http.PostAsync(url, form);
    string body = await response.Content.ReadAsStringAsync();
    if (response.IsSuccessStatusCode)
    {
        Debug.Log($"[TrackingManager] Upload OK: rgb={capture.rgbWidth}x{capture.rgbHeight}");
        HandleSegmentationResponse(body);
    }
}
```

Then:
- First capture: `await UploadAndSegmentAsync(capture, rePredict: false)`
- Re-predict: `await UploadAndSegmentAsync(_lastCapturePayload, rePredict: true)`
- New frame: `await UploadAndSegmentAsync(capture, rePredict: true)` (includes anchors)

---

## Task 4: B short press → undo last anchor, B long press → clear all

**Files:** Modify `TrackingManager.cs`

**Change:** Replace B-button handler:

```csharp
// Track B press time for short/long distinction
private float _bPressTime = -1f;

// In Update():
if (OVRInput.GetDown(OVRInput.RawButton.B))
{
    _bPressTime = Time.time;
}
if (OVRInput.GetUp(OVRInput.RawButton.B))
{
    float holdDuration = Time.time - _bPressTime;
    if (holdDuration < 0.5f)  // short press
    {
        UndoLastAnchor();
        _ = RePredictAsync();
    }
    else  // long press
    {
        ClearAnchors();
        Debug.Log("[TrackingManager] All anchors cleared");
    }
    _bPressTime = -1f;
}
```

**Add method:**
```csharp
private void UndoLastAnchor()
{
    if (_anchorPositions.Count == 0) return;
    int idx = _anchorPositions.Count - 1;
    if (_anchorSpheres[idx] != null)
        Destroy(_anchorSpheres[idx]);
    _anchorSpheres.RemoveAt(idx);
    _anchorPositions.RemoveAt(idx);
    _anchorLabels.RemoveAt(idx);
    Debug.Log($"[TrackingManager] Removed last anchor, {_anchorPositions.Count} remaining");
}
```

---

## Task 5: Verify build

```bash
dotnet build "Assembly-CSharp.csproj"
```
Expected: 0 errors.

---

## Task 6: Commit

```bash
git add unity/Quest3Client/Assets/Scripts/Tracking/TrackingManager.cs viewer/quest3_rgbd_align_viewer.py
git commit -m "fix: contour format, cursor status ball, grip recapture, B undo"
```
