using System.Collections.Generic;
using Meta.XR;
using SmartRoom.Networking;
using UnityEngine;

namespace SmartRoom.Interaction
{
    /// <summary>
    /// 深度探针：扣扳机后，以命中点为中心 20cm 立方体范围内，
    /// 采样深度帧，将所有表面点渲染为彩色点云（红=近，蓝=远）。
    /// 
    /// 挂载：独立 GameObject
    /// </summary>
    public sealed class TriggerDepthProbe : MonoBehaviour
    {
        [Header("References")]
        [SerializeField] private ObjectGrabber objectGrabber;
        [SerializeField] private DepthCursor depthCursor;
        [SerializeField] private DepthFrameSampler depthSampler;
        [SerializeField] private PassthroughCameraAccess pca;

        [Header("Probe Settings")]
        [SerializeField] private float boxSize = 0.2f;          // 20cm cube
        [SerializeField] private float pointSize = 0.005f;      // 5mm per point
        [SerializeField] private int subsampleStep = 2;         // sample every Nth pixel
        [SerializeField] private float displayDuration = 5f;    // how long to show
        [SerializeField] private int maxPoints = 4096;

        [Header("Colors")]
        [SerializeField] private Color nearColor = Color.red;
        [SerializeField] private Color farColor = Color.blue;

        private Material _probeMaterial;
        private Mesh _quadMesh;
        private ComputeBuffer _pointBuffer;
        private List<ProbePoint> _points = new List<ProbePoint>();
        private bool _active;
        private float _activationTime;
        private Bounds _renderBounds;

        private static readonly int ProbePointsId = Shader.PropertyToID("_ProbePoints");
        private static readonly int PointSizeId = Shader.PropertyToID("_PointSize");

        private struct ProbePoint
        {
            public Vector4 data; // xyz = world pos, w = packed RGBA color
        }

        private void Awake()
        {
            if (objectGrabber == null)
                objectGrabber = FindFirstObjectByType<ObjectGrabber>();

            if (depthCursor == null)
                depthCursor = FindFirstObjectByType<DepthCursor>();

            if (depthSampler == null)
                depthSampler = FindFirstObjectByType<DepthFrameSampler>();

            if (pca == null)
                pca = FindFirstObjectByType<PassthroughCameraAccess>();

            InitializeMaterial();
            CreateQuadMesh();

            if (objectGrabber != null)
                objectGrabber.OnGrabStarted += OnGrabStarted;
        }

        private void InitializeMaterial()
        {
            var shader = Shader.Find("SmartRoom/Scanning/DepthProbePoint");
            if (shader == null)
            {
                Debug.LogError("[TriggerDepthProbe] Shader 'SmartRoom/Scanning/DepthProbePoint' not found. " +
                               "Ensure DepthProbePoint.shader is in AlwaysIncludedShaders.");
                enabled = false;
                return;
            }
            _probeMaterial = new Material(shader);
        }

        private void CreateQuadMesh()
        {
            _quadMesh = new Mesh { name = "ProbePointQuad" };
            _quadMesh.vertices = new[]
            {
                new Vector3(-0.5f, -0.5f, 0),
                new Vector3( 0.5f, -0.5f, 0),
                new Vector3( 0.5f,  0.5f, 0),
                new Vector3(-0.5f,  0.5f, 0),
            };
            _quadMesh.uv = new[]
            {
                new Vector2(0, 0),
                new Vector2(1, 0),
                new Vector2(1, 1),
                new Vector2(0, 1),
            };
            _quadMesh.triangles = new[] { 0, 1, 2, 0, 2, 3 };
            _quadMesh.RecalculateBounds();
        }

        private void OnDestroy()
        {
            if (objectGrabber != null)
                objectGrabber.OnGrabStarted -= OnGrabStarted;

            ReleaseBuffer();
            if (_quadMesh != null) Destroy(_quadMesh);
            if (_probeMaterial != null) Destroy(_probeMaterial);
        }

        private void OnGrabStarted(Vector3 worldPoint, Vector2Int pixel)
        {
            ActivateProbe(worldPoint);
        }

        /// <summary>
        /// 在指定世界坐标激活探针
        /// </summary>
        public void ActivateProbe(Vector3 worldCenter)
        {
            _active = true;
            _activationTime = Time.time;
            _renderBounds = new Bounds(worldCenter, Vector3.one * boxSize);
            Debug.Log($"[TriggerDepthProbe] Activated at ({worldCenter.x:F2}, {worldCenter.y:F2}, {worldCenter.z:F2})");
        }

        private void LateUpdate()
        {
            if (!_active) return;

            if (Time.time - _activationTime > displayDuration)
            {
                Deactivate();
                return;
            }

            BuildProbePoints();
            RenderProbePoints();
        }

        private void BuildProbePoints()
        {
            if (depthSampler == null || !depthSampler.HasFrame || pca == null) return;

            int dw = depthSampler.LayoutWidth;
            int dh = depthSampler.LayoutHeight;
            if (dw <= 0 || dh <= 0) return;

            // Compute the 2D bounding box of the probe cube projected to screen UV
            Vector3 center = _renderBounds.center;
            Vector3 half = _renderBounds.extents;

            // 8 corners of the box in world space
            Vector3[] corners = new Vector3[8];
            int ci = 0;
            for (int ix = -1; ix <= 1; ix += 2)
            for (int iy = -1; iy <= 1; iy += 2)
            for (int iz = -1; iz <= 1; iz += 2)
                corners[ci++] = center + new Vector3(ix * half.x, iy * half.y, iz * half.z);

            // Project corners to UV, find min/max
            float uMin = 1f, uMax = 0f, vMin = 1f, vMax = 0f;
            int inFrustum = 0;
            for (int i = 0; i < 8; i++)
            {
                var local = pca.transform.InverseTransformPoint(corners[i]);
                if (local.z <= 0f) continue; // behind camera

                // Get UV from PCA — use the intrinsics
                var intrinsics = pca.Intrinsics;
                float xNorm = local.x / local.z;
                float yNorm = local.y / local.z;
                float u = (xNorm * intrinsics.FocalLength.x + intrinsics.PrincipalPoint.x) / dw;
                float v = (yNorm * intrinsics.FocalLength.y + intrinsics.PrincipalPoint.y) / dh;
                // V is from top in screen coords, but depth frame uses top-left = 0.
                // PCA ViewportPointToRay uses bottom-left = 0, so we flip V.
                v = 1f - v;

                u = Mathf.Clamp01(u);
                v = Mathf.Clamp01(v);
                uMin = Mathf.Min(uMin, u);
                uMax = Mathf.Max(uMax, u);
                vMin = Mathf.Min(vMin, v);
                vMax = Mathf.Max(vMax, v);
                inFrustum++;
            }

            if (inFrustum == 0) return;

            // Expand slightly to avoid missing edge points
            float margin = 0.02f;
            uMin = Mathf.Max(0f, uMin - margin);
            uMax = Mathf.Min(1f, uMax + margin);
            vMin = Mathf.Max(0f, vMin - margin);
            vMax = Mathf.Min(1f, vMax + margin);

            // Iterate over pixels in this UV rect
            int step = subsampleStep;
            _points.Clear();

            float depthMin = float.MaxValue;
            float depthMax = float.MinValue;

            // First pass: collect points + find depth range
            var tempPoints = new List<(Vector3 worldPos, float depth)>();
            for (float v = vMin; v <= vMax; v += (float)step / dh)
            {
                for (float u = uMin; u <= uMax; u += (float)step / dw)
                {
                    if (_points.Count + tempPoints.Count >= maxPoints) goto doneCollecting;

                    float depth = depthSampler.Sample(u, 1f - v); // flip V for depth frame (top-left origin)
                    if (depth <= 0f || !float.IsFinite(depth)) continue;

                    var ray = pca.ViewportPointToRay(new Vector2(u, 1f - v));
                    Vector3 worldPt = ray.origin + ray.direction.normalized * depth;

                    // Check if inside the box
                    Vector3 delta = worldPt - center;
                    if (Mathf.Abs(delta.x) > half.x || Mathf.Abs(delta.y) > half.y || Mathf.Abs(delta.z) > half.z)
                        continue;

                    tempPoints.Add((worldPt, depth));
                    if (depth < depthMin) depthMin = depth;
                    if (depth > depthMax) depthMax = depth;
                }
            }
            doneCollecting:

            if (tempPoints.Count == 0) return;

            // Ensure depth range is valid
            if (Mathf.Approximately(depthMin, depthMax))
                depthMax = depthMin + 0.01f;

            // Second pass: build colored points
            for (int i = 0; i < tempPoints.Count; i++)
            {
                var (worldPt, depth) = tempPoints[i];
                float t = Mathf.Clamp01((depth - depthMin) / (depthMax - depthMin));
                Color color = Color.Lerp(nearColor, farColor, t);

                _points.Add(new ProbePoint
                {
                    data = new Vector4(worldPt.x, worldPt.y, worldPt.z, PackColor(color))
                });
            }
        }

        private void RenderProbePoints()
        {
            if (_points.Count == 0 || _probeMaterial == null || _quadMesh == null) return;

            ReleaseBuffer();
            _pointBuffer = new ComputeBuffer(_points.Count, sizeof(float) * 4);
            _pointBuffer.SetData(_points);

            _probeMaterial.SetBuffer(ProbePointsId, _pointBuffer);
            _probeMaterial.SetFloat(PointSizeId, pointSize);

            Graphics.DrawMeshInstancedProcedural(_quadMesh, 0, _probeMaterial, _renderBounds, _points.Count);
        }

        private void Deactivate()
        {
            _active = false;
            _points.Clear();
            ReleaseBuffer();
        }

        private void ReleaseBuffer()
        {
            if (_pointBuffer != null)
            {
                _pointBuffer.Release();
                _pointBuffer = null;
            }
        }

        /// <summary>
        /// Pack Color32 into a single float (for StructuredBuffer<Vector4>.w)
        /// </summary>
        private static float PackColor(Color c)
        {
            uint r = (uint)(Mathf.Clamp01(c.r) * 255f);
            uint g = (uint)(Mathf.Clamp01(c.g) * 255f);
            uint b = (uint)(Mathf.Clamp01(c.b) * 255f);
            uint a = (uint)(Mathf.Clamp01(c.a) * 255f);
            uint packed = (r << 24) | (g << 16) | (b << 8) | a;
            return System.BitConverter.Int32BitsToSingle((int)packed);
        }

        private void OnDisable()
        {
            Deactivate();
        }
    }
}
