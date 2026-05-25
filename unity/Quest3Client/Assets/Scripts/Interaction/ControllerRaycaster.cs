using System;
using UnityEngine;

namespace SmartRoom.Interaction
{
    /// <summary>
    /// 从右手柄每帧发射射线。纯数据发射器。
    /// 
    /// 挂载：RightControllerAnchor (OVRCameraRig > TrackingSpace > RightControllerAnchor)
    /// 
    /// VR 可见射线：Inspector 中拖入 LineRenderer（必须，OnDrawGizmos 在 build 中不渲染）。
    /// 首次运行时会自动创建子 GO "RayLine" 并挂上 LineRenderer。
    /// </summary>
    public sealed class ControllerRaycaster : MonoBehaviour
    {
        [Header("References")]
        [SerializeField] private Transform controllerAnchor;
        [SerializeField] private Transform headAnchor;
        [SerializeField] private LineRenderer lineRenderer;

        [Header("Ray Settings")]
        [SerializeField] private float maxDistance = 10f;
        [SerializeField] private float throttleSeconds = 0f;

        [Header("Line Visuals")]
        [SerializeField] private Color lineColor = Color.green;
        [SerializeField] private float lineWidth = 0.002f;
        [SerializeField] private bool showLine = true;

        [Header("Debug (Editor Only)")]
        [SerializeField] private bool drawDebugRay = true;

        public bool IsActive { get; private set; }
        public Ray CurrentRay { get; private set; }
        public float CurrentMaxDistance => maxDistance;

        public event Action<Ray> OnRayUpdated;
        public event Action OnRayDeactivated;

        private float _lastUpdateTime;

        private void Awake()
        {
            if (controllerAnchor == null)
            {
                var found = GameObject.Find("RightControllerAnchor");
                if (found != null) controllerAnchor = found.transform;
            }

            if (headAnchor == null)
            {
                var mainCam = Camera.main;
                headAnchor = mainCam != null ? mainCam.transform : transform;
            }

            // Auto-create LineRenderer if not assigned
            if (lineRenderer == null)
            {
                var lineGo = new GameObject("RayLine");
                lineGo.transform.SetParent(transform);
                lineGo.transform.localPosition = Vector3.zero;
                lineGo.transform.localRotation = Quaternion.identity;
                lineRenderer = lineGo.AddComponent<LineRenderer>();
                lineRenderer.positionCount = 2;
                lineRenderer.startWidth = lineWidth;
                lineRenderer.endWidth = lineWidth;
                lineRenderer.material = new Material(Shader.Find("Universal Render Pipeline/Unlit"));
                lineRenderer.startColor = lineColor;
                lineRenderer.endColor = lineColor;
                lineRenderer.useWorldSpace = true;
                lineRenderer.enabled = false;
            }
        }

        private void Start()
        {
            Enable();
        }

        private void OnEnable()
        {
            Enable();
        }

        private void OnDisable()
        {
            Disable();
        }

        public void Enable()
        {
            IsActive = true;
        }

        public void Disable()
        {
            if (IsActive)
            {
                IsActive = false;
                OnRayDeactivated?.Invoke();
            }
        }

        private void Update()
        {
            if (!IsActive) return;
            if (throttleSeconds > 0f && Time.time - _lastUpdateTime < throttleSeconds) return;

            _lastUpdateTime = Time.time;
            if (controllerAnchor == null) return;

            CurrentRay = new Ray(controllerAnchor.position, controllerAnchor.forward);
            OnRayUpdated?.Invoke(CurrentRay);

            // Update LineRenderer for VR-visible ray
            if (showLine && lineRenderer != null)
            {
                lineRenderer.enabled = true;
                lineRenderer.SetPosition(0, controllerAnchor.position);
                lineRenderer.SetPosition(1, controllerAnchor.position + controllerAnchor.forward * maxDistance);
            }
            else if (lineRenderer != null)
            {
                lineRenderer.enabled = false;
            }
        }

        public Ray GetRay()
        {
            if (controllerAnchor == null) return default;
            return new Ray(controllerAnchor.position, controllerAnchor.forward);
        }

        private void OnDrawGizmos()
        {
            if (!drawDebugRay || controllerAnchor == null) return;
            var pos = controllerAnchor.position;
            var dir = controllerAnchor.forward;
            Gizmos.color = lineColor;
            Gizmos.DrawLine(pos, pos + dir * maxDistance);
            Gizmos.DrawWireSphere(pos + dir * maxDistance, 0.02f);
        }
    }
}
