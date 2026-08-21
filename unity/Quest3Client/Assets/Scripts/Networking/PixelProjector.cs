using Meta.XR;
using UnityEngine;

namespace SmartRoom.Networking
{
    /// <summary>
    /// World coordinates to PCA image pixels and back.
    /// Image pixels use the conventional top-left origin used by JPEG and the backend.
    /// PCA viewport coordinates use Meta/Unity's bottom-left origin.
    /// </summary>
    public sealed class PixelProjector : MonoBehaviour
    {
        [Header("References")]
        [SerializeField] private PassthroughCameraAccess passthroughCameraAccess;

        [Header("PCA Image Dimensions")]
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

        // PCA native intrinsics (before crop and output scaling).
        private float _nativeWidth;
        private float _nativeHeight;

        private void Awake()
        {
            if (passthroughCameraAccess == null)
                passthroughCameraAccess = FindFirstObjectByType<PassthroughCameraAccess>();

            RefreshProjectionConfiguration(force: true);
        }

        private bool RefreshProjectionConfiguration(bool force = false)
        {
            if (passthroughCameraAccess == null)
            {
                _initialized = false;
                if (force)
                    Debug.LogWarning("[PixelProjector] PassthroughCameraAccess not found");
                return false;
            }

            int targetWidth = imageWidth;
            int targetHeight = imageHeight;
            if (autoSyncDimensions)
            {
                if (passthroughCameraAccess.IsPlaying)
                {
                    Vector2Int current = passthroughCameraAccess.CurrentResolution;
                    if (current.x > 0 && current.y > 0)
                    {
                        targetWidth = current.x;
                        targetHeight = current.y;
                    }
                }
            }

            if (targetWidth <= 0 || targetHeight <= 0 ||
                !passthroughCameraAccess.enabled || !passthroughCameraAccess.IsPlaying)
            {
                _initialized = false;
                return false;
            }

            try
            {
                var intrinsics = passthroughCameraAccess.Intrinsics;
                Vector2 sensorResolution = intrinsics.SensorResolution;
                Vector2 currentResolution = passthroughCameraAccess.CurrentResolution;
                if (sensorResolution.x <= 0f || sensorResolution.y <= 0f ||
                    currentResolution.x <= 0f || currentResolution.y <= 0f)
                {
                    _initialized = false;
                    return false;
                }

                bool changed = !_initialized ||
                               imageWidth != targetWidth ||
                               imageHeight != targetHeight ||
                               !Mathf.Approximately(_nativeWidth, sensorResolution.x) ||
                               !Mathf.Approximately(_nativeHeight, sensorResolution.y);
                imageWidth = targetWidth;
                imageHeight = targetHeight;
                _nativeWidth = sensorResolution.x;
                _nativeHeight = sensorResolution.y;

                // Equivalent to MRUK PassthroughCameraAccess.CalcSensorCropRegion().
                Vector2 cropScale = new Vector2(
                    currentResolution.x / sensorResolution.x,
                    currentResolution.y / sensorResolution.y);
                cropScale /= Mathf.Max(cropScale.x, cropScale.y);
                Vector2 cropSize = Vector2.Scale(sensorResolution, cropScale);
                Vector2 cropOrigin = (sensorResolution - cropSize) * 0.5f;
                float scaleX = imageWidth / cropSize.x;
                float scaleY = imageHeight / cropSize.y;

                FocalPixels = new Vector2(
                    intrinsics.FocalLength.x * scaleX,
                    intrinsics.FocalLength.y * scaleY);
                PrincipalPoint = new Vector2(
                    (intrinsics.PrincipalPoint.x - cropOrigin.x) * scaleX,
                    (intrinsics.PrincipalPoint.y - cropOrigin.y) * scaleY);
                _initialized = true;

                if (changed || force)
                {
                    Debug.Log(
                        $"[PixelProjector] Ready: camera={passthroughCameraAccess.CameraPosition} " +
                        $"fx={FocalPixels.x:F1} fy={FocalPixels.y:F1} " +
                        $"cx={PrincipalPoint.x:F1} cy={PrincipalPoint.y:F1} " +
                        $"sensor={_nativeWidth:F0}x{_nativeHeight:F0} " +
                        $"stream={currentResolution.x:F0}x{currentResolution.y:F0} " +
                        $"image={imageWidth}x{imageHeight}");
                }
                return true;
            }
            catch (System.Exception ex)
            {
                _initialized = false;
                if (force)
                    Debug.LogWarning($"[PixelProjector] PCA projection unavailable: {ex.Message}");
                return false;
            }
        }

        /// <summary>
        /// World position to top-left-origin RGB image pixel.
        /// </summary>
        public Vector2Int? WorldToPixel(Vector3 worldPoint)
        {
            if (!RefreshProjectionConfiguration())
            {
                Debug.LogWarning("[PixelProjector] WorldToPixel failed: PCA projection is not ready");
                return null;
            }

            Pose cameraPose = passthroughCameraAccess.GetCameraPose();
            Vector3 cameraPoint = Quaternion.Inverse(cameraPose.rotation) * (worldPoint - cameraPose.position);
            if (cameraPoint.z <= 1e-4f)
            {
                Debug.LogWarning(
                    $"[PixelProjector] WorldToPixel: point behind camera. " +
                    $"world=({worldPoint.x:F2},{worldPoint.y:F2},{worldPoint.z:F2}) " +
                    $"cameraZ={cameraPoint.z:F3}");
                return null;
            }

            // MRUK handles lens principal-point offset and sensor crop internally.
            Vector2 viewport = passthroughCameraAccess.WorldToViewportPoint(worldPoint, cameraPose);
            int pxInt = Mathf.RoundToInt(viewport.x * imageWidth);
            int pyInt = Mathf.RoundToInt((imageHeight - 1f) - viewport.y * imageHeight);

            if (pxInt < 0 || pxInt >= imageWidth || pyInt < 0 || pyInt >= imageHeight)
            {
                Debug.LogWarning(
                    $"[PixelProjector] WorldToPixel: pixel out of bounds. " +
                    $"pixel=({pxInt},{pyInt}) image={imageWidth}x{imageHeight} " +
                    $"viewport=({viewport.x:F3},{viewport.y:F3}) cameraZ={cameraPoint.z:F3}");
                return null;
            }

            return new Vector2Int(pxInt, pyInt);
        }

        /// <summary>
        /// Top-left-origin RGB image pixel and ray distance to world position.
        /// </summary>
        public Vector3? PixelToWorld(Vector2Int pixel, float depthMeters)
        {
            if (!RefreshProjectionConfiguration()) return null;
            if (depthMeters <= 0f) return null;
            if (pixel.x < 0 || pixel.x >= imageWidth || pixel.y < 0 || pixel.y >= imageHeight)
                return null;

            float u = (float)pixel.x / imageWidth;
            float v = (imageHeight - 1f - pixel.y) / imageHeight;

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
            if (!RefreshProjectionConfiguration() || passthroughCameraAccess == null) return null;
            if (!passthroughCameraAccess.enabled || !passthroughCameraAccess.IsPlaying) return null;

            var ray = passthroughCameraAccess.ViewportPointToRay(new Vector2(u, v));
            return ray.origin + ray.direction.normalized * depthMeters;
        }
    }
}
