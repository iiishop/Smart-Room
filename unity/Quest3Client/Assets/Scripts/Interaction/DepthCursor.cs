using Meta.XR;
using Meta.XR.MRUtilityKit;
using UnityEngine;

namespace SmartRoom.Interaction
{
    /// <summary>
    /// 深度锁定小球：接收 ControllerRaycaster 的射线，
    /// 用 EnvironmentRaycastManager 做深度碰撞，把小球吸附在真实物体表面。
    /// 
    /// 挂载：独立 GameObject（不挂在手柄下）
    /// 要求：Inspector 中拖入 cursorMaterial（必须，防止 IL2CPP strip 导致粉色错误材质）
    ///       创建子 GameObject "CursorSphere" 作为视觉小球。
    /// </summary>
    public sealed class DepthCursor : MonoBehaviour
    {
        [Header("References")]
        [SerializeField] private EnvironmentRaycastManager raycastManager;
        [SerializeField] private ControllerRaycaster controllerRaycaster;
        [SerializeField] private Material cursorMaterial;

        [Header("Cursor Visuals")]
        [SerializeField] private float baseRadius = 0.02f; // 2cm
        [SerializeField] private Color hitColor = Color.green;
        [SerializeField] private Color missColor = Color.red;

        [Header("Ray Settings")]
        [SerializeField] private float maxDistance = 10f;

        // Runtime state
        public bool IsHitting { get; private set; }
        public Vector3 HitPoint { get; private set; }
        public Vector3 HitNormal { get; private set; }
        public float HitDistance { get; private set; }

        public event System.Action<bool, Vector3, Vector3> OnHitChanged;

        private Transform _cursorTransform;
        private MeshRenderer _cursorRenderer;
        private Material _cursorMaterialInstance;
        private static readonly int BaseColorId = Shader.PropertyToID("_BaseColor");
        private static readonly int ColorId = Shader.PropertyToID("_Color");

        private void Awake()
        {
            if (raycastManager == null)
                raycastManager = FindFirstObjectByType<EnvironmentRaycastManager>();

            if (controllerRaycaster == null)
                controllerRaycaster = FindFirstObjectByType<ControllerRaycaster>();

            if (cursorMaterial == null)
            {
                Debug.LogError("[DepthCursor] cursorMaterial is required. Drag a URP/Unlit material in the Inspector. " +
                               "Without it, IL2CPP builds will show a pink error sphere.");
                enabled = false;
                return;
            }

            maxDistance = controllerRaycaster != null
                ? controllerRaycaster.CurrentMaxDistance
                : maxDistance;

            CreateCursor();
        }

        private void CreateCursor()
        {
            // --- Create sphere mesh ---
            var sphereGo = new GameObject("CursorSphere");
            sphereGo.transform.SetParent(transform);
            sphereGo.transform.localPosition = Vector3.zero;
            sphereGo.transform.localRotation = Quaternion.identity;
            sphereGo.transform.localScale = Vector3.one * (baseRadius / 0.5f); // Unity default sphere radius = 0.5

            var mf = sphereGo.AddComponent<MeshFilter>();
            mf.sharedMesh = CreateSphereMesh();

            _cursorRenderer = sphereGo.AddComponent<MeshRenderer>();
            _cursorMaterialInstance = new Material(cursorMaterial);
            _cursorMaterialInstance.SetColor(BaseColorId, missColor);
            _cursorMaterialInstance.SetColor(ColorId, missColor);
            _cursorRenderer.sharedMaterial = _cursorMaterialInstance;

            _cursorTransform = sphereGo.transform;
            sphereGo.SetActive(false);

            Debug.Log("[DepthCursor] Sphere created as child of " + gameObject.name);
        }

        private void OnEnable()
        {
            if (controllerRaycaster != null)
                controllerRaycaster.OnRayUpdated += HandleRayUpdated;
        }

        private void OnDisable()
        {
            if (controllerRaycaster != null)
                controllerRaycaster.OnRayUpdated -= HandleRayUpdated;
        }

        private void HandleRayUpdated(Ray ray)
        {
            UpdateCursor(ray);
        }

        /// <summary>
        /// 每帧用深度射线更新小球位置。返回是否命中深度表面。
        /// </summary>
        public bool UpdateCursor(Ray ray)
        {
            if (raycastManager == null || _cursorTransform == null) return false;

            bool wasHitting = IsHitting;

            if (raycastManager.Raycast(ray, out var hit, maxDistance))
            {
                HitPoint = hit.point;
                HitNormal = hit.normal;
                HitDistance = Vector3.Distance(ray.origin, hit.point);
                IsHitting = true;

                _cursorTransform.position = hit.point;
                if (hit.normal != Vector3.zero)
                {
                    _cursorTransform.rotation = Quaternion.LookRotation(hit.normal, Vector3.up);
                }

                SetCursorColor(hitColor);
                _cursorTransform.gameObject.SetActive(true);
            }
            else
            {
                HitPoint = ray.origin + ray.direction * maxDistance;
                HitNormal = Vector3.up;
                HitDistance = maxDistance;
                IsHitting = false;

                _cursorTransform.position = HitPoint;
                _cursorTransform.rotation = Quaternion.identity;
                SetCursorColor(missColor);
                _cursorTransform.gameObject.SetActive(true);
            }

            if (wasHitting != IsHitting)
            {
                OnHitChanged?.Invoke(IsHitting, HitPoint, HitNormal);
            }

            return IsHitting;
        }

        private void SetCursorColor(Color color)
        {
            if (_cursorMaterialInstance != null)
            {
                _cursorMaterialInstance.SetColor(BaseColorId, color);
                _cursorMaterialInstance.SetColor(ColorId, color);
            }
        }

        public void Show()
        {
            if (_cursorTransform != null) _cursorTransform.gameObject.SetActive(true);
        }

        public void Hide()
        {
            if (_cursorTransform != null) _cursorTransform.gameObject.SetActive(false);
        }

        public Vector3 GetHitPoint()
        {
            return HitPoint;
        }

        private void OnDestroy()
        {
            if (_cursorMaterialInstance != null)
                Destroy(_cursorMaterialInstance);
        }

        /// <summary>
        /// 创建一个简单的 UV sphere mesh（不依赖 Unity 内置 Sphere primitive，
        /// 避免 CreatePrimitive 在 build 中引入不必要的 collider 等）。
        /// </summary>
        private static Mesh CreateSphereMesh()
        {
            var mesh = new Mesh { name = "DepthCursorSphere" };
            int segments = 16;
            int rings = 12;
            int vertCount = (segments + 1) * (rings + 1);
            var verts = new Vector3[vertCount];
            var uvs = new Vector2[vertCount];
            var tris = new int[6 * segments * rings];

            for (int ring = 0; ring <= rings; ring++)
            {
                float phi = Mathf.PI * ring / rings;
                for (int seg = 0; seg <= segments; seg++)
                {
                    float theta = 2f * Mathf.PI * seg / segments;
                    int i = ring * (segments + 1) + seg;
                    verts[i] = new Vector3(
                        Mathf.Sin(phi) * Mathf.Cos(theta),
                        Mathf.Cos(phi),
                        Mathf.Sin(phi) * Mathf.Sin(theta)
                    );
                    uvs[i] = new Vector2((float)seg / segments, (float)ring / rings);
                }
            }

            int ti = 0;
            for (int ring = 0; ring < rings; ring++)
            {
                for (int seg = 0; seg < segments; seg++)
                {
                    int a = ring * (segments + 1) + seg;
                    int b = a + segments + 1;
                    tris[ti++] = a;
                    tris[ti++] = b;
                    tris[ti++] = a + 1;
                    tris[ti++] = a + 1;
                    tris[ti++] = b;
                    tris[ti++] = b + 1;
                }
            }

            mesh.vertices = verts;
            mesh.uv = uvs;
            mesh.triangles = tris;
            mesh.RecalculateNormals();
            mesh.RecalculateBounds();
            return mesh;
        }
    }
}
