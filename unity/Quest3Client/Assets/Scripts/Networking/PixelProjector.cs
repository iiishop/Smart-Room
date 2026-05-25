using Meta.XR;
using UnityEngine;

namespace SmartRoom.Networking
{
    /// <summary>
    /// 世界坐标 ↔ PCA 像素坐标的双向投影。
    /// 用 PassthroughCameraAccess 的 Intrinsics 做精确投影。
    /// 挂载：跟 RgbStreamModule 同一 GameObject
    /// </summary>
    public sealed class PixelProjector : MonoBehaviour
    {
        [Header("References")]
        [SerializeField] private PassthroughCameraAccess passthroughCameraAccess;

        [Header("Image Dimensions")]
        [SerializeField] private int imageWidth = 640;
        [SerializeField] private int imageHeight = 480;

        // Intrinsics
        public Vector2 FocalPixels { get; private set; }    // fx, fy
        public Vector2 PrincipalPoint { get; private set; }  // cx, cy
        public int ImageWidth => imageWidth;
        public int ImageHeight => imageHeight;
        public bool IsReady => _initialized;

        private bool _initialized;
        private Transform _pcaTransform;

        private void Awake()
        {
            if (passthroughCameraAccess == null)
                passthroughCameraAccess = FindFirstObjectByType<PassthroughCameraAccess>();

            Initialize();
        }

        private void Initialize()
        {
            if (passthroughCameraAccess == null)
            {
                Debug.LogWarning("[PixelProjector] PassthroughCameraAccess not found");
                return;
            }

            _pcaTransform = passthroughCameraAccess.transform;

            try
            {
                var intrinsics = passthroughCameraAccess.Intrinsics;
                FocalPixels = intrinsics.FocalLength;
                PrincipalPoint = intrinsics.PrincipalPoint;
                _initialized = true;
                Debug.Log($"[PixelProjector] Ready: fx={FocalPixels.x:F1} fy={FocalPixels.y:F1} " +
                          $"cx={PrincipalPoint.x:F1} cy={PrincipalPoint.y:F1}");
            }
            catch (System.Exception ex)
            {
                // Fallback defaults for Quest 3
                FocalPixels = new Vector2(640f, 640f);
                PrincipalPoint = new Vector2(320f, 240f);
                _initialized = true;
                Debug.LogWarning($"[PixelProjector] PCA Intrinsics unavailable, using defaults: {ex.Message}");
            }
        }

        /// <summary>
        /// 世界坐标 → PCA 像素坐标。
        /// 返回 null 表示世界点在 PCA 视野外。
        /// </summary>
        public Vector2Int? WorldToPixel(Vector3 worldPoint)
        {
            if (!_initialized || _pcaTransform == null) return null;

            // Transform world → PCA camera-local space
            Vector3 local = _pcaTransform.InverseTransformPoint(worldPoint);

            // PCA camera: +Z is forward, +X is right, +Y is up
            // Normalized image coordinates (in focal-length units)
            if (local.z <= 0f) return null; // Behind camera

            float xNorm = local.x / local.z;
            float yNorm = local.y / local.z;

            // Convert to pixel coordinates
            float px = xNorm * FocalPixels.x + PrincipalPoint.x;
            float py = yNorm * FocalPixels.y + PrincipalPoint.y;

            int pxInt = Mathf.RoundToInt(px);
            int pyInt = Mathf.RoundToInt(py);

            // Check bounds
            if (pxInt < 0 || pxInt >= imageWidth || pyInt < 0 || pyInt >= imageHeight)
                return null;

            return new Vector2Int(pxInt, pyInt);
        }

        /// <summary>
        /// PCA 像素 + 深度 → 世界坐标（逆运算）。
        /// 用于从 SAM mask 还原物体 3D 点云。
        /// </summary>
        public Vector3? PixelToWorld(Vector2Int pixel, float depthMeters)
        {
            if (!_initialized || _pcaTransform == null) return null;
            if (depthMeters <= 0f) return null;

            // Pixel → normalized image coords
            float xNorm = (pixel.x - PrincipalPoint.x) / FocalPixels.x;
            float yNorm = (pixel.y - PrincipalPoint.y) / FocalPixels.y;

            // Camera-local 3D point
            Vector3 local = new Vector3(xNorm * depthMeters, yNorm * depthMeters, depthMeters);

            // Transform to world
            return _pcaTransform.TransformPoint(local);
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
