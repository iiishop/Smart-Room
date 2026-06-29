using System.Globalization;
using Meta.XR;
using Meta.XR.MRUtilityKit;
using TMPro;
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
        public enum ProbeEditMode
        {
            Add,
            Del
        }

        [Header("References")]
        [SerializeField] private EnvironmentRaycastManager raycastManager;
        [SerializeField] private ControllerRaycaster controllerRaycaster;
        [SerializeField] private Material cursorMaterial;

        [Header("Cursor Visuals")]
        [SerializeField] private bool interactionEnabledAtStartup;
        [SerializeField] private float baseRadius = 0.02f; // 2cm
        [SerializeField] private Color addHitColor = Color.green;
        [SerializeField] private Color delHitColor = Color.red;
        [SerializeField] private Color missColor = Color.red;

        [Header("World Coordinate Label")]
        [SerializeField] private bool showWorldCoordinateLabel = true;
        [SerializeField] private Vector3 coordinateLabelOffset = new Vector3(0f, 0.055f, 0f);
        [SerializeField] private Vector2 coordinateLabelSize = new Vector2(0.7f, 0.24f);
        [SerializeField] private float coordinateLabelFontSize = 0.055f;
        [SerializeField] private Color coordinateLabelColor = Color.white;
        [SerializeField] private Color coordinateLabelOutlineColor = new Color(0f, 0f, 0f, 0.9f);
        [SerializeField, Range(0f, 1f)] private float coordinateLabelOutlineWidth = 0.25f;

        [Header("Ray Settings")]
        [SerializeField] private float maxDistance = 10f;

        [Header("Smoothing")]
        [SerializeField] private float positionSmoothSpeed = 20f;  // higher = snappier
        [SerializeField] private int missToleranceFrames = 5;       // hold position for N frames before accepting miss
        [SerializeField] private float colorSmoothSpeed = 8f;       // smooth color transitions

        // Runtime state
        public bool IsHitting { get; private set; }
        public Vector3 HitPoint { get; private set; }
        public Vector3 HitNormal { get; private set; }
        public float HitDistance { get; private set; }
        public ProbeEditMode CurrentMode { get; private set; } = ProbeEditMode.Add;

        public event System.Action<bool, Vector3, Vector3> OnHitChanged;
        public event System.Action<ProbeEditMode> OnModeChanged;

        private Transform _cursorTransform;
        private MeshRenderer _cursorRenderer;
        private Material _cursorMaterialInstance;
        private Transform _coordinateLabelTransform;
        private TextMeshPro _coordinateLabelText;
        private Camera _labelCamera;
        private static readonly int BaseColorId = Shader.PropertyToID("_BaseColor");
        private static readonly int ColorId = Shader.PropertyToID("_Color");

        // Smoothing state
        private Vector3 _smoothPosition;
        private Vector3 _smoothNormal;
        private Color _smoothColor;
        private int _consecutiveMisses;
        private bool _initialized;
        private bool _interactionEnabled;

        private void Awake()
        {
            _interactionEnabled = interactionEnabledAtStartup;

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

            CreateCoordinateLabel();

            Debug.Log("[DepthCursor] Sphere created as child of " + gameObject.name);
        }

        private void CreateCoordinateLabel()
        {
            var labelGo = new GameObject("CursorWorldCoordinateLabel", typeof(TextMeshPro));
            labelGo.transform.SetParent(transform, false);
            labelGo.transform.localPosition = Vector3.zero;
            labelGo.transform.localRotation = Quaternion.identity;
            labelGo.transform.localScale = Vector3.one;

            _coordinateLabelTransform = labelGo.transform;
            _coordinateLabelText = labelGo.GetComponent<TextMeshPro>();
            _coordinateLabelText.text = string.Empty;
            _coordinateLabelText.fontSize = coordinateLabelFontSize;
            _coordinateLabelText.color = coordinateLabelColor;
            _coordinateLabelText.alignment = TextAlignmentOptions.Center;
            _coordinateLabelText.textWrappingMode = TextWrappingModes.NoWrap;
            _coordinateLabelText.overflowMode = TextOverflowModes.Overflow;
            _coordinateLabelText.outlineColor = coordinateLabelOutlineColor;
            _coordinateLabelText.outlineWidth = coordinateLabelOutlineWidth;

            RectTransform rectTransform = labelGo.GetComponent<RectTransform>();
            if (rectTransform != null)
            {
                rectTransform.sizeDelta = coordinateLabelSize;
            }

            labelGo.SetActive(false);
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

        private void Update()
        {
            if (OVRInput.GetDown(OVRInput.Button.One, OVRInput.Controller.RTouch))
                ToggleMode();
        }

        /// <summary>
        /// 时序平滑 + 丢失容忍的射线更新。
        /// - 命中时：指数平滑位置，防止微小抖动
        /// - 丢失时：保持最后位置 N 帧（容忍偶发 miss），超时后平滑收回到远端
        /// - 颜色平滑过渡，无闪烁
        /// </summary>
        public bool UpdateCursor(Ray ray)
        {
            if (!_interactionEnabled)
            {
                Hide();
                return false;
            }

            if (raycastManager == null || _cursorTransform == null) return false;

            bool rawHit = raycastManager.Raycast(ray, out var hit, maxDistance);
            Vector3 targetPos;
            Vector3 targetNormal;
            Color targetColor;
            bool targetHitting;

            if (rawHit)
            {
                targetPos = hit.point;
                targetNormal = hit.normal != Vector3.zero ? hit.normal : Vector3.up;
                targetColor = GetActiveHitColor();
                targetHitting = true;
                _consecutiveMisses = 0;
            }
            else
            {
                _consecutiveMisses++;
                if (_consecutiveMisses <= missToleranceFrames && _initialized)
                {
                    // Hold last known position — tolerate brief misses
                    targetPos = _smoothPosition;
                    targetNormal = _smoothNormal;
                    targetColor = _smoothColor;
                    targetHitting = IsHitting; // maintain previous state
                }
                else
                {
                    // Real miss — move to far position
                    targetPos = ray.origin + ray.direction * maxDistance;
                    targetNormal = Vector3.up;
                    targetColor = missColor;
                    targetHitting = false;
                }
            }

            // Initialize on first frame
            if (!_initialized)
            {
                _smoothPosition = targetPos;
                _smoothNormal = targetNormal;
                _smoothColor = targetColor;
                _initialized = true;
            }
            else
            {
                // Exponential smoothing
                float t = 1f - Mathf.Exp(-positionSmoothSpeed * Time.deltaTime);
                _smoothPosition = Vector3.Lerp(_smoothPosition, targetPos, t);
                _smoothNormal = Vector3.Slerp(_smoothNormal, targetNormal, t).normalized;
            }

            // Smooth color
            _smoothColor = Color.Lerp(_smoothColor, targetColor, Mathf.Clamp01(colorSmoothSpeed * Time.deltaTime));

            // Apply
            bool wasHitting = IsHitting;
            IsHitting = targetHitting;
            HitPoint = _smoothPosition;
            HitNormal = _smoothNormal;
            HitDistance = Vector3.Distance(ray.origin, _smoothPosition);

            _cursorTransform.position = _smoothPosition;
            if (_smoothNormal != Vector3.zero)
                _cursorTransform.rotation = Quaternion.LookRotation(_smoothNormal, Vector3.up);

            SetCursorColor(_smoothColor);
            _cursorTransform.gameObject.SetActive(true);
            UpdateCoordinateLabel(IsHitting, HitPoint);

            if (wasHitting != IsHitting)
                OnHitChanged?.Invoke(IsHitting, HitPoint, HitNormal);

            return IsHitting;
        }

        private void LateUpdate()
        {
            BillboardCoordinateLabel();
        }

        private void UpdateCoordinateLabel(bool visible, Vector3 worldPoint)
        {
            if (_coordinateLabelText == null || _coordinateLabelTransform == null) return;

            bool shouldShow = showWorldCoordinateLabel && visible;
            _coordinateLabelTransform.gameObject.SetActive(shouldShow);
            if (!shouldShow) return;

            _coordinateLabelTransform.position = worldPoint + coordinateLabelOffset;
            _coordinateLabelText.text = FormatCoordinateLabel(worldPoint);
            BillboardCoordinateLabel();
        }

        private void BillboardCoordinateLabel()
        {
            if (_coordinateLabelTransform == null || !_coordinateLabelTransform.gameObject.activeSelf) return;

            Camera camera = ResolveLabelCamera();
            if (camera == null) return;

            _coordinateLabelTransform.LookAt(
                _coordinateLabelTransform.position + camera.transform.forward,
                camera.transform.up);
        }

        private Camera ResolveLabelCamera()
        {
            if (_labelCamera == null)
                _labelCamera = Camera.main;
            return _labelCamera;
        }

        private static string FormatCoordinateLabel(Vector3 worldPoint)
        {
            Vector3 roomPoint = RoomSpatialAnchorManager.WorldToRoomPoint(worldPoint);
            return string.Format(
                CultureInfo.InvariantCulture,
                "X: {0:+0.000;-0.000;0.000} m\nY: {1:+0.000;-0.000;0.000} m\nZ: {2:+0.000;-0.000;0.000} m",
                roomPoint.x,
                roomPoint.y,
                roomPoint.z);
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
            if (!_interactionEnabled) return;

            if (_cursorTransform != null) _cursorTransform.gameObject.SetActive(true);
            UpdateCoordinateLabel(IsHitting, HitPoint);
        }

        public void Hide()
        {
            if (_cursorTransform != null) _cursorTransform.gameObject.SetActive(false);
            if (_coordinateLabelTransform != null) _coordinateLabelTransform.gameObject.SetActive(false);
        }

        public void ToggleMode()
        {
            SetMode(CurrentMode == ProbeEditMode.Add ? ProbeEditMode.Del : ProbeEditMode.Add);
        }

        public void SetMode(ProbeEditMode mode)
        {
            if (CurrentMode == mode) return;

            CurrentMode = mode;
            _smoothColor = IsHitting ? GetActiveHitColor() : missColor;
            SetCursorColor(_smoothColor);
            OnModeChanged?.Invoke(CurrentMode);
            Debug.Log("[DepthCursor] Mode switched to " + CurrentMode);
        }

        public void SetInteractionEnabled(bool isEnabled)
        {
            _interactionEnabled = isEnabled;
            if (isEnabled) return;

            IsHitting = false;
            _consecutiveMisses = 0;
            Hide();
        }

        public Vector3 GetHitPoint()
        {
            return HitPoint;
        }

        private Color GetActiveHitColor()
        {
            return CurrentMode == ProbeEditMode.Add ? addHitColor : delHitColor;
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
