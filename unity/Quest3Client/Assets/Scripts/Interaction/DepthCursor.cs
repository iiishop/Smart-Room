using Meta.XR;
using Meta.XR.EnvironmentDepth;
using UnityEngine;

namespace SmartRoom.Interaction
{
    /// <summary>
    /// 深度锁定小球：接收 ControllerRaycaster 的射线，
    /// 用 EnvironmentRaycastManager 做深度碰撞，把小球吸附在真实物体表面。
    /// 挂载：独立 GameObject（不挂在手柄下，因为小球位置是世界空间）
    /// </summary>
    public sealed class DepthCursor : MonoBehaviour
    {
        [Header("References")]
        [SerializeField] private EnvironmentRaycastManager raycastManager;
        [SerializeField] private ControllerRaycaster controllerRaycaster;

        [Header("Cursor Visuals")]
        [SerializeField] private GameObject cursorPrefab;
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
        public GameObject CursorInstance { get; private set; }

        public event System.Action<bool, Vector3, Vector3> OnHitChanged;

        private Material _cursorMaterial;
        private static readonly int ColorId = Shader.PropertyToID("_Color");

        private void Awake()
        {
            if (raycastManager == null)
                raycastManager = FindFirstObjectByType<EnvironmentRaycastManager>();

            if (controllerRaycaster == null)
                controllerRaycaster = FindFirstObjectByType<ControllerRaycaster>();

            maxDistance = controllerRaycaster != null
                ? controllerRaycaster.CurrentMaxDistance
                : maxDistance;

            InitializeCursor();
        }

        private void InitializeCursor()
        {
            if (cursorPrefab == null)
            {
                // Create a simple sphere if no prefab provided
                CursorInstance = GameObject.CreatePrimitive(PrimitiveType.Sphere);
                CursorInstance.name = "DepthCursor_Sphere";
                CursorInstance.hideFlags = HideFlags.HideAndDontSave;
                Destroy(CursorInstance.GetComponent<Collider>());
            }
            else
            {
                CursorInstance = Instantiate(cursorPrefab, transform);
                CursorInstance.name = "DepthCursor_Sphere";
            }

            // Scale the sphere to base radius (default Unity sphere has radius 0.5)
            var sphereScale = baseRadius / 0.5f;
            CursorInstance.transform.localScale = Vector3.one * sphereScale;

            // Get or create material
            var renderer = CursorInstance.GetComponent<Renderer>();
            if (renderer != null)
            {
                _cursorMaterial = renderer.material;
                // Ensure it's an instance so we can modify per-instance
                if (!_cursorMaterial.name.Contains("(Instance)"))
                {
                    _cursorMaterial = new Material(_cursorMaterial);
                    renderer.material = _cursorMaterial;
                }
            }
            else
            {
                _cursorMaterial = new Material(Shader.Find("Universal Render Pipeline/Unlit"));
                CursorInstance.AddComponent<MeshRenderer>().material = _cursorMaterial;
            }

            _cursorMaterial.SetColor(ColorId, missColor);
            CursorInstance.SetActive(false);

            Debug.Log("[DepthCursor] Initialized cursor sphere");
        }

        private void OnEnable()
        {
            if (controllerRaycaster != null)
                controllerRaycaster.OnRayUpdated += UpdateCursor;
        }

        private void OnDisable()
        {
            if (controllerRaycaster != null)
                controllerRaycaster.OnRayUpdated -= UpdateCursor;
        }

        /// <summary>
        /// 核心方法：每帧调用，用深度射线更新小球位置
        /// </summary>
        public bool UpdateCursor(Ray ray)
        {
            if (raycastManager == null || CursorInstance == null) return false;

            bool wasHitting = IsHitting;

            // EnvironmentRaycastManager.Raycast returns true if it hit a depth surface
            if (raycastManager.Raycast(ray, out var hit, maxDistance))
            {
                HitPoint = hit.point;
                HitNormal = hit.normal;
                HitDistance = hit.distance;
                IsHitting = true;

                CursorInstance.transform.position = hit.point;
                if (hit.normal != Vector3.zero)
                {
                    CursorInstance.transform.rotation = Quaternion.LookRotation(hit.normal, Vector3.up);
                }

                _cursorMaterial?.SetColor(ColorId, hitColor);
                CursorInstance.SetActive(true);
            }
            else
            {
                // 未命中：小球浮动在射线最远端
                HitPoint = ray.origin + ray.direction * maxDistance;
                HitNormal = Vector3.up;
                HitDistance = maxDistance;
                IsHitting = false;

                CursorInstance.transform.position = HitPoint;
                CursorInstance.transform.rotation = Quaternion.identity;
                _cursorMaterial?.SetColor(ColorId, missColor);
                CursorInstance.SetActive(true);
            }

            if (wasHitting != IsHitting)
            {
                OnHitChanged?.Invoke(IsHitting, HitPoint, HitNormal);
            }

            return IsHitting;
        }

        public void Show()
        {
            if (CursorInstance != null) CursorInstance.SetActive(true);
        }

        public void Hide()
        {
            if (CursorInstance != null) CursorInstance.SetActive(false);
        }

        /// <summary>
        /// 返回当前命中点。未命中时返回射线末端点（用于调试）
        /// </summary>
        public Vector3 GetHitPoint()
        {
            return HitPoint;
        }

        private void Update()
        {
            // If not driven by event (e.g., no ControllerRaycaster), poll manually
            if (controllerRaycaster == null || !controllerRaycaster.IsActive)
            {
                // Passive mode: just keep cursor where it is
            }
        }

        private void OnDestroy()
        {
            if (CursorInstance != null)
            {
                if (_cursorMaterial != null) Destroy(_cursorMaterial);
                Destroy(CursorInstance);
            }
        }
    }
}
