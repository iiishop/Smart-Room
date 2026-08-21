using System;
using System.Globalization;
using SmartRoom.Interaction;
using TMPro;
using UnityEngine;

namespace SmartRoom.UI
{
    public sealed class RightHandRadialMenu : MonoBehaviour
    {
        private const string DefaultObjectName = "RightHandRadialMenu";
        private const int SlotCount = 6;

        [Header("References")]
        [SerializeField] private Camera uiCamera;
        [SerializeField] private ControllerRaycaster controllerRaycaster;

        [Header("Input")]
        [SerializeField] private float holdSeconds = 0.3f;
        [SerializeField] private float grabPressThreshold = 0.72f;
        [SerializeField] private float grabReleaseThreshold = 0.35f;

        [Header("Layout")]
        [SerializeField] private float menuDistance = 0.28f;
        [SerializeField] private float menuRadius = 0.16f;
        [SerializeField] private float selectionRadius = 0.07f;
        [SerializeField] private float slotRadius = 0.025f;

        [Header("Colors")]
        [SerializeField] private Color ringColor = new Color(1f, 1f, 1f, 0.65f);
        [SerializeField] private Color activeColor = new Color(0.12f, 0.48f, 1f, 1f);
        [SerializeField] private Color inactiveColor = new Color(0.24f, 0.26f, 0.3f, 0.85f);
        [SerializeField] private Color selectedColor = new Color(0.2f, 1f, 0.55f, 1f);
        [SerializeField] private Color cursorColor = new Color(1f, 0.88f, 0.2f, 1f);

        private readonly Vector3[] _slotLocalPositions = new Vector3[SlotCount];
        private readonly Transform[] _slotTransforms = new Transform[SlotCount];
        private readonly MeshRenderer[] _slotRenderers = new MeshRenderer[SlotCount];
        private readonly Material[] _slotMaterials = new Material[SlotCount];
        private readonly TextMeshPro[] _labels = new TextMeshPro[SlotCount];

        private Material _ringMaterial;
        private Material _cursorMaterial;
        private Transform _visualRoot;
        private Transform _cursorTransform;
        private bool _built;
        private bool _grabHeld;
        private bool _menuVisible;
        private float _grabStartTime;
        private int _selectedIndex = -1;

        private static RightHandRadialMenu _instance;
        private static readonly int BaseColorId = Shader.PropertyToID("_BaseColor");
        private static readonly int ColorId = Shader.PropertyToID("_Color");

        public static RightHandRadialMenu EnsureExists(Camera camera = null)
        {
            if (_instance != null)
            {
                _instance.SetCamera(camera);
                return _instance;
            }

            RightHandRadialMenu existing = FindFirstObjectByType<RightHandRadialMenu>();
            if (existing != null)
            {
                _instance = existing;
                existing.SetCamera(camera);
                return existing;
            }

            GameObject menuObject = GameObject.Find(DefaultObjectName);
            if (menuObject == null)
                menuObject = new GameObject(DefaultObjectName);

            RightHandRadialMenu menu = menuObject.GetComponent<RightHandRadialMenu>();
            if (menu == null)
                menu = menuObject.AddComponent<RightHandRadialMenu>();

            menu.SetCamera(camera);
            return menu;
        }

        public void SetCamera(Camera camera)
        {
            if (camera != null)
                uiCamera = camera;
        }

        private void Awake()
        {
            _instance = this;
            ResolveReferences();
            Build();
            HideMenu();
        }

        private void OnDestroy()
        {
            if (_instance == this)
                _instance = null;

            DestroyMaterial(_ringMaterial);
            DestroyMaterial(_cursorMaterial);
            for (int i = 0; i < _slotMaterials.Length; i++)
                DestroyMaterial(_slotMaterials[i]);
        }

        private void Update()
        {
            ResolveReferences();

            if (!CanUseMenu())
            {
                ResetInput();
                HideMenu();
                return;
            }

            float grabValue = OVRInput.Get(OVRInput.RawAxis1D.RHandTrigger);
            bool pressed = grabValue >= grabPressThreshold;
            bool released = _grabHeld && grabValue <= grabReleaseThreshold;

            if (!_menuVisible)
            {
                if (pressed)
                {
                    if (!_grabHeld)
                    {
                        _grabHeld = true;
                        _grabStartTime = Time.time;
                    }
                    else if (Time.time - _grabStartTime >= holdSeconds)
                    {
                        ShowMenu();
                    }
                }
                else
                {
                    ResetInput();
                }

                return;
            }

            UpdateSelection();

            if (released)
            {
                int releasedIndex = _selectedIndex;
                HideMenu();
                ResetInput();

                if (releasedIndex == 0)
                    RoomCoordinateSystemPanel.OpenSelectionPanel();
                else if (releasedIndex == 1)
                    DeviceAnnotationController.StartNewObjectFromMenu();
                else if (releasedIndex == 2)
                    RoomCaptureSession.ForceNextCapture();
            }
        }

        private bool CanUseMenu()
        {
            return RoomCoordinateSystemPanel.HasEnteredRoom
                   && !RoomCoordinateSystemPanel.IsPanelVisible
                   && !DeviceArchivePanel.IsPanelVisible
                   && !DeviceBindingPanel.IsPanelVisible
                   && controllerRaycaster != null;
        }

        private void ResolveReferences()
        {
            if (uiCamera == null)
                uiCamera = Camera.main;

            if (controllerRaycaster == null)
                controllerRaycaster = FindFirstObjectByType<ControllerRaycaster>();
        }

        private void Build()
        {
            if (_built) return;

            gameObject.name = DefaultObjectName;

            GameObject visualRootObject = new GameObject("VisualRoot");
            visualRootObject.transform.SetParent(transform, false);
            _visualRoot = visualRootObject.transform;

            LineRenderer ring = visualRootObject.AddComponent<LineRenderer>();
            ring.useWorldSpace = false;
            ring.loop = true;
            ring.positionCount = 96;
            ring.startWidth = 0.004f;
            ring.endWidth = 0.004f;
            ring.material = CreateMaterial(ringColor);
            _ringMaterial = ring.material;

            for (int i = 0; i < ring.positionCount; i++)
            {
                float angle = 2f * Mathf.PI * i / ring.positionCount;
                ring.SetPosition(i, new Vector3(Mathf.Cos(angle) * menuRadius, Mathf.Sin(angle) * menuRadius, 0f));
            }

            for (int i = 0; i < SlotCount; i++)
            {
                float angle = Mathf.PI * 0.5f - (2f * Mathf.PI * i / SlotCount);
                Vector3 localPosition = new Vector3(Mathf.Cos(angle) * menuRadius, Mathf.Sin(angle) * menuRadius, 0f);
                _slotLocalPositions[i] = localPosition;

                GameObject slot = GameObject.CreatePrimitive(PrimitiveType.Sphere);
                slot.name = i == 0 ? "Slot_RoomPanel" : i == 1 ? "Slot_NewObject" : i == 2 ? "Slot_NewImage" : "Slot_Empty_" + i;
                slot.transform.SetParent(_visualRoot, false);
                slot.transform.localPosition = localPosition;
                slot.transform.localScale = Vector3.one * (slotRadius * 2f);

                Collider slotCollider = slot.GetComponent<Collider>();
                if (slotCollider != null)
                    Destroy(slotCollider);

                MeshRenderer renderer = slot.GetComponent<MeshRenderer>();
                Material material = CreateMaterial(IsActiveSlot(i) ? activeColor : inactiveColor);
                renderer.sharedMaterial = material;
                _slotTransforms[i] = slot.transform;
                _slotRenderers[i] = renderer;
                _slotMaterials[i] = material;

                if (i == 0)
                    _labels[i] = CreateLabel("RoomsLabel", "Rooms", localPosition + new Vector3(0f, 0.042f, 0f), activeColor);
                else if (i == 1)
                    _labels[i] = CreateLabel("NewObjectLabel", "New Obj", localPosition + new Vector3(0f, 0.042f, 0f), activeColor);
                else if (i == 2)
                    _labels[i] = CreateLabel("NewImageLabel", "New Img", localPosition + new Vector3(0f, 0.042f, 0f), activeColor);
            }

            GameObject cursor = GameObject.CreatePrimitive(PrimitiveType.Sphere);
            cursor.name = "HandProjection";
            cursor.transform.SetParent(_visualRoot, false);
            cursor.transform.localScale = Vector3.one * 0.018f;
            Collider cursorCollider = cursor.GetComponent<Collider>();
            if (cursorCollider != null)
                Destroy(cursorCollider);
            _cursorMaterial = CreateMaterial(cursorColor);
            cursor.GetComponent<MeshRenderer>().sharedMaterial = _cursorMaterial;
            _cursorTransform = cursor.transform;

            _built = true;
        }

        private TextMeshPro CreateLabel(string name, string text, Vector3 localPosition, Color color)
        {
            GameObject labelObject = new GameObject(name, typeof(TextMeshPro));
            labelObject.transform.SetParent(_visualRoot, false);
            labelObject.transform.localPosition = localPosition;
            labelObject.transform.localScale = Vector3.one;

            TextMeshPro label = labelObject.GetComponent<TextMeshPro>();
            label.text = text;
            label.fontSize = 0.045f;
            label.color = color;
            label.alignment = TextAlignmentOptions.Center;
            label.textWrappingMode = TextWrappingModes.NoWrap;
            label.overflowMode = TextOverflowModes.Overflow;
            label.outlineColor = Color.black;
            label.outlineWidth = 0.25f;

            RectTransform rect = labelObject.GetComponent<RectTransform>();
            if (rect != null)
                rect.sizeDelta = new Vector2(0.24f, 0.08f);

            return label;
        }

        private void ShowMenu()
        {
            Ray ray = controllerRaycaster.GetRay();
            if (ray.direction.sqrMagnitude < 0.0001f)
                return;

            Vector3 menuPosition = ray.origin + ray.direction.normalized * menuDistance;
            Quaternion menuRotation = Quaternion.LookRotation(ResolveMenuForward(menuPosition), Vector3.up);
            transform.SetPositionAndRotation(menuPosition, menuRotation);
            if (_visualRoot != null)
                _visualRoot.gameObject.SetActive(true);
            _menuVisible = true;
            _selectedIndex = -1;
            UpdateSelection();
        }

        private Vector3 ResolveMenuForward(Vector3 menuPosition)
        {
            if (uiCamera == null)
                return Vector3.forward;

            Vector3 headToMenu = menuPosition - uiCamera.transform.position;
            return headToMenu.sqrMagnitude > 0.0001f ? headToMenu.normalized : uiCamera.transform.forward;
        }

        private void HideMenu()
        {
            _menuVisible = false;
            _selectedIndex = -1;
            if (_visualRoot != null)
                _visualRoot.gameObject.SetActive(false);
        }

        private void ResetInput()
        {
            _grabHeld = false;
            _grabStartTime = 0f;
        }

        private void UpdateSelection()
        {
            if (!_menuVisible || controllerRaycaster == null)
                return;

            Ray ray = controllerRaycaster.GetRay();
            Vector3 localHand = transform.InverseTransformPoint(ray.origin);
            Vector2 handPoint = new Vector2(localHand.x, localHand.y);

            if (_cursorTransform != null)
                _cursorTransform.localPosition = new Vector3(handPoint.x, handPoint.y, 0f);

            int closestIndex = -1;
            float closestDistance = selectionRadius;
            for (int i = 0; i < SlotCount; i++)
            {
                Vector2 slotPoint = new Vector2(_slotLocalPositions[i].x, _slotLocalPositions[i].y);
                float distance = Vector2.Distance(handPoint, slotPoint);
                if (distance <= closestDistance)
                {
                    closestDistance = distance;
                    closestIndex = i;
                }
            }

            _selectedIndex = closestIndex;
            UpdateVisualState();
            BillboardLabels();
        }

        private void UpdateVisualState()
        {
            for (int i = 0; i < SlotCount; i++)
            {
                Color baseColor = IsActiveSlot(i) ? activeColor : inactiveColor;
                Color color = i == _selectedIndex ? selectedColor : baseColor;
                SetMaterialColor(_slotMaterials[i], color);

                if (_slotTransforms[i] != null)
                    _slotTransforms[i].localScale = Vector3.one * (slotRadius * 2f * (i == _selectedIndex ? 1.35f : 1f));
            }
        }

        private void BillboardLabels()
        {
            if (uiCamera == null) return;

            for (int i = 0; i < _labels.Length; i++)
            {
                if (_labels[i] == null) continue;

                Transform labelTransform = _labels[i].transform;
                labelTransform.LookAt(labelTransform.position + uiCamera.transform.forward, uiCamera.transform.up);
            }
        }

        private Material CreateMaterial(Color color)
        {
            Shader shader = Shader.Find("Universal Render Pipeline/Unlit");
            if (shader == null) shader = Shader.Find("Unlit/Color");

            Material material = new Material(shader);
            SetMaterialColor(material, color);
            return material;
        }

        private static void SetMaterialColor(Material material, Color color)
        {
            if (material == null) return;

            material.SetColor(BaseColorId, color);
            material.SetColor(ColorId, color);
            material.color = color;
        }

        private static void DestroyMaterial(Material material)
        {
            if (material != null)
                Destroy(material);
        }

        private static bool IsActiveSlot(int index)
        {
            return index == 0 || index == 1 || index == 2;
        }
    }

    public static class RoomObjectSession
    {
        private static string _currentObjectId = string.Empty;
        private static string _currentEditSessionId = string.Empty;
        private static bool _editingSavedObject;

        public static string CurrentObjectId
        {
            get
            {
                if (string.IsNullOrWhiteSpace(_currentObjectId))
                    StartNewObject();
                return _currentObjectId;
            }
        }

        public static bool HasCurrentObject => !string.IsNullOrWhiteSpace(_currentObjectId);
        public static string CurrentEditSessionId => _currentEditSessionId;
        public static bool IsEditingSavedObject => _editingSavedObject;

        public static string StartNewObject()
        {
            string timestamp = DateTime.UtcNow.ToString("yyyyMMdd_HHmmss", CultureInfo.InvariantCulture);
            _currentObjectId = "object_" + timestamp;
            _currentEditSessionId = string.Empty;
            _editingSavedObject = false;
            PromptPointMarkerManager.ClearMarkers();
            Debug.Log("[RoomObjectSession] Started " + _currentObjectId);
            return _currentObjectId;
        }

        public static void EnterSavedObject(string objectId, string editSessionId)
        {
            if (string.IsNullOrWhiteSpace(objectId))
                return;

            _currentObjectId = objectId;
            _currentEditSessionId = editSessionId ?? string.Empty;
            _editingSavedObject = true;
            Debug.Log("[RoomObjectSession] Editing saved object " + _currentObjectId);
        }

        public static void SetEditSession(string editSessionId)
        {
            _currentEditSessionId = editSessionId ?? string.Empty;
            _editingSavedObject = !string.IsNullOrWhiteSpace(_currentEditSessionId);
        }

        public static void Reset()
        {
            _currentObjectId = string.Empty;
            _currentEditSessionId = string.Empty;
            _editingSavedObject = false;
        }
    }

    public static class RoomCaptureSession
    {
        private static bool _forceNextCapture;

        public static void ForceNextCapture()
        {
            _forceNextCapture = true;
            Debug.Log("[RoomCaptureSession] Next point prompt will capture a new image.");
        }

        public static bool ConsumeForceNextCapture()
        {
            bool value = _forceNextCapture;
            _forceNextCapture = false;
            return value;
        }

        public static void Reset()
        {
            _forceNextCapture = false;
        }
    }
}
