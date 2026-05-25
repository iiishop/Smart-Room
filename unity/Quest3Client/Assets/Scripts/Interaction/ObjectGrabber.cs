using System;
using SmartRoom.Networking;
using UnityEngine;
using UnityEngine.XR;

namespace SmartRoom.Interaction
{
    /// <summary>
    /// 编排层：监听扳机输入，串联 ControllerRaycaster → DepthCursor → PixelProjector，
    /// 在用户扣下扳机时：冻结当前 3D 点 → 投影到 2D 像素 → 发送 point prompt 给 Python SAM。
    /// 挂载：独立 GameObject（ObjectGrabberRoot）
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
        // Note: OnSegmentationComplete will be wired when Python backend supports point prompts.
        // For now, the message is sent and can be consumed via BackendCommunicationManager's events.

        private float _lastGrabTime;
        private InputDevice _rightController;
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

            _rightController = InputDevices.GetDeviceAtXRNode(XRNode.RightHand);
        }

        private void Update()
        {
            if (Time.time - _lastGrabTime < grabCooldownSeconds) return;

            _rightController.TryGetFeatureValue(CommonUsages.triggerButton, out bool triggerPressed);
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
                FailGrab("Target outside PCA camera frustum");
                return false;
            }

            _lastGrabTime = Time.time;
            LastGrabWorldPoint = hitPoint;
            LastGrabPixel = pixel.Value;
            LastGrabValid = true;
            IsGrabbing = true;

            // Debug log
            Debug.Log($"[ObjectGrabber] GRAB at world=({hitPoint.x:F2},{hitPoint.y:F2},{hitPoint.z:F2}) " +
                      $"pixel=({pixel.Value.x},{pixel.Value.y})");

            // Fire event for subscribers (e.g., UI feedback, sound)
            OnGrabStarted?.Invoke(hitPoint, pixel.Value);

            // Send point prompt to Python backend
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
                label = 1, // 1 = foreground (positive point for SAM)
                frame_width = rgbStreamModule != null ? rgbStreamModule.LatestFrameWidth : imageWidthHint,
                frame_height = rgbStreamModule != null ? rgbStreamModule.LatestFrameHeight : imageHeightHint,
                timestamp_ms = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()
            };

            string json = JsonUtility.ToJson(payload);
            backendManager.QueueControlJson(json);
            Debug.Log($"[ObjectGrabber] Sent point prompt: x={px} y={py}");
        }

        private int imageWidthHint = 640;
        private int imageHeightHint = 480;

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
