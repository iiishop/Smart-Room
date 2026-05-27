using System;
using SmartRoom.Networking;
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
        }

        private void Update()
        {
            if (Time.time - _lastGrabTime < grabCooldownSeconds) return;

            // OVRInput: Meta XR SDK v85 扳机输入
            // OVRInput.RawButton.RIndexTrigger = 右手柄食指扳机
            bool triggerPressed = OVRInput.Get(OVRInput.RawButton.RIndexTrigger);
            if (triggerPressed && !_prevTriggerPressed)
            {
                TryGrab();
            }
            _prevTriggerPressed = triggerPressed;
        }

        public bool TryGrab()
        {
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

            if (pixel != null)
                SendPointPrompt(pixel.Value.x, pixel.Value.y);

            IsGrabbing = false;
            return true;
        }

        private void SendPointPrompt(int px, int py)
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
                label = 1,
                frame_width = rgbStreamModule != null ? rgbStreamModule.LatestFrameWidth : 640,
                frame_height = rgbStreamModule != null ? rgbStreamModule.LatestFrameHeight : 480,
                timestamp_ms = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()
            };

            string json = JsonUtility.ToJson(payload);
            backendManager.QueueControlJson(json);
            Debug.Log($"[ObjectGrabber] Sent point prompt: x={px} y={py}");
        }

        private void FailGrab(string reason)
        {
            LastGrabValid = false;
            Debug.Log($"[ObjectGrabber] Grab failed: {reason}");
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
            public int frame_width;
            public int frame_height;
            public long timestamp_ms;
        }
    }
}
