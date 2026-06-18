using System;
using System.Net.Http;
using System.Text;
using System.Threading.Tasks;
using SmartRoom.Capture;
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

        [Header("Viewer")]
        [SerializeField] private string backendBaseUrl = "http://127.0.0.1:8500";
        [SerializeField] private string uploadPath = "/api/track/start-final-rgbd";
        [SerializeField] private float requestTimeoutSeconds = 300f;

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

        private void Awake()
        {
            finalRgbdCapture ??= FindFirstObjectByType<Quest3RgbdCaptureFinal>();
            if (finalRgbdCapture == null)
                finalRgbdCapture = gameObject.AddComponent<Quest3RgbdCaptureFinal>();

            xrCamera ??= Camera.main;
        }

        private void Start()
        {
            if (showStatusText)
                CreateStatusText();
        }

        private void Update()
        {
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

                ShowStatus("Uploading RGB-D...");
                bool ok = await UploadCaptureAsync(capture);
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

        private async Task<bool> UploadCaptureAsync(Quest3RgbdCaptureFinal.CapturePayload capture)
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
            return true;
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
    }
}
