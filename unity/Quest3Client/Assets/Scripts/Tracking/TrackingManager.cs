using System;
using System.Text;
using System.Threading.Tasks;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace SmartRoom.Tracking
{
    /// <summary>
    /// Single-shot object detection + 3D anchoring for Quest 3.
    ///
    /// Flow:
    ///   1. User aims green depth cursor at object
    ///   2. Presses trigger → WorldToPixel → POST /api/track/start
    ///   3. Backend returns {label, bbox, center_pixel} (one-shot, no streaming)
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
        [Header("References")]
        [SerializeField] private Interaction.DepthCursor depthCursor;
        [SerializeField] private Interaction.ControllerRaycaster controllerRaycaster;
        [SerializeField] private Networking.PixelProjector pixelProjector;
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
            if (pixelProjector != null && pixelProjector.IsReady
                && pixelProjector.CameraAccess != null
                && pixelProjector.CameraAccess.IsPlaying)
            {
                var pca = pixelProjector.CameraAccess;
                Vector2 viewport = pca.WorldToViewportPoint(hitPoint);
                // Meta viewport: (0,0)=bottom-left, (1,1)=top-right.
                // Our stream JPEG: Y=0 at top.  Flip Y.
                float streamPx = viewport.x * pixelProjector.ImageWidth;
                float streamPy = (1f - viewport.y) * pixelProjector.ImageHeight;
                pixel = new Vector2(
                    Mathf.Clamp(streamPx, 0, pixelProjector.ImageWidth - 1),
                    Mathf.Clamp(streamPy, 0, pixelProjector.ImageHeight - 1)
                );
            }
            else if (pixelProjector != null && pixelProjector.IsReady)
            {
                // Fallback: manual PCA intrinsics projection
                var projPixel = pixelProjector.WorldToPixel(hitPoint);
                pixel = projPixel ?? WorldToScreenPoint(hitPoint);
            }
            else
            {
                pixel = WorldToScreenPoint(hitPoint);
            }

            Debug.Log($"[TrackingManager] Trigger at world={hitPoint}, pixel=({pixel.x:F0},{pixel.y:F0})");

            ShowStatus("Detecting...");
            await DetectAsync(pixel);
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

        private float[] BuildRgbCameraPose()
        {
            // RGB camera world pose from PassthroughCameraAccess.GetCameraPose().
            // Sends position (x,y,z) + rotation quaternion (x,y,z,w) as 7 floats.
            // Reference: https://developers.meta.com/horizon/documentation/unity/unity-pca-documentation/
            if (pixelProjector == null || !pixelProjector.IsReady
                || pixelProjector.CameraAccess == null
                || !pixelProjector.CameraAccess.IsPlaying)
                return null;

            var pose = pixelProjector.CameraAccess.GetCameraPose();
            return new float[] {
                pose.position.x, pose.position.y, pose.position.z,
                pose.rotation.x, pose.rotation.y, pose.rotation.z, pose.rotation.w,
            };
        }

        private float[] GetDepthReprojMatrix()
        {
            // _EnvironmentDepthReprojectionMatrices is a global shader
            // array set by the Meta Depth API.  Index 0 = left eye.
            var mat = Shader.GetGlobalMatrix("_EnvironmentDepthReprojectionMatrices");
            // Unity Matrix4x4 is column-major; flatten to row-major for Python.
            return new float[] {
                mat.m00, mat.m01, mat.m02, mat.m03,
                mat.m10, mat.m11, mat.m12, mat.m13,
                mat.m20, mat.m21, mat.m22, mat.m23,
                mat.m30, mat.m31, mat.m32, mat.m33,
            };
        }

        private async Task DetectAsync(Vector2 pixel)
        {
            try
            {
                var payload = JsonConvert.SerializeObject(new
                {
                    pixel_x = pixel.x,
                    pixel_y = pixel.y,
                    rgb_intrinsics = BuildRgbIntrinsics(),
                    rgb_pose = BuildRgbCameraPose(),
                    depth_reproj = GetDepthReprojMatrix(),
                });

                using var http = new System.Net.Http.HttpClient { Timeout = TimeSpan.FromSeconds(15) };
                var content = new System.Net.Http.StringContent(payload, Encoding.UTF8, "application/json");
                var response = await http.PostAsync($"{backendBaseUrl}/api/track/start", content);

                if (!response.IsSuccessStatusCode)
                {
                    string errBody = await response.Content.ReadAsStringAsync();
                    Debug.LogError($"[TrackingManager] Detect failed ({response.StatusCode}): {errBody}");
                    ShowStatus($"Error: {response.StatusCode}");
                    return;
                }

                var responseJson = await response.Content.ReadAsStringAsync();
                var result = JObject.Parse(responseJson);

                if (result["ok"]?.Value<bool>() == true)
                {
                    var trackResult = result["result"];
                    _currentLabel = trackResult["label"]?.Value<string>() ?? "object";
                    _isTracking = true;

                    // Update bbox size from detection result
                    var box = trackResult["box_xyxy"];
                    if (box != null && box.HasValues)
                    {
                        float bw = box[2]?.Value<float>() ?? 100 - (box[0]?.Value<float>() ?? 0);
                        float bh = box[3]?.Value<float>() ?? 100 - (box[1]?.Value<float>() ?? 0);
                        // Convert pixel bbox to rough world size based on depth distance
                        float dist = depthCursor.HitDistance;
                        float fovScale = dist / 500f; // rough: 500px ~= 1m at 1m distance
                        _bboxWorldHalfSize = Mathf.Max(bw, bh) * fovScale * 0.5f;
                        _bboxWorldHalfSize = Mathf.Clamp(_bboxWorldHalfSize, 0.05f, 1.0f);
                    }

                    HideStatus();
                    Debug.Log($"[TrackingManager] Detected: {_currentLabel}");
                }
                else
                {
                    ShowStatus("Detection returned no result");
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
