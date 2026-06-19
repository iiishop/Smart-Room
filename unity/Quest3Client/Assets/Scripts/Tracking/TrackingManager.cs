using System;
using System.Collections.Generic;
using System.Net.Http;
using System.Text;
using System.Threading.Tasks;
using SmartRoom.Capture;
using SmartRoom.Interaction;
using UnityEngine;

namespace SmartRoom.Tracking
{
    /// <summary>
    /// Trigger-driven RGB-D upload path for the desktop alignment viewer.
    ///
    /// This deliberately does not run object detection, segmentation, labels, or
    /// bounding boxes. A trigger press captures one Quest 3 RGB-D bundle and
    /// posts it to the viewer on port 8500.
    /// </summary>
    public sealed class TrackingManager : MonoBehaviour
    {
        [Header("Capture")]
        [SerializeField] private Quest3RgbdCaptureFinal finalRgbdCapture;
        [SerializeField] private bool sendRgbRaw = true;
        [SerializeField] private bool includeJpegFallback = false;
        [SerializeField] private bool includeCursorPrompt = true;

        [Header("Cursor Prompt")]
        [SerializeField] private DepthCursor depthCursor;
        [SerializeField] private ControllerRaycaster controllerRaycaster;

        [Header("Viewer")]
        [SerializeField] private string backendBaseUrl = "http://127.0.0.1:8500";
        [SerializeField] private string uploadPath = "/api/track/start-final-rgbd";
        [SerializeField] private float requestTimeoutSeconds = 300f;

        [Header("VR Interactive Anchors")]
        [SerializeField] private bool enableAnchors = true;
        [SerializeField] private Material anchorPositiveMaterial;
        [SerializeField] private Material anchorNegativeMaterial;
        [SerializeField] private float anchorRadius = 0.025f;
        [SerializeField] private int maxAnchors = 16;

        [Header("Contour Rendering")]
        [SerializeField] private Material contourMaterial;
        [SerializeField] private float contourWidth = 0.005f;
        [SerializeField] private Color contourColor = Color.white;

        [Header("Input")]
        [SerializeField] private float triggerCooldownSeconds = 0.75f;

        [Header("Optional Headset Status")]
        [SerializeField] private bool showStatusText = true;
        [SerializeField] private Camera xrCamera;
        [SerializeField] private float statusDistanceMeters = 1.5f;
        [SerializeField] private float statusVisibleSeconds = 1.25f;

        private bool _prevTriggerPressed;
        private bool _uploadInFlight;
        private float _lastTriggerAt = -999f;
        private float _hideStatusAt = -1f;
        private GameObject _statusTextObject;
        private TextMesh _statusText;
        private readonly List<Vector3> _anchorPositions = new();
        private readonly List<int> _anchorLabels = new();
        private readonly List<GameObject> _anchorSpheres = new();
        private int _currentLabel = 1;
        private Quest3RgbdCaptureFinal.CapturePayload _lastCapturePayload;
        private LineRenderer _contourRenderer;
        private GameObject _contourObject;
        private GameObject _statusSphere;
        private float _bPressTime = -1f;

        private void Awake()
        {
            finalRgbdCapture ??= FindFirstObjectByType<Quest3RgbdCaptureFinal>();
            if (finalRgbdCapture == null)
                finalRgbdCapture = gameObject.AddComponent<Quest3RgbdCaptureFinal>();

            depthCursor ??= FindFirstObjectByType<DepthCursor>();
            controllerRaycaster ??= FindFirstObjectByType<ControllerRaycaster>();
            xrCamera ??= Camera.main;
        }

        private void Start()
        {
            if (showStatusText)
                CreateStatusText();
        }

        private void Update()
        {
            if (OVRInput.GetDown(OVRInput.RawButton.A))
            {
                _currentLabel = _currentLabel == 1 ? 0 : 1;
                Debug.Log($"[TrackingManager] Anchor mode: {(_currentLabel == 1 ? "positive (green)" : "negative (red)")}");
            }

            UpdateStatusSphere();

            if (OVRInput.GetDown(OVRInput.RawButton.RHandTrigger))
            {
                _lastTriggerAt = Time.time;
                _ = CaptureNewFrameAsync();
            }

            if (OVRInput.GetDown(OVRInput.RawButton.B))
                _bPressTime = Time.time;

            if (OVRInput.GetUp(OVRInput.RawButton.B))
            {
                float holdDuration = _bPressTime < 0f ? 0f : Time.time - _bPressTime;
                if (holdDuration < 0.5f)
                {
                    UndoLastAnchor();
                    if (_anchorPositions.Count > 0)
                        _ = RePredictAsync();
                }
                else
                {
                    ClearAnchors();
                    Debug.Log("[TrackingManager] All anchors cleared");
                }

                _bPressTime = -1f;
            }

            bool triggerPressed = OVRInput.Get(OVRInput.RawButton.RIndexTrigger);
            if (triggerPressed && !_prevTriggerPressed)
                OnTriggerPressed();
            _prevTriggerPressed = triggerPressed;

            if (_statusTextObject != null && _hideStatusAt > 0f && Time.time >= _hideStatusAt)
            {
                _statusTextObject.SetActive(false);
                _hideStatusAt = -1f;
            }
        }

        private async void OnTriggerPressed()
        {
            if (_uploadInFlight)
                return;
            if (Time.time - _lastTriggerAt < triggerCooldownSeconds)
                return;

            if (enableAnchors &&
                _lastCapturePayload != null &&
                depthCursor != null &&
                depthCursor.IsHitting &&
                _anchorPositions.Count < maxAnchors)
            {
                _lastTriggerAt = Time.time;
                PlaceAnchor(depthCursor.GetHitPoint(), _currentLabel);
                _ = RePredictAsync();
                return;
            }

            _lastTriggerAt = Time.time;
            _uploadInFlight = true;

            try
            {
                ShowStatus("Capturing RGB-D...");
                if (finalRgbdCapture == null || !finalRgbdCapture.CaptureOnceToPayload(out var capture) || capture == null)
                {
                    Debug.LogWarning("[TrackingManager] RGB-D capture unavailable.");
                    ShowStatus("RGB-D capture unavailable");
                    return;
                }

                _lastCapturePayload = capture;

                ShowStatus("Uploading RGB-D...");
                bool ok = await UploadAndSegmentAsync(capture, rePredict: false);
                ShowStatus(ok ? "RGB-D sent to viewer" : "RGB-D upload failed");
            }
            catch (Exception ex)
            {
                Debug.LogWarning($"[TrackingManager] RGB-D upload error: {ex.Message}");
                ShowStatus("RGB-D upload error");
            }
            finally
            {
                _uploadInFlight = false;
            }
        }

        private async Task CaptureNewFrameAsync()
        {
            if (_uploadInFlight)
                return;
            if (finalRgbdCapture == null)
            {
                ShowStatus("RGB-D capture unavailable");
                return;
            }

            _uploadInFlight = true;

            try
            {
                ShowStatus("Capturing RGB-D...");
                if (!finalRgbdCapture.CaptureOnceToPayload(out var capture) || capture == null)
                {
                    Debug.LogWarning("[TrackingManager] RGB-D capture unavailable.");
                    ShowStatus("RGB-D capture unavailable");
                    return;
                }

                _lastCapturePayload = capture;
                ShowStatus("Uploading RGB-D...");
                bool ok = await UploadAndSegmentAsync(capture, rePredict: enableAnchors && _anchorPositions.Count > 0);
                ShowStatus(ok ? "RGB-D sent to viewer" : "RGB-D upload failed");
            }
            catch (Exception ex)
            {
                Debug.LogWarning($"[TrackingManager] Re-capture error: {ex.Message}");
                ShowStatus("RGB-D upload error");
            }
            finally
            {
                _uploadInFlight = false;
            }
        }

        private async Task<bool> UploadAndSegmentAsync(Quest3RgbdCaptureFinal.CapturePayload capture, bool rePredict)
        {
            if (capture.depthRawBytes == null || capture.depthRawBytes.Length == 0)
            {
                Debug.LogWarning("[TrackingManager] Capture has no depth_raw payload.");
                return false;
            }
            if (string.IsNullOrWhiteSpace(capture.metaJson))
            {
                Debug.LogWarning("[TrackingManager] Capture has no meta_json payload.");
                return false;
            }

            bool canSendRaw = sendRgbRaw && capture.rgbRawBytes != null && capture.rgbRawBytes.Length > 0;
            bool canSendJpeg = capture.rgbJpegBytes != null && capture.rgbJpegBytes.Length > 0;
            if (!canSendRaw && !canSendJpeg)
            {
                Debug.LogWarning("[TrackingManager] Capture has no RGB payload.");
                return false;
            }

            using var http = new HttpClient { Timeout = TimeSpan.FromSeconds(Mathf.Max(1f, requestTimeoutSeconds)) };
            using var form = new MultipartFormDataContent();

            if (canSendRaw)
                AddBinaryPart(form, "rgb_raw", "rgb.raw", capture.rgbRawBytes, "application/octet-stream");
            else
                AddBinaryPart(form, "rgb_jpeg", "rgb.jpg", capture.rgbJpegBytes, "image/jpeg");

            if (includeJpegFallback && canSendRaw && canSendJpeg)
                AddBinaryPart(form, "rgb_jpeg", "rgb.jpg", capture.rgbJpegBytes, "image/jpeg");

            AddBinaryPart(form, "depth_raw", "depth.raw", capture.depthRawBytes, "application/octet-stream");
            form.Add(new StringContent(capture.metaJson, Encoding.UTF8, "application/json"), "meta_json");
            string cursorJson = BuildCursorJson();
            if (includeCursorPrompt && !string.IsNullOrWhiteSpace(cursorJson))
                form.Add(new StringContent(cursorJson, Encoding.UTF8, "application/json"), "cursor_json");
            if (enableAnchors && _anchorPositions.Count > 0)
            {
                string anchorsJson = BuildAnchorsJson();
                form.Add(new StringContent(anchorsJson, Encoding.UTF8, "application/json"), "anchor_points_json");
            }
            if (rePredict)
                form.Add(new StringContent("true", Encoding.UTF8, "text/plain"), "re_predict");

            string url = BuildUrl(backendBaseUrl, uploadPath);
            using HttpResponseMessage response = await http.PostAsync(url, form);
            string body = await response.Content.ReadAsStringAsync();
            if (!response.IsSuccessStatusCode)
            {
                Debug.LogWarning($"[TrackingManager] Viewer upload failed ({response.StatusCode}): {body}");
                return false;
            }

            Debug.Log(
                $"[TrackingManager] Uploaded RGB-D to viewer: rgb={capture.rgbWidth}x{capture.rgbHeight} " +
                $"depth={capture.depthWidth}x{capture.depthHeight} rawRgb={canSendRaw} response={body}"
            );
            HandleSegmentationResponse(body);
            return true;
        }

        private void UpdateStatusSphere()
        {
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
                Renderer renderer = _statusSphere.GetComponent<Renderer>();
                Material material = _currentLabel == 1 ? anchorPositiveMaterial : anchorNegativeMaterial;
                if (material != null)
                    renderer.material = material;
                else
                    renderer.material.color = _currentLabel == 1 ? Color.green : Color.red;
                _statusSphere.SetActive(true);
            }
            else if (_statusSphere.activeSelf)
            {
                _statusSphere.SetActive(false);
            }
        }

        private string BuildAnchorsJson()
        {
            var sb = new StringBuilder();
            sb.Append("[");
            for (int i = 0; i < _anchorPositions.Count; i++)
            {
                if (i > 0)
                    sb.Append(",");

                Vector3 p = _anchorPositions[i];
                sb.Append($"{{\"x\":{p.x:F4},\"y\":{p.y:F4},\"z\":{p.z:F4},\"label\":{_anchorLabels[i]}}}");
            }

            sb.Append("]");
            return sb.ToString();
        }

        private string BuildCursorJson()
        {
            var ray = controllerRaycaster != null ? controllerRaycaster.GetRay() : default;
            bool hasRay = controllerRaycaster != null && controllerRaycaster.IsActive;
            bool isHitting = depthCursor != null && depthCursor.IsHitting;
            Vector3 hitPoint = isHitting ? depthCursor.GetHitPoint() : Vector3.zero;
            Vector3 hitNormal = isHitting ? depthCursor.HitNormal : Vector3.zero;
            float hitDistance = isHitting ? depthCursor.HitDistance : 0f;

            var payload = new CursorPromptPayload
            {
                is_hitting = isHitting,
                has_ray = hasRay,
                unity_frame = Time.frameCount,
                hit_world_x = hitPoint.x,
                hit_world_y = hitPoint.y,
                hit_world_z = hitPoint.z,
                hit_normal_x = hitNormal.x,
                hit_normal_y = hitNormal.y,
                hit_normal_z = hitNormal.z,
                hit_distance_m = hitDistance,
                ray_origin_x = ray.origin.x,
                ray_origin_y = ray.origin.y,
                ray_origin_z = ray.origin.z,
                ray_direction_x = ray.direction.x,
                ray_direction_y = ray.direction.y,
                ray_direction_z = ray.direction.z,
            };
            return JsonUtility.ToJson(payload, false);
        }

        private static void AddBinaryPart(
            MultipartFormDataContent form,
            string name,
            string fileName,
            byte[] data,
            string mediaType)
        {
            var content = new ByteArrayContent(data);
            content.Headers.ContentType = new System.Net.Http.Headers.MediaTypeHeaderValue(mediaType);
            form.Add(content, name, fileName);
        }

        private static string BuildUrl(string baseUrl, string path)
        {
            string root = string.IsNullOrWhiteSpace(baseUrl) ? "http://127.0.0.1:8500" : baseUrl.TrimEnd('/');
            string suffix = string.IsNullOrWhiteSpace(path) ? "/api/track/start-final-rgbd" : path;
            if (!suffix.StartsWith("/", StringComparison.Ordinal))
                suffix = "/" + suffix;
            return root + suffix;
        }

        private void PlaceAnchor(Vector3 worldPos, int label)
        {
            _anchorPositions.Add(worldPos);
            _anchorLabels.Add(label);

            Material mat = label == 1 ? anchorPositiveMaterial : anchorNegativeMaterial;
            if (mat == null)
            {
                mat = new Material(Shader.Find("Universal Render Pipeline/Unlit"));
                mat.color = label == 1 ? Color.green : Color.red;
            }

            GameObject sphere = GameObject.CreatePrimitive(PrimitiveType.Sphere);
            sphere.transform.position = worldPos;
            sphere.transform.localScale = Vector3.one * (anchorRadius * 2f);
            sphere.GetComponent<Renderer>().material = mat;
            Destroy(sphere.GetComponent<Collider>());
            sphere.name = $"Anchor_{label}_{_anchorPositions.Count}";
            _anchorSpheres.Add(sphere);

            Debug.Log($"[TrackingManager] Placed {(label == 1 ? "positive" : "negative")} anchor at {worldPos}");
        }

        private void ClearAnchors()
        {
            foreach (GameObject sphere in _anchorSpheres)
            {
                if (sphere != null)
                    Destroy(sphere);
            }

            _anchorSpheres.Clear();
            _anchorPositions.Clear();
            _anchorLabels.Clear();
            ClearContour();
        }

        private void UndoLastAnchor()
        {
            if (_anchorPositions.Count == 0)
                return;

            int idx = _anchorPositions.Count - 1;
            if (_anchorSpheres[idx] != null)
                Destroy(_anchorSpheres[idx]);
            _anchorSpheres.RemoveAt(idx);
            _anchorPositions.RemoveAt(idx);
            _anchorLabels.RemoveAt(idx);

            if (_anchorPositions.Count == 0)
                ClearContour();

            Debug.Log($"[TrackingManager] Removed last anchor, {_anchorPositions.Count} remaining");
        }

        private async Task RePredictAsync()
        {
            if (_anchorPositions.Count == 0 || _lastCapturePayload == null)
                return;

            try
            {
                await UploadAndSegmentAsync(_lastCapturePayload, rePredict: true);
            }
            catch (Exception ex)
            {
                Debug.LogWarning($"[TrackingManager] Re-predict error: {ex.Message}");
            }
        }

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

        private void RenderContour(ContourPoint[] points)
        {
            if (points.Length < 3)
            {
                ClearContour();
                return;
            }

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
                _contourRenderer.useWorldSpace = true;
            }

            _contourRenderer.positionCount = points.Length;
            for (int i = 0; i < points.Length; i++)
                _contourRenderer.SetPosition(i, new Vector3(points[i].x, points[i].y, points[i].z));

            Debug.Log($"[TrackingManager] Contour rendered: {points.Length} points");
        }

        private void ClearContour()
        {
            if (_contourRenderer != null)
                _contourRenderer.positionCount = 0;
        }

        private void CreateStatusText()
        {
            if (_statusTextObject != null)
                return;

            _statusTextObject = new GameObject("RgbdUploadStatus");
            _statusTextObject.transform.SetParent(transform);
            _statusText = _statusTextObject.AddComponent<TextMesh>();
            _statusText.fontSize = 36;
            _statusText.characterSize = 0.012f;
            _statusText.anchor = TextAnchor.MiddleCenter;
            _statusText.color = Color.cyan;
            _statusText.text = "";
            _statusTextObject.SetActive(false);
        }

        private void ShowStatus(string message)
        {
            Debug.Log($"[TrackingManager] {message}");
            if (!showStatusText || _statusText == null || _statusTextObject == null || xrCamera == null)
                return;

            _statusText.text = message;
            _statusTextObject.SetActive(true);
            _statusTextObject.transform.position =
                xrCamera.transform.position + xrCamera.transform.forward * statusDistanceMeters;
            _statusTextObject.transform.LookAt(xrCamera.transform);
            _statusTextObject.transform.Rotate(0f, 180f, 0f);
            _hideStatusAt = Time.time + statusVisibleSeconds;
        }

        [Serializable]
        private sealed class CursorPromptPayload
        {
            public bool is_hitting;
            public bool has_ray;
            public int unity_frame;
            public float hit_world_x, hit_world_y, hit_world_z;
            public float hit_normal_x, hit_normal_y, hit_normal_z;
            public float hit_distance_m;
            public float ray_origin_x, ray_origin_y, ray_origin_z;
            public float ray_direction_x, ray_direction_y, ray_direction_z;
        }

        [Serializable]
        private sealed class ViewerResponse
        {
            public DeviceInfo device;
        }

        [Serializable]
        private sealed class DeviceInfo
        {
            public ContourPoint[] contour_3d;
            public bool segmented;
        }

        [Serializable]
        private sealed class ContourPoint
        {
            public float x;
            public float y;
            public float z;
        }
    }
}
