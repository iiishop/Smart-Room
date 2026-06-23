using System;
using System.Collections.Generic;
using System.Globalization;
using SmartRoom.Interaction;
using SmartRoom.Networking;
using TMPro;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.InputSystem.UI;
using UnityEngine.UI;

namespace SmartRoom.UI
{
    public sealed class RoomCoordinateSystemPanel : MonoBehaviour
    {
        private const string DefaultObjectName = "RoomCoordinateSystemPanel";
        private const string StoreKey = "SmartRoom.RoomCoordinateSystems.v1";
        private const float PanelWidth = 720f;
        private const float PanelHeight = 520f;
        private const float RowHeight = 58f;
        private const float RowGap = 8f;

        [Header("Follow")]
        [SerializeField] private Transform headTarget;
        [SerializeField] private Vector3 localOffset = new Vector3(0f, -0.1f, 1.05f);
        [SerializeField] private float minFollowSpeed = 1.2f;
        [SerializeField] private float followSpeedPerMeter = 8f;
        [SerializeField] private float maxFollowSpeed = 18f;
        [SerializeField] private float minRotationSpeed = 2.5f;
        [SerializeField] private float rotationSpeedPerDegree = 0.12f;
        [SerializeField] private float maxRotationSpeed = 18f;

        [Header("Canvas")]
        [SerializeField] private Camera uiCamera;
        [SerializeField] private ControllerRaycaster controllerRaycaster;
        [SerializeField] private float canvasScale = 0.0012f;

        private readonly Dictionary<string, GameObject> _rowMenus = new Dictionary<string, GameObject>();
        private readonly List<RaycastResult> _uiRaycastResults = new List<RaycastResult>();
        private RoomCoordinateDatabase _database = new RoomCoordinateDatabase();
        private RectTransform _canvasRoot;
        private RectTransform _listRoot;
        private GraphicRaycaster _graphicRaycaster;
        private TextMeshProUGUI _statusText;
        private TextMeshProUGUI _emptyText;
        private GameObject _renameDialog;
        private TMP_InputField _renameInput;
        private string _renameRoomId;
        private bool _initializedPose;
        private bool _hasEnteredRoom;
        private bool _panelVisible = true;
        private PointerEventData _pointerEventData;
        private EventSystem _pointerEventSystem;
        private GameObject _hoveredObject;
        private GameObject _pressedObject;

        public static bool IsUiBlockingSceneInput { get; private set; }
        public static bool IsPanelVisible => _instance != null && _instance._panelVisible;
        public static bool HasEnteredRoom => _instance != null && _instance._hasEnteredRoom;
        public static string CurrentRoomId =>
            _instance != null && _instance._hasEnteredRoom ? _instance._database.selected_id : string.Empty;
        public static string CurrentRoomName
        {
            get
            {
                if (_instance == null || !_instance._hasEnteredRoom) return string.Empty;
                RoomCoordinateRecord record = _instance.FindRoom(_instance._database.selected_id);
                return record != null ? record.name : string.Empty;
            }
        }

        private static RoomCoordinateSystemPanel _instance;

        public static RoomCoordinateSystemPanel EnsureExists(Camera camera = null)
        {
            RoomCoordinateSystemPanel existing = FindFirstObjectByType<RoomCoordinateSystemPanel>();
            if (existing != null)
            {
                existing.SetCamera(camera);
                return existing;
            }

            GameObject panelObject = GameObject.Find(DefaultObjectName);
            if (panelObject == null)
                panelObject = new GameObject(DefaultObjectName);

            RoomCoordinateSystemPanel panel = panelObject.GetComponent<RoomCoordinateSystemPanel>();
            if (panel == null)
                panel = panelObject.AddComponent<RoomCoordinateSystemPanel>();

            panel.SetCamera(camera);
            return panel;
        }

        public static void OpenSelectionPanel()
        {
            RoomCoordinateSystemPanel panel = EnsureExists(Camera.main);
            panel.ShowSelectionPanel();
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
            LoadRooms();
            BuildUi();
            RefreshList();
            SetSceneInteractionEnabled(false);
            RightHandRadialMenu.EnsureExists(uiCamera);
        }

        private void LateUpdate()
        {
            ResolveReferences();

            if (!_panelVisible)
            {
                IsUiBlockingSceneInput = false;
                return;
            }

            FollowHeadWithLag();
            UpdateControllerUiInput();
        }

        private void OnDisable()
        {
            ClearHover();
            _pressedObject = null;
            IsUiBlockingSceneInput = false;
        }

        private void OnDestroy()
        {
            if (_instance == this)
                _instance = null;
        }

        private void ResolveReferences()
        {
            if (uiCamera == null)
                uiCamera = Camera.main;

            if (headTarget == null && uiCamera != null)
                headTarget = uiCamera.transform;

            if (controllerRaycaster == null)
                controllerRaycaster = FindFirstObjectByType<ControllerRaycaster>();
        }

        private void ShowSelectionPanel()
        {
            ResolveReferences();
            LoadRooms();
            _hasEnteredRoom = false;
            _panelVisible = true;
            _initializedPose = false;

            if (_canvasRoot != null)
                _canvasRoot.gameObject.SetActive(true);

            CloseRenameDialog();
            RefreshList();
            SetSceneInteractionEnabled(false);
            RoomObjectSession.Reset();
            RoomCaptureSession.Reset();
        }

        private void HidePanel()
        {
            _panelVisible = false;
            ClearHover();
            _pressedObject = null;
            IsUiBlockingSceneInput = false;

            if (_canvasRoot != null)
                _canvasRoot.gameObject.SetActive(false);
        }

        private void FollowHeadWithLag()
        {
            if (headTarget == null) return;

            Vector3 targetPosition = headTarget.TransformPoint(localOffset);
            if (!_initializedPose)
            {
                transform.position = targetPosition;
                _initializedPose = true;
            }
            else
            {
                float distance = Vector3.Distance(transform.position, targetPosition);
                float speed = Mathf.Clamp(minFollowSpeed + distance * followSpeedPerMeter, minFollowSpeed, maxFollowSpeed);
                float t = 1f - Mathf.Exp(-speed * Time.deltaTime);
                transform.position = Vector3.Lerp(transform.position, targetPosition, t);
            }

            Vector3 headToPanel = transform.position - headTarget.position;
            if (headToPanel.sqrMagnitude < 0.0001f) return;

            Quaternion targetRotation = Quaternion.LookRotation(headToPanel.normalized, Vector3.up);
            float angle = Quaternion.Angle(transform.rotation, targetRotation);
            float rotationSpeed = Mathf.Clamp(minRotationSpeed + angle * rotationSpeedPerDegree, minRotationSpeed, maxRotationSpeed);
            float rt = 1f - Mathf.Exp(-rotationSpeed * Time.deltaTime);
            transform.rotation = Quaternion.Slerp(transform.rotation, targetRotation, rt);
        }

        private void UpdateControllerUiInput()
        {
            IsUiBlockingSceneInput = false;

            if (!TryRaycastPanel(out GameObject currentTarget, out PointerEventData pointerData))
            {
                ClearHover();
                if (OVRInput.GetUp(OVRInput.RawButton.RIndexTrigger))
                    ReleasePressedObject(pointerData, null);
                return;
            }

            IsUiBlockingSceneInput = true;
            UpdateHover(currentTarget, pointerData);

            if (OVRInput.GetDown(OVRInput.RawButton.RIndexTrigger))
            {
                _pressedObject = ExecuteEvents.ExecuteHierarchy(currentTarget, pointerData, ExecuteEvents.pointerDownHandler);
                if (_pressedObject == null)
                    _pressedObject = ExecuteEvents.GetEventHandler<IPointerClickHandler>(currentTarget);
            }

            if (OVRInput.GetUp(OVRInput.RawButton.RIndexTrigger))
                ReleasePressedObject(pointerData, currentTarget);
        }

        private bool TryRaycastPanel(out GameObject target, out PointerEventData pointerData)
        {
            target = null;
            pointerData = null;

            if (_canvasRoot == null || _graphicRaycaster == null || uiCamera == null || controllerRaycaster == null)
                return false;

            Ray ray = controllerRaycaster.GetRay();
            if (ray.direction.sqrMagnitude < 0.0001f)
                return false;

            Plane panelPlane = new Plane(_canvasRoot.forward, _canvasRoot.position);
            if (!panelPlane.Raycast(ray, out float enter) || enter < 0f)
                return false;

            Vector3 worldPoint = ray.GetPoint(enter);
            Vector3 localPoint = _canvasRoot.InverseTransformPoint(worldPoint);
            if (!_canvasRoot.rect.Contains(new Vector2(localPoint.x, localPoint.y)))
                return false;

            Vector3 screenPoint = uiCamera.WorldToScreenPoint(worldPoint);
            if (screenPoint.z < 0f)
                return false;

            EventSystem eventSystem = EventSystem.current;
            if (eventSystem == null)
                return false;

            if (_pointerEventData == null || _pointerEventSystem != eventSystem)
            {
                _pointerEventData = new PointerEventData(eventSystem);
                _pointerEventSystem = eventSystem;
            }

            _pointerEventData.Reset();
            _pointerEventData.position = new Vector2(screenPoint.x, screenPoint.y);
            _pointerEventData.button = PointerEventData.InputButton.Left;
            _pointerEventData.pointerId = -101;

            _uiRaycastResults.Clear();
            _graphicRaycaster.Raycast(_pointerEventData, _uiRaycastResults);
            if (_uiRaycastResults.Count == 0)
                return false;

            _pointerEventData.pointerCurrentRaycast = _uiRaycastResults[0];
            target = _uiRaycastResults[0].gameObject;
            pointerData = _pointerEventData;
            return target != null;
        }

        private void UpdateHover(GameObject currentTarget, PointerEventData pointerData)
        {
            if (_hoveredObject == currentTarget)
                return;

            ClearHover();
            _hoveredObject = currentTarget;
            ExecuteEvents.ExecuteHierarchy(_hoveredObject, pointerData, ExecuteEvents.pointerEnterHandler);
        }

        private void ClearHover()
        {
            if (_hoveredObject == null || _pointerEventData == null)
                return;

            ExecuteEvents.ExecuteHierarchy(_hoveredObject, _pointerEventData, ExecuteEvents.pointerExitHandler);
            _hoveredObject = null;
        }

        private void ReleasePressedObject(PointerEventData pointerData, GameObject currentTarget)
        {
            if (_pressedObject == null || pointerData == null)
            {
                _pressedObject = null;
                return;
            }

            ExecuteEvents.Execute(_pressedObject, pointerData, ExecuteEvents.pointerUpHandler);

            GameObject pressedClickHandler = ExecuteEvents.GetEventHandler<IPointerClickHandler>(_pressedObject);
            GameObject currentClickHandler = currentTarget != null
                ? ExecuteEvents.GetEventHandler<IPointerClickHandler>(currentTarget)
                : null;

            if (pressedClickHandler != null && pressedClickHandler == currentClickHandler)
                ExecuteEvents.Execute(pressedClickHandler, pointerData, ExecuteEvents.pointerClickHandler);

            _pressedObject = null;
        }

        private void LoadRooms()
        {
            string json = PlayerPrefs.GetString(StoreKey, string.Empty);
            if (!string.IsNullOrWhiteSpace(json))
            {
                try
                {
                    _database = JsonUtility.FromJson<RoomCoordinateDatabase>(json) ?? new RoomCoordinateDatabase();
                }
                catch (Exception ex)
                {
                    Debug.LogWarning("[RoomCoordinateSystemPanel] Failed to parse room database: " + ex.Message);
                    _database = new RoomCoordinateDatabase();
                }
            }

            if (_database.rooms == null)
                _database.rooms = new List<RoomCoordinateRecord>();
        }

        private void SaveRooms()
        {
            PlayerPrefs.SetString(StoreKey, JsonUtility.ToJson(_database));
            PlayerPrefs.Save();
        }

        private void BuildUi()
        {
            if (_canvasRoot != null) return;

            EnsureEventSystem();

            var canvasObject = new GameObject("RoomCoordinateCanvas",
                typeof(RectTransform), typeof(Canvas), typeof(CanvasScaler), typeof(GraphicRaycaster), typeof(TrackedDeviceRaycaster));
            canvasObject.transform.SetParent(transform, false);

            _canvasRoot = canvasObject.GetComponent<RectTransform>();
            _canvasRoot.sizeDelta = new Vector2(PanelWidth, PanelHeight);
            _canvasRoot.localScale = Vector3.one * canvasScale;

            Canvas canvas = canvasObject.GetComponent<Canvas>();
            canvas.renderMode = RenderMode.WorldSpace;
            canvas.worldCamera = uiCamera;
            _graphicRaycaster = canvasObject.GetComponent<GraphicRaycaster>();

            CanvasScaler scaler = canvasObject.GetComponent<CanvasScaler>();
            scaler.dynamicPixelsPerUnit = 1000f;
            scaler.referencePixelsPerUnit = 100f;

            Image background = canvasObject.AddComponent<Image>();
            background.color = new Color(0.035f, 0.04f, 0.05f, 0.88f);

            TextMeshProUGUI title = CreateText(_canvasRoot, "Title", "Coordinate Systems", 34f, FontStyles.Bold, TextAlignmentOptions.Left);
            SetTopLeft(title.rectTransform, 28f, 24f, 430f, 44f);

            Button addButton = CreateButton(_canvasRoot, "AddRoomButton", "+", new Color(0.16f, 0.46f, 0.86f, 0.95f));
            SetTopRight((RectTransform)addButton.transform, 24f, 20f, 58f, 48f);
            addButton.onClick.AddListener(AddRoom);

            _statusText = CreateText(_canvasRoot, "Status", string.Empty, 20f, FontStyles.Normal, TextAlignmentOptions.Left);
            SetTopLeft(_statusText.rectTransform, 28f, 74f, 660f, 72f);

            _listRoot = AddRect(_canvasRoot, "RoomList");
            Stretch(_listRoot, 28f, 28f, 152f, 92f);

            _emptyText = CreateText(_listRoot, "EmptyText", "No saved rooms. Press + to create one.", 24f, FontStyles.Normal, TextAlignmentOptions.Center);
            Stretch(_emptyText.rectTransform, 12f, 12f, 24f, 24f);

            TextMeshProUGUI footer = CreateText(
                _canvasRoot,
                "Footer",
                "Persistent physical origin requires binding this room to a Spatial Anchor.",
                17f,
                FontStyles.Normal,
                TextAlignmentOptions.Left);
            SetBottomLeft(footer.rectTransform, 28f, 24f, 660f, 34f);

            BuildRenameDialog();
        }

        private void BuildRenameDialog()
        {
            _renameDialog = new GameObject("RenameDialog", typeof(RectTransform), typeof(Image));
            _renameDialog.transform.SetParent(_canvasRoot, false);
            RectTransform dialogRect = (RectTransform)_renameDialog.transform;
            dialogRect.anchorMin = new Vector2(0.5f, 0.5f);
            dialogRect.anchorMax = new Vector2(0.5f, 0.5f);
            dialogRect.pivot = new Vector2(0.5f, 0.5f);
            dialogRect.anchoredPosition = Vector2.zero;
            dialogRect.sizeDelta = new Vector2(520f, 220f);
            _renameDialog.GetComponent<Image>().color = new Color(0.08f, 0.09f, 0.11f, 0.98f);

            TextMeshProUGUI title = CreateText(dialogRect, "RenameTitle", "Rename room", 28f, FontStyles.Bold, TextAlignmentOptions.Left);
            SetTopLeft(title.rectTransform, 24f, 20f, 340f, 38f);

            _renameInput = CreateInput(dialogRect, "RenameInput");
            SetTopLeft((RectTransform)_renameInput.transform, 24f, 76f, 472f, 52f);

            Button okButton = CreateButton(dialogRect, "RenameOk", "OK", new Color(0.16f, 0.46f, 0.86f, 0.95f));
            SetBottomRight((RectTransform)okButton.transform, 132f, 22f, 104f, 42f);
            okButton.onClick.AddListener(CommitRename);

            Button cancelButton = CreateButton(dialogRect, "RenameCancel", "Cancel", new Color(0.22f, 0.24f, 0.28f, 0.95f));
            SetBottomRight((RectTransform)cancelButton.transform, 24f, 22f, 104f, 42f);
            cancelButton.onClick.AddListener(CloseRenameDialog);

            _renameDialog.SetActive(false);
        }

        private void RefreshList()
        {
            ClearRows();
            _emptyText.gameObject.SetActive(_database.rooms.Count == 0);
            UpdateStatus();

            float y = 0f;
            for (int i = 0; i < _database.rooms.Count; i++)
            {
                CreateRoomRow(_database.rooms[i], y);
                y += RowHeight + RowGap;
            }
        }

        private void ClearRows()
        {
            _rowMenus.Clear();
            for (int i = _listRoot.childCount - 1; i >= 0; i--)
            {
                Transform child = _listRoot.GetChild(i);
                if (child == _emptyText.transform) continue;
                Destroy(child.gameObject);
            }
        }

        private void CreateRoomRow(RoomCoordinateRecord record, float y)
        {
            var rowObject = new GameObject("RoomRow_" + record.id, typeof(RectTransform), typeof(Image), typeof(Button));
            rowObject.transform.SetParent(_listRoot, false);
            RectTransform rowRect = (RectTransform)rowObject.transform;
            rowRect.anchorMin = new Vector2(0f, 1f);
            rowRect.anchorMax = new Vector2(1f, 1f);
            rowRect.pivot = new Vector2(0.5f, 1f);
            rowRect.offsetMin = new Vector2(0f, -y - RowHeight);
            rowRect.offsetMax = new Vector2(0f, -y);

            bool selected = _hasEnteredRoom && record.id == _database.selected_id;
            rowObject.GetComponent<Image>().color = selected
                ? new Color(0.12f, 0.24f, 0.38f, 0.95f)
                : new Color(0.12f, 0.13f, 0.15f, 0.95f);

            Button rowButton = rowObject.GetComponent<Button>();
            rowButton.onClick.AddListener(() => SelectRoom(record.id));
            ConfigureButtonColors(rowButton, rowObject.GetComponent<Image>().color);

            TextMeshProUGUI nameText = CreateText(rowRect, "Name", record.name, 22f, selected ? FontStyles.Bold : FontStyles.Normal, TextAlignmentOptions.Left);
            SetTopLeft(nameText.rectTransform, 18f, 8f, 520f, 28f);

            string sub = string.IsNullOrWhiteSpace(record.anchor_uuid)
                ? "Local pose only"
                : "Anchor " + record.anchor_uuid;
            TextMeshProUGUI subText = CreateText(rowRect, "Sub", sub, 15f, FontStyles.Normal, TextAlignmentOptions.Left);
            subText.color = new Color(0.72f, 0.77f, 0.82f, 1f);
            SetTopLeft(subText.rectTransform, 18f, 34f, 520f, 20f);

            Button moreButton = CreateButton(rowRect, "More", "...", new Color(0.18f, 0.19f, 0.22f, 0.96f));
            SetTopRight((RectTransform)moreButton.transform, 10f, 9f, 54f, 40f);
            moreButton.onClick.AddListener(() => ToggleRowMenu(record.id));

            GameObject menu = CreateRowMenu(rowRect, record);
            menu.SetActive(false);
            _rowMenus[record.id] = menu;
        }

        private GameObject CreateRowMenu(RectTransform rowRect, RoomCoordinateRecord record)
        {
            var menuObject = new GameObject("Menu_" + record.id, typeof(RectTransform), typeof(Image));
            menuObject.transform.SetParent(rowRect, false);
            RectTransform menuRect = (RectTransform)menuObject.transform;
            menuRect.anchorMin = new Vector2(1f, 0.5f);
            menuRect.anchorMax = new Vector2(1f, 0.5f);
            menuRect.pivot = new Vector2(1f, 0.5f);
            menuRect.anchoredPosition = new Vector2(-72f, 0f);
            menuRect.sizeDelta = new Vector2(208f, 46f);
            menuObject.GetComponent<Image>().color = new Color(0.055f, 0.06f, 0.075f, 0.98f);

            Button renameButton = CreateButton(menuRect, "Rename", "Rename", new Color(0.22f, 0.24f, 0.29f, 0.98f));
            SetTopLeft((RectTransform)renameButton.transform, 6f, 6f, 96f, 34f);
            renameButton.onClick.AddListener(() => OpenRenameDialog(record.id));

            Button deleteButton = CreateButton(menuRect, "Delete", "Delete", new Color(0.48f, 0.12f, 0.12f, 0.98f));
            SetTopRight((RectTransform)deleteButton.transform, 6f, 6f, 96f, 34f);
            deleteButton.onClick.AddListener(() => DeleteRoom(record.id));

            return menuObject;
        }

        private void ToggleRowMenu(string roomId)
        {
            foreach (KeyValuePair<string, GameObject> entry in _rowMenus)
            {
                bool shouldShow = entry.Key == roomId && !entry.Value.activeSelf;
                entry.Value.SetActive(shouldShow);
            }
        }

        private void AddRoom()
        {
            DateTime now = DateTime.Now;
            string timestamp = now.ToString("yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture);

            var record = new RoomCoordinateRecord
            {
                id = Guid.NewGuid().ToString("N"),
                name = timestamp,
                created_at = timestamp,
                px = 0f,
                py = 0f,
                pz = 0f,
                qx = 0f,
                qy = 0f,
                qz = 0f,
                qw = 1f,
                anchor_uuid = string.Empty
            };

            _database.rooms.Add(record);
            _database.selected_id = record.id;
            _hasEnteredRoom = true;
            SaveRooms();
            RefreshList();
            ApplySelectedRoomOrigin();
            RoomObjectSession.StartNewObject();
            if (_hasEnteredRoom)
                HidePanel();
        }

        private void SelectRoom(string roomId)
        {
            _database.selected_id = roomId;
            _hasEnteredRoom = true;
            SaveRooms();
            RefreshList();
            ApplySelectedRoomOrigin();
            RoomObjectSession.StartNewObject();
            if (_hasEnteredRoom)
                HidePanel();
        }

        private void DeleteRoom(string roomId)
        {
            int index = _database.rooms.FindIndex(room => room.id == roomId);
            if (index < 0) return;

            bool removedEnteredRoom = _hasEnteredRoom && _database.selected_id == roomId;
            _database.rooms.RemoveAt(index);
            if (_database.selected_id == roomId)
            {
                _database.selected_id = removedEnteredRoom && _database.rooms.Count > 0
                    ? _database.rooms[0].id
                    : string.Empty;
            }

            if (removedEnteredRoom && _database.rooms.Count == 0)
                _hasEnteredRoom = false;

            SaveRooms();
            RefreshList();

            if (_hasEnteredRoom)
                ApplySelectedRoomOrigin();
            else
                SetSceneInteractionEnabled(false);
        }

        private void OpenRenameDialog(string roomId)
        {
            RoomCoordinateRecord record = FindRoom(roomId);
            if (record == null) return;

            _renameRoomId = roomId;
            _renameInput.text = record.name;
            _renameDialog.SetActive(true);
            _renameInput.ActivateInputField();
        }

        private void CommitRename()
        {
            RoomCoordinateRecord record = FindRoom(_renameRoomId);
            if (record == null)
            {
                CloseRenameDialog();
                return;
            }

            string nextName = (_renameInput.text ?? string.Empty).Trim();
            if (!string.IsNullOrWhiteSpace(nextName))
                record.name = nextName;

            SaveRooms();
            CloseRenameDialog();
            RefreshList();
            if (_hasEnteredRoom)
                ApplySelectedRoomOrigin();
        }

        private void CloseRenameDialog()
        {
            _renameRoomId = string.Empty;
            if (_renameDialog != null)
                _renameDialog.SetActive(false);
        }

        private RoomCoordinateRecord FindRoom(string roomId)
        {
            if (string.IsNullOrWhiteSpace(roomId)) return null;
            return _database.rooms.Find(room => room.id == roomId);
        }

        private void ApplySelectedRoomOrigin()
        {
            if (!_hasEnteredRoom)
            {
                SetSceneInteractionEnabled(false);
                return;
            }

            RoomCoordinateRecord selected = FindRoom(_database.selected_id);
            if (selected == null)
            {
                _hasEnteredRoom = false;
                SetSceneInteractionEnabled(false);
                return;
            }

            WorldOriginReference.EnsureExists(uiCamera).SetOriginPose(selected.ToPose(), selected.name);
            SetSceneInteractionEnabled(true);
        }

        private void UpdateStatus()
        {
            if (!_hasEnteredRoom)
            {
                _statusText.text = "Current: not selected\nSelect a saved room or press + to enter.";
                return;
            }

            RoomCoordinateRecord selected = FindRoom(_database.selected_id);
            if (selected == null)
            {
                _statusText.text = "Current: none\nSelect a saved room or press + to create one.";
                return;
            }

            string persistence = string.IsNullOrWhiteSpace(selected.anchor_uuid)
                ? "local pose only; not relocalized across sessions"
                : "spatial anchor bound";
            _statusText.text = "Current: " + selected.name + "\nOrigin: " + persistence;
        }

        private static void SetSceneInteractionEnabled(bool isEnabled)
        {
            SetEnabledOnAll<RgbStreamModule>(isEnabled);
            SetEnabledOnAll<DepthStreamModule>(isEnabled);
            SetEnabledOnAll<RaycastQueryModule>(isEnabled);
            SetEnabledOnAll<VisionReceiverModule>(isEnabled);
            SetEnabledOnAll<DepthFrameSampler>(isEnabled);
            SetEnabledOnAll<TriggerDepthProbe>(isEnabled);
            SetEnabledOnAll<ObjectGrabber>(isEnabled, activateGameObjectWhenEnabled: true);

            DepthCursor depthCursor = FindFirstObjectByType<DepthCursor>();
            if (depthCursor != null)
                depthCursor.SetInteractionEnabled(isEnabled);

            if (!isEnabled)
            {
                PromptPointMarkerManager.ClearMarkers();
                WorldOriginReference.DestroyExisting();
            }
        }

        private static void SetEnabledOnAll<T>(bool isEnabled, bool activateGameObjectWhenEnabled = false) where T : Behaviour
        {
            T[] components = FindObjectsByType<T>(FindObjectsInactive.Include, FindObjectsSortMode.None);
            for (int i = 0; i < components.Length; i++)
            {
                if (components[i] != null)
                {
                    if (isEnabled && activateGameObjectWhenEnabled && !components[i].gameObject.activeSelf)
                        components[i].gameObject.SetActive(true);
                    components[i].enabled = isEnabled;
                }
            }
        }

        private static void EnsureEventSystem()
        {
            if (FindFirstObjectByType<EventSystem>() != null) return;

            var eventSystem = new GameObject("EventSystem", typeof(EventSystem), typeof(InputSystemUIInputModule));
            eventSystem.GetComponent<InputSystemUIInputModule>().AssignDefaultActions();
            eventSystem.SetActive(true);
        }

        private static RectTransform AddRect(Transform parent, string name)
        {
            var go = new GameObject(name, typeof(RectTransform));
            go.transform.SetParent(parent, false);
            return (RectTransform)go.transform;
        }

        private static TextMeshProUGUI CreateText(Transform parent, string name, string text, float fontSize, FontStyles style, TextAlignmentOptions alignment)
        {
            var go = new GameObject(name, typeof(RectTransform), typeof(CanvasRenderer), typeof(TextMeshProUGUI));
            go.transform.SetParent(parent, false);
            TextMeshProUGUI textComponent = go.GetComponent<TextMeshProUGUI>();
            textComponent.text = text;
            textComponent.fontSize = fontSize;
            textComponent.fontStyle = style;
            textComponent.alignment = alignment;
            textComponent.color = Color.white;
            textComponent.textWrappingMode = TextWrappingModes.NoWrap;
            textComponent.overflowMode = TextOverflowModes.Ellipsis;
            textComponent.raycastTarget = false;
            return textComponent;
        }

        private static Button CreateButton(Transform parent, string name, string text, Color color)
        {
            var go = new GameObject(name, typeof(RectTransform), typeof(Image), typeof(Button));
            go.transform.SetParent(parent, false);
            Image image = go.GetComponent<Image>();
            image.color = color;

            Button button = go.GetComponent<Button>();
            ConfigureButtonColors(button, color);

            TextMeshProUGUI label = CreateText(go.transform, "Label", text, 21f, FontStyles.Bold, TextAlignmentOptions.Center);
            Stretch(label.rectTransform, 4f, 4f, 4f, 4f);
            label.overflowMode = TextOverflowModes.Overflow;
            return button;
        }

        private static TMP_InputField CreateInput(Transform parent, string name)
        {
            var go = new GameObject(name, typeof(RectTransform), typeof(Image), typeof(TMP_InputField));
            go.transform.SetParent(parent, false);
            go.GetComponent<Image>().color = new Color(0.92f, 0.94f, 0.96f, 1f);

            RectTransform root = (RectTransform)go.transform;

            RectTransform viewport = AddRect(root, "Viewport");
            Stretch(viewport, 10f, 10f, 6f, 6f);

            TextMeshProUGUI text = CreateText(viewport, "Text", string.Empty, 22f, FontStyles.Normal, TextAlignmentOptions.Left);
            text.color = new Color(0.04f, 0.05f, 0.06f, 1f);
            text.raycastTarget = true;
            Stretch(text.rectTransform, 4f, 4f, 2f, 2f);

            TextMeshProUGUI placeholder = CreateText(viewport, "Placeholder", "Room name", 22f, FontStyles.Italic, TextAlignmentOptions.Left);
            placeholder.color = new Color(0.45f, 0.48f, 0.52f, 1f);
            Stretch(placeholder.rectTransform, 4f, 4f, 2f, 2f);

            TMP_InputField input = go.GetComponent<TMP_InputField>();
            input.textViewport = viewport;
            input.textComponent = text;
            input.placeholder = placeholder;
            input.lineType = TMP_InputField.LineType.SingleLine;
            return input;
        }

        private static void ConfigureButtonColors(Button button, Color normalColor)
        {
            ColorBlock colors = button.colors;
            colors.normalColor = normalColor;
            colors.highlightedColor = Color.Lerp(normalColor, Color.white, 0.16f);
            colors.pressedColor = Color.Lerp(normalColor, Color.black, 0.18f);
            colors.selectedColor = colors.highlightedColor;
            colors.disabledColor = new Color(normalColor.r, normalColor.g, normalColor.b, 0.35f);
            colors.fadeDuration = 0.08f;
            button.colors = colors;
        }

        private static void Stretch(RectTransform rectTransform, float left, float right, float top, float bottom)
        {
            rectTransform.anchorMin = Vector2.zero;
            rectTransform.anchorMax = Vector2.one;
            rectTransform.offsetMin = new Vector2(left, bottom);
            rectTransform.offsetMax = new Vector2(-right, -top);
        }

        private static void SetTopLeft(RectTransform rectTransform, float x, float y, float width, float height)
        {
            rectTransform.anchorMin = new Vector2(0f, 1f);
            rectTransform.anchorMax = new Vector2(0f, 1f);
            rectTransform.pivot = new Vector2(0f, 1f);
            rectTransform.anchoredPosition = new Vector2(x, -y);
            rectTransform.sizeDelta = new Vector2(width, height);
        }

        private static void SetTopRight(RectTransform rectTransform, float x, float y, float width, float height)
        {
            rectTransform.anchorMin = new Vector2(1f, 1f);
            rectTransform.anchorMax = new Vector2(1f, 1f);
            rectTransform.pivot = new Vector2(1f, 1f);
            rectTransform.anchoredPosition = new Vector2(-x, -y);
            rectTransform.sizeDelta = new Vector2(width, height);
        }

        private static void SetBottomLeft(RectTransform rectTransform, float x, float y, float width, float height)
        {
            rectTransform.anchorMin = new Vector2(0f, 0f);
            rectTransform.anchorMax = new Vector2(0f, 0f);
            rectTransform.pivot = new Vector2(0f, 0f);
            rectTransform.anchoredPosition = new Vector2(x, y);
            rectTransform.sizeDelta = new Vector2(width, height);
        }

        private static void SetBottomRight(RectTransform rectTransform, float x, float y, float width, float height)
        {
            rectTransform.anchorMin = new Vector2(1f, 0f);
            rectTransform.anchorMax = new Vector2(1f, 0f);
            rectTransform.pivot = new Vector2(1f, 0f);
            rectTransform.anchoredPosition = new Vector2(-x, y);
            rectTransform.sizeDelta = new Vector2(width, height);
        }

        [Serializable]
        private sealed class RoomCoordinateDatabase
        {
            public string selected_id = string.Empty;
            public List<RoomCoordinateRecord> rooms = new List<RoomCoordinateRecord>();
        }

        [Serializable]
        private sealed class RoomCoordinateRecord
        {
            public string id;
            public string name;
            public string created_at;
            public float px;
            public float py;
            public float pz;
            public float qx;
            public float qy;
            public float qz;
            public float qw;
            public string anchor_uuid;

            public Pose ToPose()
            {
                float magnitude = Mathf.Sqrt(qx * qx + qy * qy + qz * qz + qw * qw);
                Quaternion rotation;
                if (magnitude < 0.0001f)
                {
                    rotation = Quaternion.identity;
                }
                else
                {
                    float invMagnitude = 1f / magnitude;
                    rotation = new Quaternion(qx * invMagnitude, qy * invMagnitude, qz * invMagnitude, qw * invMagnitude);
                }

                return new Pose(
                    new Vector3(px, py, pz),
                    rotation);
            }
        }
    }
}
