using System;
using System.Net.Http;
using System.Text;
using System.Threading.Tasks;
using SmartRoom.Capture;
using SmartRoom.UI;
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
        [SerializeField] private string pointPath = "/api/room/point";
        [SerializeField] private string deletePointPath = "/api/room/point/delete";
        [SerializeField] private string objectBeginEditPath = "/api/room/object/begin_edit";
        [SerializeField] private string objectCompletePath = "/api/room/object/complete";
        [SerializeField] private string objectAbandonPath = "/api/room/object/abandon";
        [SerializeField] private string objectDeletePath = "/api/room/object/delete";
        [SerializeField] private string objectRenamePath = "/api/room/object/rename";
        [SerializeField] private float requestTimeoutSeconds = 300f;

        [Header("Input")]
        [SerializeField] private bool listenForTriggerInput = false;
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

        public bool IsBusy => _uploadInFlight;

        public string BuildViewerUrl(string path)
        {
            return BuildUrl(backendBaseUrl, path);
        }

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
            if (listenForTriggerInput)
            {
                bool triggerPressed = OVRInput.Get(OVRInput.RawButton.RIndexTrigger);
                if (triggerPressed && !_prevTriggerPressed)
                    OnTriggerPressed();
                _prevTriggerPressed = triggerPressed;
            }

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
                bool ok = await UploadCaptureAsync(capture, null);
                if (ok)
                    ShowStatus("RGB-D sent to viewer");
            }
            catch (Exception ex)
            {
                Debug.LogWarning($"[TrackingManager] RGB-D upload error: {ex}");
                ShowStatus("RGB-D upload error: " + BuildVisibleError(ex));
            }
            finally
            {
                _uploadInFlight = false;
            }
        }

        public async Task<bool> HandlePointPromptAsync(
            Vector3 worldPoint,
            Vector2Int pixel,
            int label,
            string mode,
            int frameWidth,
            int frameHeight)
        {
            if (_uploadInFlight)
                return false;

            _lastTriggerAt = Time.time;
            _uploadInFlight = true;

            try
            {
                string cursorJson = BuildCursorJson(worldPoint, pixel, label, mode, frameWidth, frameHeight);
                ShowStatus(label > 0 ? "Positive point: checking saved images..." : "Negative point: checking saved images...");

                if (await TrySendPointOnlyAsync(cursorJson))
                {
                    ShowStatus("Point sent to existing image");
                    return true;
                }

                ShowStatus("Capturing RGB-D...");
                if (finalRgbdCapture == null || !finalRgbdCapture.CaptureOnceToPayload(out var capture) || capture == null)
                {
                    Debug.LogWarning("[TrackingManager] RGB-D capture unavailable.");
                    ShowStatus("RGB-D capture unavailable");
                    return false;
                }

                ShowStatus("Uploading RGB-D...");
                bool ok = await UploadCaptureAsync(capture, cursorJson);
                if (ok)
                    ShowStatus("RGB-D point sent");
                return ok;
            }
            catch (Exception ex)
            {
                Debug.LogWarning($"[TrackingManager] Point prompt error: {ex}");
                ShowStatus("Point prompt error: " + BuildVisibleError(ex));
                return false;
            }
            finally
            {
                _uploadInFlight = false;
            }
        }

        public async Task<bool> DeletePointPromptAsync(Vector3 worldPoint)
        {
            if (_uploadInFlight)
                return false;

            _uploadInFlight = true;
            try
            {
                string deleteJson = BuildDeletePointJson(worldPoint);
                string url = BuildUrl(backendBaseUrl, deletePointPath);
                using var http = new HttpClient { Timeout = TimeSpan.FromSeconds(Mathf.Max(1f, requestTimeoutSeconds)) };
                using var content = new StringContent(deleteJson, Encoding.UTF8, "application/json");
                using HttpResponseMessage response = await http.PostAsync(url, content);
                string body = await response.Content.ReadAsStringAsync();
                if (!response.IsSuccessStatusCode)
                {
                    Debug.LogWarning($"[TrackingManager] Point delete failed ({response.StatusCode}): {body}");
                    ReportStatus($"Point delete failed {(int)response.StatusCode}: {ShortStatus(body)}");
                    return false;
                }

                PointDeleteResponse parsed = null;
                try
                {
                    parsed = JsonUtility.FromJson<PointDeleteResponse>(body);
                }
                catch
                {
                    // Keep the HTTP success as the source of truth if the body shape changes.
                }

                bool deleted = parsed == null || parsed.deleted;
                ReportStatus(deleted ? "Point deleted" : "No saved point near cursor");
                return deleted;
            }
            catch (Exception ex)
            {
                Debug.LogWarning($"[TrackingManager] Point delete error: {ex}");
                ReportStatus("Point delete error: " + BuildVisibleError(ex));
                return false;
            }
            finally
            {
                _uploadInFlight = false;
            }
        }

        public Task<ObjectActionResponse> BeginEditObjectAsync(string objectId)
        {
            return PostObjectActionAsync(objectBeginEditPath, objectId, string.Empty, string.Empty, "Opening saved device...");
        }

        public Task<ObjectActionResponse> CompleteObjectAsync(string objectId, string editSessionId)
        {
            return PostObjectActionAsync(objectCompletePath, objectId, editSessionId, string.Empty, "Saving device...");
        }

        public Task<ObjectActionResponse> AbandonObjectAsync(string objectId, string editSessionId)
        {
            return PostObjectActionAsync(objectAbandonPath, objectId, editSessionId, string.Empty, "Abandoning device...");
        }

        public async Task<bool> DeleteObjectAsync(string objectId)
        {
            ObjectActionResponse response = await PostObjectActionAsync(objectDeletePath, objectId, string.Empty, string.Empty, "Deleting device...");
            return response != null && response.ok;
        }

        public async Task<bool> RenameObjectAsync(string objectId, string name)
        {
            ObjectActionResponse response = await PostObjectActionAsync(objectRenamePath, objectId, string.Empty, name, "Renaming device...");
            return response != null && response.ok;
        }

        private async Task<ObjectActionResponse> PostObjectActionAsync(
            string path,
            string objectId,
            string editSessionId,
            string name,
            string status)
        {
            string cleanObjectId = string.IsNullOrWhiteSpace(objectId) ? RoomObjectSession.CurrentObjectId : objectId;
            if (string.IsNullOrWhiteSpace(cleanObjectId))
                return new ObjectActionResponse { ok = false, reason = "missing_object_id" };

            try
            {
                ReportStatus(status);
                string url = BuildUrl(backendBaseUrl, path);
                string json = BuildObjectActionJson(cleanObjectId, editSessionId, name);
                using var http = new HttpClient { Timeout = TimeSpan.FromSeconds(Mathf.Max(1f, requestTimeoutSeconds)) };
                using var content = new StringContent(json, Encoding.UTF8, "application/json");
                using HttpResponseMessage response = await http.PostAsync(url, content);
                string body = await response.Content.ReadAsStringAsync();
                if (!response.IsSuccessStatusCode)
                {
                    Debug.LogWarning($"[TrackingManager] Object action failed ({response.StatusCode}): {body}");
                    ReportStatus($"Device action failed {(int)response.StatusCode}: {ShortStatus(body)}");
                    return new ObjectActionResponse { ok = false, reason = body };
                }

                ObjectActionResponse parsed = null;
                try
                {
                    parsed = JsonUtility.FromJson<ObjectActionResponse>(body);
                }
                catch (Exception ex)
                {
                    Debug.LogWarning($"[TrackingManager] Object action JSON parse failed: {ex.Message} body={body}");
                }

                if (parsed == null)
                    parsed = new ObjectActionResponse { ok = true, object_id = cleanObjectId };
                if (parsed.ok)
                    ReportStatus("Device action complete");
                else
                    ReportStatus("Device action failed: " + ShortStatus(parsed.reason));
                return parsed;
            }
            catch (Exception ex)
            {
                Debug.LogWarning($"[TrackingManager] Object action error: {ex}");
                ReportStatus("Device action error: " + BuildVisibleError(ex));
                return new ObjectActionResponse { ok = false, reason = BuildVisibleError(ex) };
            }
        }

        private async Task<bool> TrySendPointOnlyAsync(string cursorJson)
        {
            if (string.IsNullOrWhiteSpace(cursorJson))
                return false;

            string url = BuildUrl(backendBaseUrl, pointPath);
            try
            {
                using var http = new HttpClient { Timeout = TimeSpan.FromSeconds(Mathf.Max(1f, requestTimeoutSeconds)) };
                using var content = new StringContent(cursorJson, Encoding.UTF8, "application/json");
                using HttpResponseMessage response = await http.PostAsync(url, content);
                string body = await response.Content.ReadAsStringAsync();
                if (!response.IsSuccessStatusCode)
                {
                    Debug.Log($"[TrackingManager] Point-only probe fell back to capture ({response.StatusCode}): {body}");
                    ReportStatus($"Saved-image check {(int)response.StatusCode}; capturing RGB-D...");
                    return false;
                }

                PointOnlyResponse parsed = null;
                try
                {
                    parsed = JsonUtility.FromJson<PointOnlyResponse>(body);
                }
                catch
                {
                    // Unknown response shape means we should capture a fresh frame.
                }

                bool usedExistingImage = parsed != null && parsed.ok && !parsed.needs_capture;
                if (!usedExistingImage)
                {
                    Debug.Log($"[TrackingManager] Point-only probe requested capture: {body}");
                    ReportStatus("No saved image hit; capturing RGB-D...");
                }
                return usedExistingImage;
            }
            catch (Exception ex)
            {
                Debug.LogWarning($"[TrackingManager] Point-only probe failed; falling back to capture. url={url} error={ex}");
                ReportStatus("Saved-image check failed: " + BuildVisibleError(ex));
                return false;
            }
        }

        private async Task<bool> UploadCaptureAsync(Quest3RgbdCaptureFinal.CapturePayload capture, string cursorJson)
        {
            if (capture.depthRawBytes == null || capture.depthRawBytes.Length == 0)
            {
                Debug.LogWarning("[TrackingManager] Capture has no depth_raw payload.");
                ReportStatus("RGB-D upload failed: no depth_raw");
                return false;
            }
            if (string.IsNullOrWhiteSpace(capture.metaJson))
            {
                Debug.LogWarning("[TrackingManager] Capture has no meta_json payload.");
                ReportStatus("RGB-D upload failed: no meta_json");
                return false;
            }

            bool canSendRaw = sendRgbRaw && capture.rgbRawBytes != null && capture.rgbRawBytes.Length > 0;
            bool canSendJpeg = capture.rgbJpegBytes != null && capture.rgbJpegBytes.Length > 0;
            if (!canSendRaw && !canSendJpeg)
            {
                Debug.LogWarning("[TrackingManager] Capture has no RGB payload.");
                ReportStatus("RGB-D upload failed: no RGB payload");
                return false;
            }

            string url = BuildUrl(backendBaseUrl, uploadPath);
            try
            {
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
                if (!string.IsNullOrWhiteSpace(cursorJson))
                    form.Add(new StringContent(cursorJson, Encoding.UTF8, "application/json"), "cursor_json");

                using HttpResponseMessage response = await http.PostAsync(url, form);
                string body = await response.Content.ReadAsStringAsync();
                if (!response.IsSuccessStatusCode)
                {
                    Debug.LogWarning($"[TrackingManager] Viewer upload failed ({response.StatusCode}): {body}");
                    ReportStatus($"RGB-D upload failed {(int)response.StatusCode}: {ShortStatus(body)}");
                    return false;
                }

                Debug.Log(
                    $"[TrackingManager] Uploaded RGB-D to viewer: rgb={capture.rgbWidth}x{capture.rgbHeight} " +
                    $"depth={capture.depthWidth}x{capture.depthHeight} rawRgb={canSendRaw} response={body}"
                );
                return true;
            }
            catch (Exception ex)
            {
                Debug.LogWarning($"[TrackingManager] Viewer upload exception. url={url} error={ex}");
                ReportStatus("RGB-D upload error: " + BuildVisibleError(ex));
                return false;
            }
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

        private string BuildVisibleError(Exception ex)
        {
            string message = ex == null ? string.Empty : ex.Message;
            if (ex?.InnerException != null && !string.IsNullOrWhiteSpace(ex.InnerException.Message))
                message = string.IsNullOrWhiteSpace(message)
                    ? ex.InnerException.Message
                    : message + " / " + ex.InnerException.Message;

            if (IsLocalhostBackendOnAndroid())
                return "Quest localhost needs adb reverse tcp:8500 tcp:8500 or PC IP";

            return ShortStatus(message);
        }

        private bool IsLocalhostBackendOnAndroid()
        {
#if UNITY_ANDROID && !UNITY_EDITOR
            string root = backendBaseUrl ?? string.Empty;
            return root.IndexOf("localhost", StringComparison.OrdinalIgnoreCase) >= 0 ||
                   root.IndexOf("127.0.0.1", StringComparison.OrdinalIgnoreCase) >= 0;
#else
            return false;
#endif
        }

        private static string ShortStatus(string message, int maxLength = 96)
        {
            if (string.IsNullOrWhiteSpace(message))
                return "unknown";

            string cleaned = message.Replace('\r', ' ').Replace('\n', ' ').Trim();
            while (cleaned.Contains("  ", StringComparison.Ordinal))
                cleaned = cleaned.Replace("  ", " ");

            if (cleaned.Length <= maxLength)
                return cleaned;
            return cleaned.Substring(0, Mathf.Max(0, maxLength - 3)) + "...";
        }

        private static string BuildCursorJson(
            Vector3 worldPoint,
            Vector2Int pixel,
            int label,
            string mode,
            int frameWidth,
            int frameHeight)
        {
            var payload = new CursorPointPayload
            {
                type = "room_point_prompt",
                is_hitting = true,
                hit_world_x = worldPoint.x,
                hit_world_y = worldPoint.y,
                hit_world_z = worldPoint.z,
                x = pixel.x,
                y = pixel.y,
                label = label > 0 ? 1 : 0,
                mode = string.IsNullOrWhiteSpace(mode) ? (label > 0 ? "add" : "del") : mode,
                frame_width = frameWidth > 0 ? frameWidth : 0,
                frame_height = frameHeight > 0 ? frameHeight : 0,
                timestamp_ms = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                room_id = RoomCoordinateSystemPanel.CurrentRoomId,
                room_name = RoomCoordinateSystemPanel.CurrentRoomName,
                device_id = SystemInfo.deviceUniqueIdentifier,
                device_name = SystemInfo.deviceName,
                device_model = SystemInfo.deviceModel,
                object_session_id = RoomObjectSession.CurrentObjectId,
                force_new_capture = RoomCaptureSession.ConsumeForceNextCapture(),
            };
            return JsonUtility.ToJson(payload);
        }

        private static string BuildDeletePointJson(Vector3 worldPoint)
        {
            var payload = new PointDeletePayload
            {
                type = "room_point_delete",
                hit_world_x = worldPoint.x,
                hit_world_y = worldPoint.y,
                hit_world_z = worldPoint.z,
                timestamp_ms = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                room_id = RoomCoordinateSystemPanel.CurrentRoomId,
                room_name = RoomCoordinateSystemPanel.CurrentRoomName,
                device_id = SystemInfo.deviceUniqueIdentifier,
                device_name = SystemInfo.deviceName,
                device_model = SystemInfo.deviceModel,
                object_session_id = RoomObjectSession.CurrentObjectId,
            };
            return JsonUtility.ToJson(payload);
        }

        private static string BuildObjectActionJson(string objectId, string editSessionId, string name)
        {
            var payload = new ObjectActionPayload
            {
                room_id = RoomCoordinateSystemPanel.CurrentRoomId,
                room_name = RoomCoordinateSystemPanel.CurrentRoomName,
                device_id = SystemInfo.deviceUniqueIdentifier,
                device_name = SystemInfo.deviceName,
                device_model = SystemInfo.deviceModel,
                object_id = objectId,
                object_session_id = objectId,
                edit_session_id = editSessionId ?? string.Empty,
                name = name ?? string.Empty,
                timestamp_ms = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
            };
            return JsonUtility.ToJson(payload);
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
            ReportStatus(message);
        }

        public void ReportStatus(string message)
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
            _hideStatusAt = Time.time + Mathf.Max(statusVisibleSeconds, 4f);
        }

        [Serializable]
        private sealed class CursorPointPayload
        {
            public string type;
            public bool is_hitting;
            public float hit_world_x;
            public float hit_world_y;
            public float hit_world_z;
            public int x;
            public int y;
            public int label;
            public string mode;
            public int frame_width;
            public int frame_height;
            public long timestamp_ms;
            public string room_id;
            public string room_name;
            public string device_id;
            public string device_name;
            public string device_model;
            public string object_session_id;
            public bool force_new_capture;
        }

        [Serializable]
        private sealed class PointDeletePayload
        {
            public string type;
            public float hit_world_x;
            public float hit_world_y;
            public float hit_world_z;
            public long timestamp_ms;
            public string room_id;
            public string room_name;
            public string device_id;
            public string device_name;
            public string device_model;
            public string object_session_id;
        }

        [Serializable]
        private sealed class PointOnlyResponse
        {
            public bool ok = false;
            public bool needs_capture = true;
            public string reason = string.Empty;
        }

        [Serializable]
        private sealed class PointDeleteResponse
        {
            public bool ok = false;
            public bool deleted = false;
            public string reason = string.Empty;
        }

        [Serializable]
        private sealed class ObjectActionPayload
        {
            public string room_id;
            public string room_name;
            public string device_id;
            public string device_name;
            public string device_model;
            public string object_id;
            public string object_session_id;
            public string edit_session_id;
            public string name;
            public long timestamp_ms;
        }

        [Serializable]
        public sealed class RoomObjectPointRecord
        {
            public string point_id = string.Empty;
            public int label = 1;
            public float[] world_xyz_m = Array.Empty<float>();
            public string image_id = string.Empty;
        }

        [Serializable]
        public sealed class ObjectActionResponse
        {
            public bool ok = false;
            public string reason = string.Empty;
            public string room_id = string.Empty;
            public string device_id = string.Empty;
            public string object_id = string.Empty;
            public string edit_session_id = string.Empty;
            public string name = string.Empty;
            public RoomObjectPointRecord[] points = Array.Empty<RoomObjectPointRecord>();
        }
    }
}
