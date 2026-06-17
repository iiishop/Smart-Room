using System;
using System.Text;
using System.Threading.Tasks;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using SmartRoom.Capture;
using UnityEngine;

namespace SmartRoom.Tracking
{
    /// <summary>
    /// Single-shot object detection + 3D anchoring for Quest 3.
    ///
    /// Flow:
    ///   1. User aims green depth cursor at object
    ///   2. Presses trigger -> Quest3RgbdCaptureFinal -> POST /api/track/start-final-rgbd
    ///   3. Backend aligns final RGB-D and returns {label, bbox, center_pixel}
    ///   4. Bbox anchored at 3D world position from depth cursor → stays on object
    ///
    /// References:
    ///   - Meta XR SDK v85: OVRInput.Get(OVRInput.RawButton.RIndexTrigger)
    ///     https://developer.oculus.com/documentation/unity/unity-ovrinput/
    ///   - Unity Camera.WorldToScreenPoint:
    ///     https://docs.unity3d.com/ScriptReference/Camera.WorldToScreenPoint.html
    /// </summary>
    public sealed class TrackingManager : MonoBehaviour
    {
        [Serializable]
        private sealed class TriggerCaptureBundle
        {
            public long triggerTimestampMs;
            public long pcaTimestampMs;
            public int unityFrameCount;
            public float hitX;
            public float hitY;
            public float hitZ;
            public float pixelX;
            public float pixelY;
            public float viewportX;
            public float viewportY;
            public int rgbFrameWidth;
            public int rgbFrameHeight;
            public int rgbRequestedWidth;
            public int rgbRequestedHeight;
            public int rgbCurrentWidth;
            public int rgbCurrentHeight;
            public float[] rgbIntrinsics9;
            public float[] rgbPose7;
        }

        [Header("References")]
        [SerializeField] private Interaction.DepthCursor depthCursor;
        [SerializeField] private Interaction.ControllerRaycaster controllerRaycaster;
        [SerializeField] private Networking.PixelProjector pixelProjector;
        [SerializeField] private Quest3RgbdCaptureFinal finalRgbdCapture;
        [SerializeField] private Camera xrCamera;

        [Header("Backend")]
        [SerializeField] private string backendBaseUrl = "http://localhost:8500";

        [Header("Display")]
        [SerializeField] private Material bboxLineMaterial;
        [SerializeField] private float bboxLineWidth = 0.005f;
        [SerializeField] private Color bboxColor = new Color(0f, 1f, 0.5f, 1f);

        // ── Runtime state ──

        private bool _prevTriggerPressed;
        private bool _isTracking;
        private string _currentLabel = "";

        // 3D box rendering
        private LineRenderer _bboxRenderer;
        private GameObject _labelObject;
        private TextMesh _labelText;
        private GameObject _statusTextObject;
        private TextMesh _statusText;

        // Anchored world position
        private Vector3 _anchoredWorldPos;
        private float _bboxWorldHalfSize = 0.15f; // default ~30cm box

        // ── Unity lifecycle ──

        private void Awake()
        {
            depthCursor ??= FindFirstObjectByType<Interaction.DepthCursor>();
            controllerRaycaster ??= FindFirstObjectByType<Interaction.ControllerRaycaster>();
            pixelProjector ??= FindFirstObjectByType<Networking.PixelProjector>();
            finalRgbdCapture ??= FindFirstObjectByType<Quest3RgbdCaptureFinal>();
            if (finalRgbdCapture == null)
                finalRgbdCapture = gameObject.AddComponent<Quest3RgbdCaptureFinal>();
            xrCamera ??= Camera.main;
        }

        private void Start()
        {
            CreateBboxRenderer();
            CreateLabelObject();
            CreateStatusText();
        }

        private void Update()
        {
            HandleTriggerInput();

            if (_isTracking)
            {
                UpdateBboxBillboard();
                UpdateLabelPosition();
            }
        }

        private void OnDestroy()
        {
            StopTracking();
        }

        // ── Trigger handling ──

        private void HandleTriggerInput()
        {
            // OVRInput: Meta XR SDK v85
            // https://developer.oculus.com/documentation/unity/unity-ovrinput/
            bool triggerPressed = OVRInput.Get(OVRInput.RawButton.RIndexTrigger);

            if (triggerPressed && !_prevTriggerPressed)
            {
                OnTriggerPressed();
            }

            _prevTriggerPressed = triggerPressed;
        }

        private async void OnTriggerPressed()
        {
            if (!depthCursor.IsHitting)
            {
                ShowStatus("No surface hit — aim at an object");
                return;
            }

            // Stop previous tracking
            StopTracking();

            // Get world hit point from depth cursor (used for 3D bbox anchoring)
            Vector3 hitPoint = depthCursor.HitPoint;
            _anchoredWorldPos = hitPoint;

            // ── Compute PCA pixel using Meta's WorldToViewportPoint ──
            // This is the official API that handles PCA intrinsics + sensor
            // calibration internally, avoiding manual coordinate chain errors.
            Vector2 pixel;
            TriggerCaptureBundle bundle = null;
            if (pixelProjector != null && pixelProjector.IsReady
                && pixelProjector.CameraAccess != null
                && pixelProjector.CameraAccess.IsPlaying)
            {
                var pca = pixelProjector.CameraAccess;
                // Cache camera pose at trigger instant so the projection uses
                // the exact pose when the user pulled the trigger, not the pose
                // at callback time (they could differ by a frame).
                // Reference: Meta PCA WorldToViewportPoint(Vector3, Pose?)
                //   https://developers.meta.com/horizon/reference/mruk/v85/
                var triggerPose = pca.GetCameraPose();
                long triggerTimestampMs = pca.Timestamp == default
                    ? DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()
                    : new DateTimeOffset(DateTime.SpecifyKind(pca.Timestamp, DateTimeKind.Utc)).ToUnixTimeMilliseconds();
                Vector2 viewport = pca.WorldToViewportPoint(hitPoint, triggerPose);
                // Meta viewport: (0,0)=bottom-left, (1,1)=top-right.
                // If the hit point is already outside the PCA image frustum,
                // fail closed instead of clamping to an arbitrary image edge.
                bool insideViewport =
                    viewport.x >= 0f && viewport.x <= 1f &&
                    viewport.y >= 0f && viewport.y <= 1f;
                if (!insideViewport)
                {
                    Debug.LogWarning(
                        $"[TrackingManager] Trigger rejected: viewport out of bounds. " +
                        $"viewport=({viewport.x:F4},{viewport.y:F4}), hit=({hitPoint.x:F3},{hitPoint.y:F3},{hitPoint.z:F3})"
                    );
                    ShowStatus("Target is outside RGB camera view");
                    return;
                }

                // Our stream JPEG: Y=0 at top. Flip Y from Meta viewport.
                float streamPx = viewport.x * pixelProjector.ImageWidth;
                float streamPy = (1f - viewport.y) * pixelProjector.ImageHeight;
                pixel = new Vector2(
                    Mathf.Clamp(streamPx, 0, pixelProjector.ImageWidth - 1),
                    Mathf.Clamp(streamPy, 0, pixelProjector.ImageHeight - 1)
                );
                bundle = CreateTriggerCaptureBundle(
                    triggerTimestampMs,
                    triggerPose,
                    hitPoint,
                    pixel,
                    viewport
                );
                Debug.Log(
                    $"[TrackingManager] Trigger bundle seed: ts_ms={triggerTimestampMs}, " +
                    $"hit=({hitPoint.x:F3},{hitPoint.y:F3},{hitPoint.z:F3}), " +
                    $"pose_pos=({triggerPose.position.x:F3},{triggerPose.position.y:F3},{triggerPose.position.z:F3}), " +
                    $"pose_rot=({triggerPose.rotation.x:F4},{triggerPose.rotation.y:F4},{triggerPose.rotation.z:F4},{triggerPose.rotation.w:F4}), " +
                    $"viewport=({viewport.x:F4},{viewport.y:F4}), rgb_px=({streamPx:F1},{streamPy:F1})"
                );
            }
            else if (pixelProjector != null && pixelProjector.IsReady)
            {
                // Backup pixel projection when the Meta viewport projection path is unavailable.
                var projPixel = pixelProjector.WorldToPixel(hitPoint);
                pixel = projPixel ?? WorldToScreenPoint(hitPoint);
            }
            else
            {
                pixel = WorldToScreenPoint(hitPoint);
            }

            Debug.Log($"[TrackingManager] Trigger at world={hitPoint}, pixel=({pixel.x:F0},{pixel.y:F0})");

            ShowStatus("Detecting...");
            await DetectAsync(pixel, bundle);
        }

        private Vector2 WorldToScreenPoint(Vector3 worldPoint)
        {
            // Unity Camera.WorldToScreenPoint:
            // https://docs.unity3d.com/ScriptReference/Camera.WorldToScreenPoint.html
            Vector3 screenPoint = xrCamera.WorldToScreenPoint(worldPoint);
            return new Vector2(screenPoint.x, Screen.height - screenPoint.y);
        }

        // ── Backend communication ──

        private float[] BuildRgbIntrinsics()
        {
            // Standard 3×3 intrinsic matrix, row-major:
            // [fx,  0, cx]
            // [ 0, fy, cy]
            // [ 0,  0,  1]
            if (pixelProjector == null || !pixelProjector.IsReady)
                return null;

            return new float[] {
                pixelProjector.FocalPixels.x, 0f, pixelProjector.PrincipalPoint.x,
                0f, pixelProjector.FocalPixels.y, pixelProjector.PrincipalPoint.y,
                0f, 0f, 1f,
            };
        }

        private TriggerCaptureBundle CreateTriggerCaptureBundle(
            long triggerTimestampMs,
            Pose triggerPose,
            Vector3 hitPoint,
            Vector2 pixel,
            Vector2 viewport)
        {
            if (pixelProjector == null || pixelProjector.CameraAccess == null)
                return null;

            var pca = pixelProjector.CameraAccess;
            var bundle = new TriggerCaptureBundle
            {
                triggerTimestampMs = triggerTimestampMs,
                pcaTimestampMs = triggerTimestampMs,
                unityFrameCount = Time.frameCount,
                hitX = hitPoint.x,
                hitY = hitPoint.y,
                hitZ = hitPoint.z,
                pixelX = pixel.x,
                pixelY = pixel.y,
                viewportX = viewport.x,
                viewportY = viewport.y,
                rgbFrameWidth = pixelProjector.ImageWidth,
                rgbFrameHeight = pixelProjector.ImageHeight,
                rgbRequestedWidth = pca.RequestedResolution.x,
                rgbRequestedHeight = pca.RequestedResolution.y,
                rgbCurrentWidth = pca.CurrentResolution.x,
                rgbCurrentHeight = pca.CurrentResolution.y,
                rgbIntrinsics9 = BuildRgbIntrinsics(),
                rgbPose7 = new[]
                {
                    triggerPose.position.x, triggerPose.position.y, triggerPose.position.z,
                    triggerPose.rotation.x, triggerPose.rotation.y, triggerPose.rotation.z, triggerPose.rotation.w,
                },
            };

            Debug.Log(
                $"[TrackingManager] Trigger bundle frozen: ts_ms={bundle.triggerTimestampMs}, " +
                $"pca_ts_ms={bundle.pcaTimestampMs}, " +
                $"unity_frame={bundle.unityFrameCount}, rgb={bundle.rgbFrameWidth}x{bundle.rgbFrameHeight}, " +
                $"pca_current={bundle.rgbCurrentWidth}x{bundle.rgbCurrentHeight}"
            );

            return bundle;
        }

        private object BuildTriggerBundleMetaPayload(TriggerCaptureBundle bundle)
        {
            if (bundle == null)
                return null;

            return new
            {
                trigger_timestamp_ms = bundle.triggerTimestampMs,
                unity_frame_count = bundle.unityFrameCount,
                hit_xyz = new[] { bundle.hitX, bundle.hitY, bundle.hitZ },
                pixel_xy = new[] { bundle.pixelX, bundle.pixelY },
                cursor_viewport_xy = new[] { bundle.viewportX, bundle.viewportY },
                rgb_frame_wh = new[] { bundle.rgbFrameWidth, bundle.rgbFrameHeight },
                rgb_requested_wh = new[] { bundle.rgbRequestedWidth, bundle.rgbRequestedHeight },
                rgb_current_wh = new[] { bundle.rgbCurrentWidth, bundle.rgbCurrentHeight },
                rgb_intrinsics9 = bundle.rgbIntrinsics9,
                rgb_pose7 = bundle.rgbPose7,
            };
        }

        private Vector2 MapPixelToFinalCapture(
            Vector2 streamPixel,
            TriggerCaptureBundle bundle,
            Quest3RgbdCaptureFinal.CapturePayload capture)
        {
            if (bundle != null
                && capture != null
                && capture.rgbWidth > 0
                && capture.rgbHeight > 0
                && bundle.viewportX >= 0f
                && bundle.viewportX <= 1f
                && bundle.viewportY >= 0f
                && bundle.viewportY <= 1f)
            {
                return new Vector2(
                    Mathf.Clamp(bundle.viewportX * capture.rgbWidth, 0, capture.rgbWidth - 1),
                    Mathf.Clamp((1f - bundle.viewportY) * capture.rgbHeight, 0, capture.rgbHeight - 1)
                );
            }

            if (bundle != null
                && capture != null
                && bundle.rgbFrameWidth > 0
                && bundle.rgbFrameHeight > 0
                && capture.rgbWidth > 0
                && capture.rgbHeight > 0)
            {
                return new Vector2(
                    Mathf.Clamp(streamPixel.x * capture.rgbWidth / bundle.rgbFrameWidth, 0, capture.rgbWidth - 1),
                    Mathf.Clamp(streamPixel.y * capture.rgbHeight / bundle.rgbFrameHeight, 0, capture.rgbHeight - 1)
                );
            }

            return streamPixel;
        }

        private bool ApplyTrackingResultJson(string responseJson)
        {
            var result = JObject.Parse(responseJson);

            if (result["ok"]?.Value<bool>() == true)
            {
                var trackResult = result["result"];
                _currentLabel = trackResult["label"]?.Value<string>() ?? "object";
                _isTracking = true;

                var box = trackResult["box_xyxy"];
                if (box != null && box.HasValues)
                {
                    float bw = box[2]?.Value<float>() ?? 100 - (box[0]?.Value<float>() ?? 0);
                    float bh = box[3]?.Value<float>() ?? 100 - (box[1]?.Value<float>() ?? 0);
                    float dist = depthCursor.HitDistance;
                    float fovScale = dist / 500f;
                    _bboxWorldHalfSize = Mathf.Max(bw, bh) * fovScale * 0.5f;
                    _bboxWorldHalfSize = Mathf.Clamp(_bboxWorldHalfSize, 0.05f, 1.0f);
                }

                HideStatus();
                Debug.Log($"[TrackingManager] Detected: {_currentLabel}");
                return true;
            }

            ShowStatus("Detection returned no result");
            return false;
        }

        private async Task<bool> DetectWithFinalRgbdAsync(Vector2 streamPixel, TriggerCaptureBundle bundle)
        {
            if (finalRgbdCapture == null)
                return false;

            try
            {
                if (!finalRgbdCapture.CaptureOnceToPayload(out var capture) || capture == null)
                {
                    Debug.LogWarning("[TrackingManager] Final RGB-D capture unavailable");
                    return false;
                }

                Vector2 finalPixel = MapPixelToFinalCapture(streamPixel, bundle, capture);
                var payload = JsonConvert.SerializeObject(new
                {
                    pixel_x = finalPixel.x,
                    pixel_y = finalPixel.y,
                    rgb_jpeg_b64 = System.Convert.ToBase64String(capture.rgbJpegBytes),
                    depth_raw_f32_le_b64 = System.Convert.ToBase64String(capture.depthRawBytes),
                    meta_json = capture.metaJson,
                    trigger_bundle_meta = BuildTriggerBundleMetaPayload(bundle),
                    final_capture_meta = new
                    {
                        rgb_wh = new[] { capture.rgbWidth, capture.rgbHeight },
                        depth_wh = new[] { capture.depthWidth, capture.depthHeight },
                        unity_frame_count = capture.unityFrame,
                        timestamp_unix_ms = capture.timestampUnixMs,
                    },
                });

                using var http = new System.Net.Http.HttpClient { Timeout = TimeSpan.FromSeconds(30) };
                var content = new System.Net.Http.StringContent(payload, Encoding.UTF8, "application/json");
                var response = await http.PostAsync($"{backendBaseUrl}/api/track/start-final-rgbd", content);
                if (!response.IsSuccessStatusCode)
                {
                    string errBody = await response.Content.ReadAsStringAsync();
                    Debug.LogWarning($"[TrackingManager] Final RGB-D detect failed ({response.StatusCode}): {errBody}");
                    return false;
                }

                string responseJson = await response.Content.ReadAsStringAsync();
                return ApplyTrackingResultJson(responseJson);
            }
            catch (Exception ex)
            {
                Debug.LogWarning($"[TrackingManager] Final RGB-D detect error: {ex.Message}");
                return false;
            }
        }

        private async Task DetectAsync(Vector2 pixel, TriggerCaptureBundle bundle)
        {
            try
            {
                bool finalOk = await DetectWithFinalRgbdAsync(pixel, bundle);
                if (!finalOk)
                {
                    ShowStatus("Final RGB-D failed");
                }
            }
            catch (System.Net.Http.HttpRequestException)
            {
                ShowStatus("Backend not reachable");
            }
            catch (Exception ex)
            {
                Debug.LogError($"[TrackingManager] Detect error: {ex.Message}");
                ShowStatus("Detection error");
            }
        }

        // ── Visual rendering ──

        private void CreateBboxRenderer()
        {
            var go = new GameObject("TrackingBbox");
            go.transform.SetParent(transform);
            _bboxRenderer = go.AddComponent<LineRenderer>();
            _bboxRenderer.material = bboxLineMaterial ?? new Material(Shader.Find("Sprites/Default"));
            _bboxRenderer.startWidth = bboxLineWidth;
            _bboxRenderer.endWidth = bboxLineWidth;
            _bboxRenderer.startColor = bboxColor;
            _bboxRenderer.endColor = bboxColor;
            _bboxRenderer.loop = true;
            _bboxRenderer.positionCount = 5;
            _bboxRenderer.useWorldSpace = true;
            _bboxRenderer.enabled = false;
        }

        private void UpdateBboxBillboard()
        {
            if (_bboxRenderer == null) return;
            _bboxRenderer.enabled = true;

            Vector3 c = _anchoredWorldPos;
            Vector3 camRight = xrCamera.transform.right;
            Vector3 camUp = xrCamera.transform.up;
            float hs = _bboxWorldHalfSize;

            Vector3[] corners = new Vector3[5];
            corners[0] = c - camRight * hs - camUp * hs;
            corners[1] = c + camRight * hs - camUp * hs;
            corners[2] = c + camRight * hs + camUp * hs;
            corners[3] = c - camRight * hs + camUp * hs;
            corners[4] = corners[0];

            _bboxRenderer.SetPositions(corners);
        }

        private void CreateLabelObject()
        {
            _labelObject = new GameObject("TrackingLabel");
            _labelObject.transform.SetParent(transform);
            _labelText = _labelObject.AddComponent<TextMesh>();
            _labelText.fontSize = 48;
            _labelText.characterSize = 0.015f;
            _labelText.anchor = TextAnchor.MiddleCenter;
            _labelText.color = Color.white;
            _labelText.text = "";
        }

        private void UpdateLabelPosition()
        {
            if (_labelObject == null || _labelText == null) return;

            _labelText.text = _currentLabel;
            _labelObject.transform.position = _anchoredWorldPos + Vector3.up * (_bboxWorldHalfSize + 0.04f);
            _labelObject.transform.LookAt(xrCamera.transform);
            _labelObject.transform.Rotate(0, 180, 0);
        }

        private void CreateStatusText()
        {
            _statusTextObject = new GameObject("TrackingStatus");
            _statusTextObject.transform.SetParent(transform);
            _statusText = _statusTextObject.AddComponent<TextMesh>();
            _statusText.fontSize = 36;
            _statusText.characterSize = 0.012f;
            _statusText.anchor = TextAnchor.MiddleCenter;
            _statusText.color = Color.yellow;
            _statusText.text = "";
            _statusTextObject.SetActive(false);
        }

        private void ShowStatus(string msg)
        {
            if (_statusText == null) return;
            _statusText.text = msg;
            _statusTextObject.SetActive(true);
            // Position status at a fixed head-locked location
            _statusTextObject.transform.position = xrCamera.transform.position + xrCamera.transform.forward * 1.5f;
            _statusTextObject.transform.LookAt(xrCamera.transform);
            _statusTextObject.transform.Rotate(0, 180, 0);
        }

        private void HideStatus()
        {
            if (_statusTextObject != null)
                _statusTextObject.SetActive(false);
        }

        // ── Cleanup ──

        private void StopTracking()
        {
            _isTracking = false;
            _currentLabel = "";

            if (_bboxRenderer != null)
                _bboxRenderer.enabled = false;
            if (_labelText != null)
                _labelText.text = "";

            _ = StopTrackingAsync();
        }

        private async Task StopTrackingAsync()
        {
            try
            {
                using var http = new System.Net.Http.HttpClient { Timeout = TimeSpan.FromSeconds(5) };
                await http.PostAsync($"{backendBaseUrl}/api/track/stop", null);
            }
            catch { /* best effort */ }
        }
    }
}
