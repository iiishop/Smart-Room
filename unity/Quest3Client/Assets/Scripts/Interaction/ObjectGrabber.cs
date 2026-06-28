using System;
using System.Threading.Tasks;
using SmartRoom.Networking;
using SmartRoom.Tracking;
using SmartRoom.UI;
using UnityEngine;

namespace SmartRoom.Interaction
{
    /// <summary>
    /// 编排层：监听右手柄扳机输入，串联 ControllerRaycaster → DepthCursor → PixelProjector，
    /// 在用户扣下扳机时：取 3D 命中点 → 投影到 PCA 像素 → 发送 point prompt JSON 给 Python 后端。
    /// 
    /// 挂载：独立 GameObject（ObjectGrabberRoot）
    /// 
    /// 输入：使用 OVRInput（Meta XR SDK），对应右手柄食指扳机。
    /// </summary>
    public sealed class ObjectGrabber : MonoBehaviour
    {
        [Header("References")]
        [SerializeField] private ControllerRaycaster controllerRaycaster;
        [SerializeField] private DepthCursor depthCursor;
        [SerializeField] private PixelProjector pixelProjector;
        [SerializeField] private BackendCommunicationManager backendManager;
        [SerializeField] private RgbStreamModule rgbStreamModule;
        [SerializeField] private TrackingManager trackingManager;

        [Header("Input")]
        [SerializeField] private float grabCooldownSeconds = 0.5f;

        [Header("Grab Settings")]
        [SerializeField] private float maxGrabDistance = 10f;

        // Runtime state
        public bool IsGrabbing { get; private set; }
        public Vector3 LastGrabWorldPoint { get; private set; }
        public Vector2Int LastGrabPixel { get; private set; }
        public bool LastGrabValid { get; private set; }

        public event Action<Vector3, Vector2Int> OnGrabStarted;
        public event Action<string> OnGrabFailed;

        private float _lastGrabTime;
        private bool _prevTriggerPressed;
        private bool _prevDeletePressed;
        private bool _deleteInFlight;
        private PromptPointMarkerManager.MarkerHandle _hoveredPromptPoint;

        private void Awake()
        {
            if (controllerRaycaster == null)
                controllerRaycaster = FindFirstObjectByType<ControllerRaycaster>();

            if (depthCursor == null)
                depthCursor = FindFirstObjectByType<DepthCursor>();

            if (pixelProjector == null)
                pixelProjector = FindFirstObjectByType<PixelProjector>();

            if (backendManager == null)
                backendManager = FindFirstObjectByType<BackendCommunicationManager>();

            if (rgbStreamModule == null)
                rgbStreamModule = FindFirstObjectByType<RgbStreamModule>();

            if (trackingManager == null)
                trackingManager = FindFirstObjectByType<TrackingManager>();
        }

        private void Update()
        {
            if (SmartRoom.UI.RoomCoordinateSystemPanel.IsUiBlockingSceneInput
                || SmartRoom.UI.DeviceArchivePanel.IsPanelVisible
                || SmartRoom.UI.DeviceBindingPanel.IsPanelVisible)
            {
                _prevTriggerPressed = OVRInput.Get(OVRInput.RawButton.RIndexTrigger);
                _prevDeletePressed = OVRInput.Get(OVRInput.Button.Two, OVRInput.Controller.RTouch);
                PromptPointMarkerManager.ClearHover();
                return;
            }

            bool triggerPressed = OVRInput.Get(OVRInput.RawButton.RIndexTrigger);
            bool triggerPressedDown = triggerPressed && !_prevTriggerPressed;
            Ray ray = controllerRaycaster != null ? controllerRaycaster.GetRay() : default(Ray);
            bool markerConsumed = SmartRoom.UI.DeviceSpatialMarkerManager.UpdateHoverAndConsumeTrigger(ray, triggerPressedDown);
            if (SmartRoom.UI.DeviceSpatialMarkerManager.IsHoveringMarker)
                PromptPointMarkerManager.ClearHover();
            else
                UpdatePromptPointHover();
            if (markerConsumed)
            {
                _prevTriggerPressed = triggerPressed;
                _prevDeletePressed = OVRInput.Get(OVRInput.Button.Two, OVRInput.Controller.RTouch);
                return;
            }

            bool deletePressed = OVRInput.Get(OVRInput.Button.Two, OVRInput.Controller.RTouch);
            if (deletePressed && !_prevDeletePressed)
                TryDeleteHoveredPromptPoint();
            _prevDeletePressed = deletePressed;

            if (Time.time - _lastGrabTime < grabCooldownSeconds) return;

            // Reuse the trigger state captured before marker interaction.
            if (triggerPressedDown)
            {
                TryGrab();
            }
            _prevTriggerPressed = triggerPressed;
        }

        private void UpdatePromptPointHover()
        {
            _hoveredPromptPoint = null;
            if (controllerRaycaster == null)
            {
                PromptPointMarkerManager.ClearHover();
                return;
            }

            Ray ray = controllerRaycaster.GetRay();
            PromptPointMarkerManager.TryUpdateHover(ray, out _hoveredPromptPoint);
        }

        private void TryDeleteHoveredPromptPoint()
        {
            if (_deleteInFlight || _hoveredPromptPoint == null || !_hoveredPromptPoint.IsValid)
                return;

            _ = DeleteHoveredPromptPointAsync(_hoveredPromptPoint);
        }

        private async Task DeleteHoveredPromptPointAsync(PromptPointMarkerManager.MarkerHandle marker)
        {
            _deleteInFlight = true;
            try
            {
                if (trackingManager == null)
                {
                    PromptPointMarkerManager.RemoveMarker(marker);
                    return;
                }

                trackingManager.ReportStatus("Deleting point...");
                bool deleted = await trackingManager.DeletePointPromptAsync(marker.WorldPoint);
                if (deleted)
                {
                    PromptPointMarkerManager.RemoveMarker(marker);
                    PromptPointMarkerManager.RemoveMarkersNear(marker.WorldPoint, 0.03f);
                }
            }
            finally
            {
                _deleteInFlight = false;
            }
        }

        public bool TryGrab()
        {
            if (trackingManager != null && trackingManager.IsBusy)
            {
                _lastGrabTime = Time.time;
                FailGrab("Still processing previous point");
                return false;
            }

            if (depthCursor == null)
            {
                FailGrab("DepthCursor not assigned");
                return false;
            }

            if (!depthCursor.IsHitting)
            {
                FailGrab("No depth surface hit — aim at a real surface");
                return false;
            }

            if (depthCursor.HitDistance > maxGrabDistance)
            {
                FailGrab($"Target too far ({depthCursor.HitDistance:F1}m > {maxGrabDistance}m max)");
                return false;
            }

            Vector3 hitPoint = depthCursor.GetHitPoint();

            if (pixelProjector == null)
            {
                FailGrab("PixelProjector not assigned");
                return false;
            }

            var pixel = pixelProjector.WorldToPixel(hitPoint);
            if (pixel == null)
            {
                // PCA frustum check failed — log and continue anyway.
                // TriggerDepthProbe only needs the world point; SAM pipeline is disabled.
                Debug.LogWarning($"[ObjectGrabber] WorldToPixel returned null for hitPoint=({hitPoint.x:F2},{hitPoint.y:F2},{hitPoint.z:F2}). " +
                                 "PCA camera frustum may be misaligned. Proceeding with depth probe only (no SAM prompt).");
                // Don't return — still fire OnGrabStarted for depth probe
            }

            _lastGrabTime = Time.time;
            LastGrabWorldPoint = hitPoint;
            LastGrabPixel = pixel ?? new Vector2Int(-1, -1);
            LastGrabValid = true;
            IsGrabbing = true;

            if (pixel != null)
            {
                Debug.Log($"[ObjectGrabber] GRAB at world=({hitPoint.x:F2},{hitPoint.y:F2},{hitPoint.z:F2}) " +
                          $"pixel=({pixel.Value.x},{pixel.Value.y})");
            }
            else
            {
                Debug.Log($"[ObjectGrabber] GRAB at world=({hitPoint.x:F2},{hitPoint.y:F2},{hitPoint.z:F2}) " +
                          "pixel=N/A (PCA frustum miss — depth probe only)");
            }

            OnGrabStarted?.Invoke(hitPoint, pixel ?? Vector2Int.zero);

            int label = depthCursor.CurrentMode == DepthCursor.ProbeEditMode.Add ? 1 : 0;
            string mode = label > 0 ? "add" : "del";
            int frameWidth = rgbStreamModule != null ? rgbStreamModule.LatestFrameWidth : 0;
            int frameHeight = rgbStreamModule != null ? rgbStreamModule.LatestFrameHeight : 0;
            PromptPointMarkerManager.MarkerHandle marker = PromptPointMarkerManager.AddMarker(hitPoint, label);

            if (trackingManager != null)
            {
                trackingManager.ReportStatus(label > 0 ? "Sending positive point..." : "Sending negative point...");
                _ = SendTrackedPointPromptAsync(
                    marker,
                    hitPoint,
                    pixel ?? new Vector2Int(-1, -1),
                    label,
                    mode,
                    frameWidth,
                    frameHeight);
            }
            else if (pixel != null)
            {
                SendPointPrompt(pixel.Value.x, pixel.Value.y, hitPoint, label, mode);
            }

            IsGrabbing = false;
            return true;
        }

        private async Task SendTrackedPointPromptAsync(
            PromptPointMarkerManager.MarkerHandle marker,
            Vector3 hitPoint,
            Vector2Int pixel,
            int label,
            string mode,
            int frameWidth,
            int frameHeight)
        {
            bool sent = await trackingManager.HandlePointPromptAsync(hitPoint, pixel, label, mode, frameWidth, frameHeight);
            if (!sent)
                PromptPointMarkerManager.RemoveMarker(marker);
        }

        private void SendPointPrompt(int px, int py, Vector3 hitPoint, int label, string mode)
        {
            if (backendManager == null)
            {
                Debug.LogWarning("[ObjectGrabber] No BackendCommunicationManager — can't send point prompt");
                return;
            }

            var payload = new PointPromptPayload
            {
                type = "point_prompt",
                x = px,
                y = py,
                label = label > 0 ? 1 : 0,
                mode = mode,
                is_hitting = true,
                hit_world_x = hitPoint.x,
                hit_world_y = hitPoint.y,
                hit_world_z = hitPoint.z,
                frame_width = rgbStreamModule != null ? rgbStreamModule.LatestFrameWidth : 640,
                frame_height = rgbStreamModule != null ? rgbStreamModule.LatestFrameHeight : 480,
                timestamp_ms = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                room_id = RoomCoordinateSystemPanel.CurrentRoomId,
                room_name = RoomCoordinateSystemPanel.CurrentRoomName,
                device_id = SystemInfo.deviceUniqueIdentifier,
                object_session_id = RoomObjectSession.CurrentObjectId,
                force_new_capture = RoomCaptureSession.ConsumeForceNextCapture(),
            };

            string json = JsonUtility.ToJson(payload);
            backendManager.QueueControlJson(json);
            Debug.Log($"[ObjectGrabber] Sent point prompt: x={px} y={py} label={payload.label}");
        }

        private void FailGrab(string reason)
        {
            LastGrabValid = false;
            Debug.Log($"[ObjectGrabber] Grab failed: {reason}");
            if (trackingManager != null)
                trackingManager.ReportStatus("Point not sent: " + reason);
            OnGrabFailed?.Invoke(reason);
        }

        public void CancelGrab()
        {
            IsGrabbing = false;
        }

        [Serializable]
        private sealed class PointPromptPayload
        {
            public string type;
            public int x;
            public int y;
            public int label;
            public string mode;
            public bool is_hitting;
            public float hit_world_x;
            public float hit_world_y;
            public float hit_world_z;
            public int frame_width;
            public int frame_height;
            public long timestamp_ms;
            public string room_id;
            public string room_name;
            public string device_id;
            public string object_session_id;
            public bool force_new_capture;
        }
    }
}
