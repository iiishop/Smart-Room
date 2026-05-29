using Meta.XR;
using UnityEngine;

namespace SmartRoom.Networking
{
    /// <summary>
    /// 世界坐标 ↔ PCA 像素坐标的双向投影。
    /// 用 PassthroughCameraAccess 的 Intrinsics 做精确投影，
    /// 相机位姿从 ViewportPointToRay 推导（不依赖 PCA.transform，因为
    /// PCA GameObject 可能与 Camera Rig 平级，Transform 不跟踪头显）。
    /// 挂载：跟 RgbStreamModule 同一 GameObject
    /// </summary>
    public sealed class PixelProjector : MonoBehaviour
    {
        [Header("References")]
        [SerializeField] private PassthroughCameraAccess passthroughCameraAccess;
        [SerializeField] private RgbStreamModule rgbStreamModule;

        [Header("Image Dimensions (auto-synced from RgbStreamModule)")]
        [SerializeField] private bool autoSyncDimensions = true;
        [SerializeField] private int imageWidth = 640;
        [SerializeField] private int imageHeight = 480;

        // Intrinsics
        public Vector2 FocalPixels { get; private set; }    // fx, fy
        public Vector2 PrincipalPoint { get; private set; }  // cx, cy
        public int ImageWidth => imageWidth;
        public int ImageHeight => imageHeight;
        public bool IsReady => _initialized;
        public PassthroughCameraAccess CameraAccess => passthroughCameraAccess;

        private bool _initialized;

        // PCA native intrinsics (before scaling)
        private float _nativeWidth;
        private float _nativeHeight;

        // Camera pose derived from ViewportPointToRay (NOT from PCA.transform)
        private Vector3 _camPos;
        private Vector3 _camForward;
        private Vector3 _camRight;
        private Vector3 _camUp;
        private int _lastPoseFrame;

        private void Awake()
        {
            if (passthroughCameraAccess == null)
                passthroughCameraAccess = FindFirstObjectByType<PassthroughCameraAccess>();
            if (rgbStreamModule == null)
                rgbStreamModule = FindFirstObjectByType<RgbStreamModule>();

            if (autoSyncDimensions && rgbStreamModule != null)
            {
                imageWidth = rgbStreamModule.LatestFrameWidth;
                imageHeight = rgbStreamModule.LatestFrameHeight;
                // If stream hasn't started yet, LatestFrameWidth/Height may be 0
                if (imageWidth <= 0) imageWidth = 640;
                if (imageHeight <= 0) imageHeight = 360;
            }

            Initialize();
        }

        private void Initialize()
        {
            if (passthroughCameraAccess == null)
            {
                Debug.LogWarning("[PixelProjector] PassthroughCameraAccess not found");
                return;
            }

            try
            {
                var intrinsics = passthroughCameraAccess.Intrinsics;
                // PCA intrinsics are at native passthrough resolution (~1280x1280).
                // Estimate native dimensions from principal point (cx ≈ nativeW/2, cy ≈ nativeH/2).
                _nativeWidth = intrinsics.PrincipalPoint.x * 2f;
                _nativeHeight = intrinsics.PrincipalPoint.y * 2f;

                float scaleX = imageWidth / _nativeWidth;
                float scaleY = imageHeight / _nativeHeight;

                FocalPixels = new Vector2(intrinsics.FocalLength.x * scaleX, intrinsics.FocalLength.y * scaleY);
                PrincipalPoint = new Vector2(intrinsics.PrincipalPoint.x * scaleX, intrinsics.PrincipalPoint.y * scaleY);
                _initialized = true;
                Debug.Log($"[PixelProjector] Ready: fx={FocalPixels.x:F1} fy={FocalPixels.y:F1} " +
                          $"cx={PrincipalPoint.x:F1} cy={PrincipalPoint.y:F1} " +
                          $"(native={_nativeWidth:F0}x{_nativeHeight:F0}, target={imageWidth}x{imageHeight})");
            }
            catch (System.Exception ex)
            {
                // Fallback defaults for Quest 3 (assume 640x480 native)
                FocalPixels = new Vector2(640f, 640f);
                PrincipalPoint = new Vector2(320f, 240f);
                _nativeWidth = 640f;
                _nativeHeight = 480f;
                _initialized = true;
                Debug.LogWarning($"[PixelProjector] PCA Intrinsics unavailable, using defaults: {ex.Message}");
            }
        }

        /// <summary>
        /// 从 ViewportPointToRay 推导 PCA 相机位姿。同帧内缓存。
        /// ViewportPointToRay 内部用 Meta SDK 的正确外参，不依赖 PCA.transform。
        /// </summary>
        private bool TryRefreshPcaPose()
        {
            if (passthroughCameraAccess == null || !passthroughCameraAccess.enabled || !passthroughCameraAccess.IsPlaying)
                return false;

            int frame = Time.frameCount;
            if (_lastPoseFrame == frame) return true; // already fresh this frame

            // All ViewportPointToRay calls share the same origin (= camera world position)
            var centerRay = passthroughCameraAccess.ViewportPointToRay(new Vector2(0.5f, 0.5f));
            _camPos = centerRay.origin;
            _camForward = centerRay.direction.normalized;

            // Derive right axis from right-edge ray (1.0, 0.5)
            var rightRay = passthroughCameraAccess.ViewportPointToRay(new Vector2(1.0f, 0.5f));
            _camRight = (rightRay.direction - _camForward * Vector3.Dot(rightRay.direction, _camForward)).normalized;

            // Derive up axis from top-edge ray (0.5, 1.0), orthogonalize against forward + right
            var upRay = passthroughCameraAccess.ViewportPointToRay(new Vector2(0.5f, 1.0f));
            _camUp = upRay.direction - _camForward * Vector3.Dot(upRay.direction, _camForward);
            _camUp = (_camUp - _camRight * Vector3.Dot(_camUp, _camRight)).normalized;

            _lastPoseFrame = frame;
            return true;
        }

        /// <summary>
        /// 世界坐标 → PCA 像素坐标。
        /// 返回 null 表示世界点在 PCA 视野外。
        /// </summary>
        public Vector2Int? WorldToPixel(Vector3 worldPoint)
        {
            if (!_initialized)
            {
                Debug.LogWarning("[PixelProjector] WorldToPixel failed: not initialized");
                return null;
            }
            if (!TryRefreshPcaPose())
            {
                Debug.LogWarning("[PixelProjector] WorldToPixel failed: PCA not ready (not playing or disabled)");
                return null;
            }

            // Transform world → camera-local via dot products (no Transform dependency)
            Vector3 delta = worldPoint - _camPos;
            float localZ = Vector3.Dot(delta, _camForward);
            if (localZ <= 0f)
            {
                // Behind camera
                Debug.LogWarning($"[PixelProjector] WorldToPixel: point behind camera. worldPt=({worldPoint.x:F2},{worldPoint.y:F2},{worldPoint.z:F2}) _camPos=({_camPos.x:F2},{_camPos.y:F2},{_camPos.z:F2}) _camForward=({_camForward.x:F2},{_camForward.y:F2},{_camForward.z:F2}) localZ={localZ:F2}");
                return null;
            }

            float localX = Vector3.Dot(delta, _camRight);
            float localY = Vector3.Dot(delta, _camUp);

            float xNorm = localX / localZ;
            float yNorm = localY / localZ;

            // Convert to pixel coordinates
            float px = xNorm * FocalPixels.x + PrincipalPoint.x;
            float py = yNorm * FocalPixels.y + PrincipalPoint.y;

            int pxInt = Mathf.RoundToInt(px);
            int pyInt = Mathf.RoundToInt(py);

            // Check bounds
            if (pxInt < 0 || pxInt >= imageWidth || pyInt < 0 || pyInt >= imageHeight)
            {
                Debug.LogWarning($"[PixelProjector] WorldToPixel: pixel out of bounds. ({pxInt},{pyInt}) — image={imageWidth}x{imageHeight} localZ={localZ:F2} local=({localX:F2},{localY:F2})");
                return null;
            }

            return new Vector2Int(pxInt, pyInt);
        }

        /// <summary>
        /// PCA 像素 + 深度 → 世界坐标。
        /// 直接用 ViewportPointToRay（正确外参），不依赖 PCA.transform。
        /// </summary>
        public Vector3? PixelToWorld(Vector2Int pixel, float depthMeters)
        {
            if (!_initialized) return null;
            if (depthMeters <= 0f) return null;

            float u = (float)pixel.x / imageWidth;
            float v = (float)pixel.y / imageHeight;

            return ViewportToWorld(u, v, depthMeters);
        }

        /// <summary>
        /// 判断世界点是否在 PCA 视锥内（不对投影做裁剪，仅粗判）
        /// </summary>
        public bool IsInFrustum(Vector3 worldPoint)
        {
            return WorldToPixel(worldPoint).HasValue;
        }

        /// <summary>
        /// 通过 UV → 射线 + 深度 计算世界点（给点云用）
        /// </summary>
        public Vector3? ViewportToWorld(float u, float v, float depthMeters)
        {
            if (!_initialized || passthroughCameraAccess == null) return null;
            if (!passthroughCameraAccess.enabled || !passthroughCameraAccess.IsPlaying) return null;

            var ray = passthroughCameraAccess.ViewportPointToRay(new Vector2(u, v));
            return ray.origin + ray.direction.normalized * depthMeters;
        }
    }
}
