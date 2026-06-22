using System.Collections.Generic;
using Meta.XR;
using Meta.XR.MRUtilityKit;
using UnityEngine;

namespace SmartRoom.Interaction
{
    /// <summary>
    /// 深度探针 v2 — 基于 EnvironmentRaycastManager 的精确射线检测。
    /// 参考 OpenQuestCapture 架构：从相机打密集射线，用 Meta SDK 原生 raycast 获取
    /// 精确表面命中点 + 法线，无需 depth frame 采样和内参投影。
    /// 
    /// 交互：按住右手柄扳机 → 以 DepthCursor 为球心、sphereRadius 为半径的
    /// 球体内，显示与深度表面重合的彩色点云。松开 → 消失。
    /// 着色：表面角度（白=正对表面、鲜艳=斜掠角）。
    /// 
    /// 挂载：独立 GameObject（TriggerDepthProbeRoot）
    /// </summary>
    public sealed class TriggerDepthProbe : MonoBehaviour
    {
        [Header("References")]
        [SerializeField] private DepthCursor depthCursor;
        [SerializeField] private Camera xrCamera;
        [SerializeField] private EnvironmentRaycastManager raycastManager;

        [Header("Probe Shape")]
        [SerializeField] private float sphereRadius = 0.2f;
        [SerializeField] private float maxDistance = 10f;

        [Header("Ray Density")]
        [SerializeField] private int rayGrid = 60; // oversample: 60×60 max, then thin to uniform 3D spacing
        [SerializeField] private int minGrid = 12;
        [SerializeField] private float targetSpacing = 0.015f; // target 3D Euclidean distance between points (meters)

        [Header("Rendering")]
        [SerializeField] private float pointSize = 0.012f; // larger for Gaussian billboard (soft edges look smaller)
        [SerializeField] private int maxPoints = 4096;

        [Header("Sphere Boundary Ring")]
        [SerializeField] private bool showBoundaryRing = true;
        [SerializeField] private Color ringColor = new Color(0.3f, 1f, 0.3f, 0.6f);
        [SerializeField] private int ringSegments = 64;
        [SerializeField] private float ringWidth = 0.002f;

        [Header("Room Coordinate UI")]
        [SerializeField] private bool showRoomCoordinateSystemPanel = true;

        // Internal
        private Material _probeMaterial;
        private Mesh _quadMesh;
        private ComputeBuffer _pointBuffer;
        private readonly List<ProbePoint> _points = new List<ProbePoint>();
        private bool _wasActive;

        // Boundary ring
        private LineRenderer _ringRenderer;
        private GameObject _ringObject;

        // Rate-limited diagnostic logging
        private float _lastDiagLogTime;
        private const float DiagLogInterval = 2f;

        private static readonly int ProbePointsId = Shader.PropertyToID("_ProbePoints");
        private static readonly int PointSizeId = Shader.PropertyToID("_PointSize");

        private struct ProbePoint
        {
            public Vector4 data; // xyz = world pos, w = packed RGBA color
        }

        private void Awake()
        {
            if (depthCursor == null)
                depthCursor = FindFirstObjectByType<DepthCursor>();

            if (xrCamera == null)
                xrCamera = Camera.main;

            if (raycastManager == null)
                raycastManager = FindFirstObjectByType<EnvironmentRaycastManager>();

            InitializeMaterial();
            CreateQuadMesh();
            CreateBoundaryRing();
            EnsureRoomCoordinateSystemPanel();

            Debug.Log($"[DepthProbe] Awake — depthCursor={depthCursor != null} " +
                      $"camera={xrCamera != null} raycastMgr={raycastManager != null} " +
                      $"material={_probeMaterial != null} enabled={enabled}");
        }

        private void InitializeMaterial()
        {
            var shader = Shader.Find("SmartRoom/Scanning/DepthProbeLit");
            if (shader == null)
                shader = Shader.Find("SmartRoom/Scanning/DepthProbePoint"); // fallback
            if (shader == null)
            {
                Debug.LogError("[DepthProbe] No probe shader found.");
                enabled = false;
                return;
            }
            _probeMaterial = new Material(shader);
            _probeMaterial.SetVector("_LightDir", new Vector4(0.5f, 0.8f, 0.3f, 0f));
            _probeMaterial.SetFloat("_Ambient", 0.3f);
        }

        private void CreateQuadMesh()
        {
            _quadMesh = new Mesh { name = "ProbePointQuad" };
            _quadMesh.vertices = new[]
            {
                new Vector3(-0.5f, -0.5f, 0), new Vector3( 0.5f, -0.5f, 0),
                new Vector3( 0.5f,  0.5f, 0), new Vector3(-0.5f,  0.5f, 0),
            };
            _quadMesh.uv = new[]
            {
                new Vector2(0, 0), new Vector2(1, 0), new Vector2(1, 1), new Vector2(0, 1),
            };
            _quadMesh.triangles = new[] { 0, 1, 2, 0, 2, 3 };
            _quadMesh.RecalculateBounds();
        }

        private void OnDestroy()
        {
            ReleaseBuffer();
            if (_probeMaterial != null) Destroy(_probeMaterial);
            if (_ringObject != null) Destroy(_ringObject);
        }

        private void LateUpdate()
        {
            if (SmartRoom.UI.RoomCoordinateSystemPanel.IsUiBlockingSceneInput)
            {
                _points.Clear();
                ReleaseBuffer();
                UpdateBoundaryRing(false, Vector3.zero, Vector3.up);
                _wasActive = false;
                return;
            }

            bool triggerHeld = OVRInput.Get(OVRInput.RawButton.RIndexTrigger);

            if (!triggerHeld)
            {
                if (_wasActive)
                {
                    _points.Clear();
                    ReleaseBuffer();
                    UpdateBoundaryRing(false, Vector3.zero, Vector3.up);
                    _wasActive = false;
                }
                return;
            }

            _wasActive = true;

            // Validate state
            if (depthCursor == null || !depthCursor.IsHitting)
            {
                UpdateBoundaryRing(false, Vector3.zero, Vector3.up);
                DiagLog("depthCursor not hitting");
                return;
            }
            if (xrCamera == null)
            {
                DiagLog("no XR camera");
                return;
            }
            if (raycastManager == null)
            {
                DiagLog("no EnvironmentRaycastManager");
                return;
            }

            Vector3 sphereCenter = depthCursor.GetHitPoint();
            Vector3 camPos = xrCamera.transform.position;
            UpdateBoundaryRing(true, sphereCenter, depthCursor.HitNormal);
            float distToCenter = Vector3.Distance(sphereCenter, camPos);

            if (distToCenter > maxDistance)
            {
                DiagLog($"sphere too far: {distToCenter:F1}m");
                return;
            }

            // Project sphere to viewport
            ComputeSphereViewportBounds(sphereCenter, camPos, xrCamera,
                out float uMin, out float uMax, out float vMin, out float vMax);

            if (uMin > uMax || vMin > vMax)
            {
                DiagLog($"sphere outside view: u=[{uMin:F3},{uMax:F3}] v=[{vMin:F3},{vMax:F3}]");
                return;
            }

            // --- Distance-compensated grid: maintain roughly constant point count
            // regardless of how far the sphere is. A far sphere covers fewer viewport
            // pixels, so we increase ray density to compensate.
            float uExtent = uMax - uMin;
            float vExtent = vMax - vMin;
            float viewportArea = uExtent * vExtent;
            float densityScale = 1f / Mathf.Sqrt(Mathf.Max(0.0001f, viewportArea));
            int gridW = Mathf.Max(minGrid, Mathf.CeilToInt(rayGrid * densityScale * uExtent));
            int gridH = Mathf.Max(minGrid, Mathf.CeilToInt(rayGrid * densityScale * vExtent));

            float du = uExtent / (gridW - 1);
            float dv = vExtent / (gridH - 1);
            float sphereRadiusSq = sphereRadius * sphereRadius;

            _points.Clear();

            for (int gy = 0; gy < gridH && _points.Count < maxPoints; gy++)
            {
                for (int gx = 0; gx < gridW && _points.Count < maxPoints; gx++)
                {
                    float u = uMin + gx * du;
                    float v = vMin + gy * dv;

                    Ray ray = xrCamera.ViewportPointToRay(new Vector3(u, v, 0f));

                    if (!raycastManager.Raycast(ray, out EnvironmentRaycastHit hit, maxDistance))
                        continue;

                    // Sphere containment + edge fade
                    float distFromCenterSq = (hit.point - sphereCenter).sqrMagnitude;
                    if (distFromCenterSq > sphereRadiusSq)
                        continue;

                    float distFromCenter = Mathf.Sqrt(distFromCenterSq);
                    // EDL-style: edge points get DARKER (not transparent)
                    // Luminance = 1.0 at center, 0.2 at edge
                    float edgeT = 1.0f - Mathf.Clamp01(distFromCenter / sphereRadius);
                    float luminance = 0.2f + 0.8f * (edgeT * edgeT); // smooth falloff

                    Color color = ComputeAngleColor(hit.point, hit.normal, camPos);
                    // Encode luminance into alpha channel (shader multiplies RGB by alpha for darkness)
                    color.a = luminance;

                    _points.Add(new ProbePoint
                    {
                        data = new Vector4(hit.point.x, hit.point.y, hit.point.z, PackColor(color))
                    });
                }
            }

            if (_points.Count == 0)
            {
                DiagLog($"no hits in sphere — center=({sphereCenter.x:F2},{sphereCenter.y:F2},{sphereCenter.z:F2}) dist={distToCenter:F2}m");
                return;
            }

            // --- Thin to uniform 3D Euclidean spacing ---
            // Viewport-uniform rays produce non-uniform surface spacing:
            // head-on areas = sparse, grazing areas = dense.
            // Reject points that are too close to an already-accepted neighbor in 3D.
            float minDistSq = targetSpacing * targetSpacing;
            var thinned = new List<ProbePoint>(_points.Count);
            for (int i = 0; i < _points.Count; i++)
            {
                var p = _points[i];
                Vector3 pp = new Vector3(p.data.x, p.data.y, p.data.z);
                bool tooClose = false;
                for (int j = 0; j < thinned.Count; j++)
                {
                    var a = thinned[j];
                    float dx = pp.x - a.data.x;
                    float dy = pp.y - a.data.y;
                    float dz = pp.z - a.data.z;
                    if (dx * dx + dy * dy + dz * dz < minDistSq)
                    {
                        tooClose = true;
                        break;
                    }
                }
                if (!tooClose)
                    thinned.Add(p);
            }

            int dropped = _points.Count - thinned.Count;
            _points.Clear();
            _points.AddRange(thinned);

            if (_points.Count == 0)
            {
                DiagLog($"all points thinned out — sphere center=({sphereCenter.x:F2},{sphereCenter.y:F2},{sphereCenter.z:F2})");
                return;
            }

            // Render
            ReleaseBuffer();
            _pointBuffer = new ComputeBuffer(_points.Count, sizeof(float) * 4);
            _pointBuffer.SetData(_points);

            _probeMaterial.SetBuffer(ProbePointsId, _pointBuffer);
            _probeMaterial.SetFloat(PointSizeId, pointSize);

            var bounds = new Bounds(sphereCenter, Vector3.one * sphereRadius * 2f);
            Graphics.DrawMeshInstancedProcedural(_quadMesh, 0, _probeMaterial, bounds, _points.Count);

            DiagLog($"rendered {_points.Count} pts (dropped {dropped}) — center=({sphereCenter.x:F2},{sphereCenter.y:F2},{sphereCenter.z:F2})");
        }

        /// <summary>
        /// 计算球体在 viewport 空间的投影区域。
        /// 用小角度近似：球心的 viewport UV + 球半径对应的 UV 范围。
        /// </summary>
        private void ComputeSphereViewportBounds(
            Vector3 sphereCenter, Vector3 camPos, Camera cam,
            out float uMin, out float uMax, out float vMin, out float vMax)
        {
            uMin = 1f; uMax = 0f; vMin = 1f; vMax = 0f;

            float dist = Vector3.Distance(sphereCenter, camPos);
            if (dist <= 0.001f) return;

            // Check if sphere center is in front of camera
            Vector3 camForward = cam.transform.forward;
            Vector3 toSphere = sphereCenter - camPos;
            float localZ = Vector3.Dot(toSphere, camForward);
            if (localZ <= 0f) return;

            // Viewport UV of sphere center — use camera pixel dimensions, NOT Screen.width/height
            // In VR (Single Pass Instanced), Screen.width is the stereo backbuffer width,
            // but camera.WorldToScreenPoint returns coordinates in the left eye's pixel rect.
            Vector3 screenPt = cam.WorldToScreenPoint(sphereCenter);
            float u = screenPt.x / cam.pixelWidth;
            float v = screenPt.y / cam.pixelHeight;

            // Focal length in pixels (from vertical FOV) — use camera pixelHeight
            float focalPixels = cam.pixelHeight / (2f * Mathf.Tan(cam.fieldOfView * 0.5f * Mathf.Deg2Rad));

            // Sphere angular radius → pixel radius → viewport radius
            float pixelRadius = focalPixels * sphereRadius / dist;
            float uRadius = pixelRadius / cam.pixelWidth;
            float vRadius = pixelRadius / cam.pixelHeight;

            uMin = Mathf.Max(0f, u - uRadius);
            uMax = Mathf.Min(1f, u + uRadius);
            vMin = Mathf.Max(0f, v - vRadius);
            vMax = Mathf.Min(1f, v + vRadius);
        }

        /// <summary>
        /// 世界空间法线 → RGB 着色（法线贴图风格）。
        /// 平面 = 均匀色，曲面 = 渐变，折角 = 突变——直接传达表面 3D 形状。
        /// </summary>
        private Color ComputeAngleColor(Vector3 hitPoint, Vector3 normal, Vector3 camPos)
        {
            // Map world-space normal from [-1,1] to [0,1] RGB
            // R = right (+X), G = up (+Y), B = forward (+Z)
            float r = normal.x * 0.5f + 0.5f;
            float g = normal.y * 0.5f + 0.5f;
            float b = normal.z * 0.5f + 0.5f;
            return new Color(r, g, b, 1f);
        }

        private void ReleaseBuffer()
        {
            if (_pointBuffer != null)
            {
                _pointBuffer.Release();
                _pointBuffer = null;
            }
        }

        private void OnDisable()
        {
            _points.Clear();
            ReleaseBuffer();
        }

        private void EnsureRoomCoordinateSystemPanel()
        {
            if (!showRoomCoordinateSystemPanel) return;

            SmartRoom.UI.RoomCoordinateSystemPanel.EnsureExists(xrCamera != null ? xrCamera : Camera.main);
        }

        private static float PackColor(Color c)
        {
            uint r = (uint)(Mathf.Clamp01(c.r) * 255f);
            uint g = (uint)(Mathf.Clamp01(c.g) * 255f);
            uint b = (uint)(Mathf.Clamp01(c.b) * 255f);
            uint a = (uint)(Mathf.Clamp01(c.a) * 255f);
            uint packed = (r << 24) | (g << 16) | (b << 8) | a;
            return System.BitConverter.Int32BitsToSingle((int)packed);
        }

        private void DiagLog(string msg)
        {
            if (Time.time - _lastDiagLogTime < DiagLogInterval) return;
            _lastDiagLogTime = Time.time;
            Debug.LogWarning($"[DepthProbe] {msg}");
        }

        /// <summary>
        /// 3D wireframe sphere: three orthogonal great circles.
        /// Always looks spherical from any viewing angle — no dependency on surface normal.
        /// </summary>
        private void CreateBoundaryRing()
        {
            _ringObject = new GameObject("ProbeBoundaryRing");
            _ringObject.transform.SetParent(transform);
            _ringRenderer = _ringObject.AddComponent<LineRenderer>();
            _ringRenderer.positionCount = ringSegments * 3;
            _ringRenderer.loop = false;
            _ringRenderer.useWorldSpace = true;
            _ringRenderer.startWidth = ringWidth;
            _ringRenderer.endWidth = ringWidth;

            var shader = Shader.Find("Universal Render Pipeline/Unlit");
            if (shader == null) shader = Shader.Find("Unlit/Color");
            _ringRenderer.material = new Material(shader);
            _ringRenderer.material.color = ringColor;
            _ringRenderer.enabled = false;
        }

        private void UpdateBoundaryRing(bool visible, Vector3 center, Vector3 normal)
        {
            if (_ringRenderer == null || !showBoundaryRing) return;

            if (!visible)
            {
                _ringRenderer.enabled = false;
                return;
            }

            _ringRenderer.enabled = true;
            float r = sphereRadius * 0.95f;

            // Three orthogonal great circles
            for (int i = 0; i < ringSegments; i++)
            {
                float angle = 2f * Mathf.PI * i / ringSegments;
                float x = Mathf.Cos(angle) * r;
                float y = Mathf.Sin(angle) * r;

                // XY circle (flat on XY plane)
                _ringRenderer.SetPosition(i, center + new Vector3(x, y, 0));
                // XZ circle (flat on XZ plane)
                _ringRenderer.SetPosition(ringSegments + i, center + new Vector3(x, 0, y));
                // YZ circle (flat on YZ plane)
                _ringRenderer.SetPosition(2 * ringSegments + i, center + new Vector3(0, x, y));
            }
        }
    }
}
