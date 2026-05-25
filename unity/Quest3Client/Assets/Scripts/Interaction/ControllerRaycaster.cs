using System;
using UnityEngine;

namespace SmartRoom.Interaction
{
    /// <summary>
    /// 从右手柄每帧发射射线。纯数据发射器，不依赖任何其他模块。
    /// 挂载：RightControllerAnchor (OVRCameraRig > TrackingSpace > RightControllerAnchor)
    /// </summary>
    public sealed class ControllerRaycaster : MonoBehaviour
    {
        [Header("References")]
        [SerializeField] private Transform controllerAnchor;
        [Tooltip("摄像机 Transform，用于知道用户看的方向（调试用）")]
        [SerializeField] private Transform headAnchor;

        [Header("Ray Settings")]
        [SerializeField] private float maxDistance = 10f;
        [SerializeField] private float throttleSeconds = 0f; // 0=每帧

        [Header("Debug")]
        [SerializeField] private Color lineColor = Color.green;
        [SerializeField] private bool drawDebugRay = true;

        public bool IsActive { get; private set; }
        public Ray CurrentRay { get; private set; }
        public float CurrentMaxDistance => maxDistance;

        public event Action<Ray> OnRayUpdated;
        public event Action OnRayDeactivated;

        private float _lastUpdateTime;

        private void Awake()
        {
            // controllerAnchor must be assigned via Inspector (drag RightControllerAnchor here).
            // If not assigned, try to find by name in scene.
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

            if (throttleSeconds > 0f)
            {
                if (Time.time - _lastUpdateTime < throttleSeconds) return;
            }

            _lastUpdateTime = Time.time;

            if (controllerAnchor == null) return;

            CurrentRay = new Ray(controllerAnchor.position, controllerAnchor.forward);
            OnRayUpdated?.Invoke(CurrentRay);
        }

        public Ray GetRay()
        {
            if (controllerAnchor == null) return default;
            return new Ray(controllerAnchor.position, controllerAnchor.forward);
        }

        private void OnDrawGizmos()
        {
            if (!drawDebugRay) return;
            if (controllerAnchor == null) return;

            var pos = controllerAnchor.position;
            var dir = controllerAnchor.forward;

            Gizmos.color = lineColor;
            Gizmos.DrawLine(pos, pos + dir * maxDistance);
            Gizmos.DrawWireSphere(pos + dir * maxDistance, 0.02f);
        }
    }
}
