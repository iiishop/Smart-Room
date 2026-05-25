using Meta.XR;
using SmartRoom.Networking;
using UnityEngine;

namespace SmartRoom.Scanning
{
    /// <summary>
    /// 实时渲染深度帧的点云：每 N 个像素取一个深度值，投影到世界空间，
    /// 用 GPU Instancing 渲染成小方块。
    /// 挂载：独立 GameObject
    /// </summary>
    public sealed class DepthPointCloudRenderer : MonoBehaviour
    {
        [Header("References")]
        [SerializeField] private DepthFrameSampler depthFrameSampler;
        [SerializeField] private PassthroughCameraAccess passthroughCameraAccess;
        [SerializeField] private Material pointMaterial;
        [SerializeField] private Material pointFallbackMaterial;

        [Header("Sampling")]
        [SerializeField] [Range(1, 32)] private int subsampleStep = 8;
        [SerializeField] private int maxPoints = 16384;
        [SerializeField] private float pointSize = 0.01f;

        public bool IsVisible
        {
            get => _isVisible;
            set { _isVisible = value; }
        }
        public int PointCount => _pointCount;

        private bool _isVisible = true;
        private int _pointCount;
        private Vector3[] _pointPositions;
        private Mesh _pointMesh;
        private Material _usedMaterial;
        private Matrix4x4[] _matrices;
        private static readonly int ColorId = Shader.PropertyToID("_Color");

        private void Awake()
        {
            if (depthFrameSampler == null)
                depthFrameSampler = FindFirstObjectByType<DepthFrameSampler>();

            if (passthroughCameraAccess == null)
                passthroughCameraAccess = FindFirstObjectByType<PassthroughCameraAccess>();

            // Create a tiny quad mesh for each point
            _pointMesh = CreatePointMesh(pointSize);

            // Setup material
            if (pointMaterial != null)
                _usedMaterial = new Material(pointMaterial);
            else if (pointFallbackMaterial != null)
                _usedMaterial = new Material(pointFallbackMaterial);
            else
            {
                var shader = Shader.Find("SmartRoom/Scanning/DepthPointCloud");
                if (shader == null) shader = Shader.Find("Universal Render Pipeline/Unlit");
                _usedMaterial = new Material(shader);
            }

            _usedMaterial.enableInstancing = true;
            _matrices = new Matrix4x4[1023]; // Unity instancing limit per draw call
            _pointPositions = new Vector3[maxPoints];

            Debug.Log("[DepthPointCloud] Initialized");
        }

        private void LateUpdate()
        {
            if (!_isVisible || depthFrameSampler == null || !depthFrameSampler.HasFrame)
                return;

            BuildPointCloud();
            RenderPointCloud();
        }

        private void BuildPointCloud()
        {
            int w = depthFrameSampler.LayoutWidth;
            int h = depthFrameSampler.LayoutHeight;
            if (w <= 0 || h <= 0) return;

            _pointCount = 0;
            int step = subsampleStep;

            for (int y = 0; y < h && _pointCount < maxPoints; y += step)
            {
                for (int x = 0; x < w && _pointCount < maxPoints; x += step)
                {
                    float depth = depthFrameSampler.Sample(x, y);
                    if (depth <= 0f || !float.IsFinite(depth)) continue;

                    float u = (x + 0.5f) / w;
                    float v = (y + 0.5f) / h;

                    Vector3? worldPt = ProjectDepthToWorld(u, v, depth);
                    if (!worldPt.HasValue) continue;

                    _pointPositions[_pointCount++] = worldPt.Value;
                }
            }
        }

        private Vector3? ProjectDepthToWorld(float u, float v, float depth)
        {
            // Use PCA ViewportPointToRay to get the ray direction,
            // then project along that direction by the depth value.
            if (passthroughCameraAccess == null || !passthroughCameraAccess.enabled || !passthroughCameraAccess.IsPlaying)
            {
                // Fallback: use main camera
                var cam = Camera.main;
                if (cam == null) return null;
                var ray = cam.ViewportPointToRay(new Vector3(u, v, 0f));
                return ray.origin + ray.direction * depth;
            }

            var pcaRay = passthroughCameraAccess.ViewportPointToRay(new Vector2(u, v));
            return pcaRay.origin + pcaRay.direction.normalized * depth;
        }

        private void RenderPointCloud()
        {
            if (_pointCount == 0 || _pointMesh == null || _usedMaterial == null) return;

            int remaining = _pointCount;
            int offset = 0;
            int batchSize = 1023;

            while (remaining > 0)
            {
                int count = Mathf.Min(remaining, batchSize);
                for (int i = 0; i < count; i++)
                {
                    _matrices[i] = Matrix4x4.TRS(_pointPositions[offset + i], Quaternion.identity, Vector3.one);
                }

                Graphics.DrawMeshInstanced(_pointMesh, 0, _usedMaterial, _matrices, count);
                remaining -= count;
                offset += count;
            }
        }

        public void Show() => _isVisible = true;
        public void Hide() => _isVisible = false;

        /// <summary>
        /// 创建一个世界空间 1×1 的小 Quad mesh
        /// </summary>
        private static Mesh CreatePointMesh(float size)
        {
            var mesh = new Mesh { name = "DepthPointQuad" };
            float h = size * 0.5f;

            mesh.vertices = new[]
            {
                new Vector3(-h, -h, 0),
                new Vector3( h, -h, 0),
                new Vector3( h,  h, 0),
                new Vector3(-h,  h, 0),
            };

            mesh.uv = new[]
            {
                new Vector2(0, 0),
                new Vector2(1, 0),
                new Vector2(1, 1),
                new Vector2(0, 1),
            };

            mesh.triangles = new[] { 0, 1, 2, 0, 2, 3 };
            mesh.RecalculateNormals();
            mesh.RecalculateBounds();
            return mesh;
        }

        private void OnDestroy()
        {
            if (_pointMesh != null) Destroy(_pointMesh);
            if (_usedMaterial != null) Destroy(_usedMaterial);
        }
    }
}
