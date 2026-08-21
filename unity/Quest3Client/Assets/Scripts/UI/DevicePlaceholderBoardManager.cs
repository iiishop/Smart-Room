using System;
using System.Collections;
using System.Collections.Generic;
using SmartRoom.Interaction;
using SmartRoom.Tracking;
using TMPro;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.InputSystem.UI;
using UnityEngine.Networking;
using UnityEngine.UI;

namespace SmartRoom.UI
{
    public sealed class DevicePlaceholderBoardManager : MonoBehaviour
    {
        private const string DefaultObjectName = "DevicePlaceholderBoardManager";

        [Header("References")]
        [SerializeField] private Camera xrCamera;
        [SerializeField] private TrackingManager trackingManager;

        [Header("Board")]
        [SerializeField] private Vector2 boardSizeMeters = new Vector2(0.50f, 0.38f);
        [SerializeField] private float canvasPixelsPerMeter = 1000f;
        [SerializeField] private float minimumSideOffsetMeters = 0.32f;
        [SerializeField] private float maximumSideOffsetMeters = 0.95f;
        [SerializeField] private float sideMarginMeters = 0.12f;
        [SerializeField] private float upwardBiasMeters = 0.08f;
        [SerializeField] private float maxUpwardBiasMeters = 0.24f;
        [SerializeField] private float positionLerp = 10f;
        [SerializeField] private float rotationLerp = 14f;
        [SerializeField] private float liveRefreshSeconds = 1.5f;

        [Header("Colors")]
        [SerializeField] private Color panelColor = new Color(1f, 1f, 1f, 0.96f);
        [SerializeField] private Color borderColor = new Color(0.72f, 0.76f, 0.82f, 1f);
        [SerializeField] private Color shadowColor = new Color(0f, 0f, 0f, 0.18f);

        private readonly Dictionary<string, BoardEntry> _boards = new Dictionary<string, BoardEntry>();
        private static DevicePlaceholderBoardManager _instance;
        private bool _liveRequestInFlight;
        private float _nextLiveRefreshAt;

        private sealed class BoardEntry
        {
            public string ObjectId;
            public GameObject Root;
            public Vector3 DeviceCenter;
            public float OrbitRadius;
            public float UpOffset;
            public RectTransform CanvasRoot;
            public GraphicRaycaster GraphicRaycaster;
            public TextMeshProUGUI Title;
            public TextMeshProUGUI Status;
            public Button DataTab;
            public Button OperationsTab;
            public Button[] RowButtons = Array.Empty<Button>();
            public Button PreviousButton;
            public Button NextButton;
            public GameObject EditorRoot;
            public TextMeshProUGUI EditorTitle;
            public TMP_InputField EditorInput;
            public LiveDataRecord[] Data = Array.Empty<LiveDataRecord>();
            public LiveOperationRecord[] Operations = Array.Empty<LiveOperationRecord>();
            public string DisplayName = string.Empty;
            public string CanonicalDeviceId = string.Empty;
            public bool Bound;
            public bool Online;
            public bool ShowingOperations;
            public int Page;
            public string EditingDataKey = string.Empty;
            public string EditingOperationTopic = string.Empty;
            public PointerEventData PointerData;
            public EventSystem PointerEventSystem;
            public readonly List<RaycastResult> RaycastResults = new List<RaycastResult>();
            public GameObject HoveredObject;
        }

        public static DevicePlaceholderBoardManager EnsureExists(Camera camera = null)
        {
            if (_instance != null)
            {
                _instance.SetCamera(camera);
                return _instance;
            }

            DevicePlaceholderBoardManager existing = FindFirstObjectByType<DevicePlaceholderBoardManager>();
            if (existing != null)
            {
                _instance = existing;
                existing.SetCamera(camera);
                return existing;
            }

            GameObject go = GameObject.Find(DefaultObjectName);
            if (go == null)
                go = new GameObject(DefaultObjectName);

            DevicePlaceholderBoardManager manager = go.GetComponent<DevicePlaceholderBoardManager>();
            if (manager == null)
                manager = go.AddComponent<DevicePlaceholderBoardManager>();
            manager.SetCamera(camera);
            return manager;
        }

        public static void PlaceForObject(string objectId, TrackingManager.RoomObjectPointRecord[] points, Camera camera = null)
        {
            EnsureExists(camera).PlaceForObjectInternal(objectId, null, points);
        }

        public static void PlaceForObject(
            string objectId,
            TrackingManager.RoomObjectSpatialRecord spatial,
            TrackingManager.RoomObjectPointRecord[] points,
            Camera camera = null)
        {
            EnsureExists(camera).PlaceForObjectInternal(objectId, spatial, points);
        }

        public static void RemoveForObject(string objectId)
        {
            if (_instance == null || string.IsNullOrWhiteSpace(objectId))
                return;
            _instance.RemoveForObjectInternal(objectId);
        }

        public static void ClearBoards()
        {
            if (_instance == null)
                return;
            _instance.ClearBoardsInternal();
        }

        public static bool UpdateHoverAndConsumeTrigger(Ray ray, bool triggerPressedDown)
        {
            if (_instance == null)
                return false;
            return _instance.UpdateBoardPointer(ray, triggerPressedDown);
        }

        public static void RequestImmediateRefresh()
        {
            if (_instance != null)
                _instance._nextLiveRefreshAt = 0f;
        }

        public void SetCamera(Camera camera)
        {
            if (camera != null)
                xrCamera = camera;
        }

        private void Awake()
        {
            _instance = this;
            if (xrCamera == null)
                xrCamera = Camera.main;
            if (trackingManager == null)
                trackingManager = FindFirstObjectByType<TrackingManager>();
            EnsureEventSystem();
        }

        private void OnDestroy()
        {
            if (_instance == this)
                _instance = null;
        }

        private void Update()
        {
            if (xrCamera == null)
                xrCamera = Camera.main;
            if (xrCamera == null)
                return;

            foreach (BoardEntry entry in _boards.Values)
                UpdateBoardPose(entry);

            if (!_liveRequestInFlight &&
                _boards.Count > 0 &&
                RoomCoordinateSystemPanel.HasEnteredRoom &&
                Time.unscaledTime >= _nextLiveRefreshAt)
            {
                _nextLiveRefreshAt = Time.unscaledTime + Mathf.Max(0.5f, liveRefreshSeconds);
                StartCoroutine(RefreshLiveDevicesAsync());
            }
        }

        private void PlaceForObjectInternal(
            string objectId,
            TrackingManager.RoomObjectSpatialRecord spatial,
            TrackingManager.RoomObjectPointRecord[] points)
        {
            if (string.IsNullOrWhiteSpace(objectId))
                return;
            if (xrCamera == null)
                xrCamera = Camera.main;
            if (xrCamera == null)
                return;

            bool solved = TrySolvePlacement(spatial, out Vector3 center, out Vector3 boardPosition);
            if (!solved)
                solved = TrySolvePlacement(points, out center, out boardPosition);
            if (!solved)
                return;

            RemoveForObjectInternal(objectId);
            BoardEntry entry = CreateBoard(objectId, center, boardPosition);
            _boards[objectId] = entry;
            DeviceSpatialMarkerManager.PlaceForObjectCenter(objectId, center, xrCamera);
            UpdateBoardPose(entry, snap: true);
            Debug.Log($"[DevicePlaceholderBoard] Placed board for {objectId} center={center:F3} board={boardPosition:F3}");
        }

        private bool TrySolvePlacement(
            TrackingManager.RoomObjectSpatialRecord spatial,
            out Vector3 center,
            out Vector3 boardPosition)
        {
            center = Vector3.zero;
            boardPosition = Vector3.zero;
            if (spatial == null || !spatial.valid || spatial.center_xyz_m == null || spatial.center_xyz_m.Length < 3)
                return false;

            center = new Vector3(spatial.center_xyz_m[0], spatial.center_xyz_m[1], spatial.center_xyz_m[2]);
            if (!IsFinite(center))
                return false;

            Vector3 side = ResolveHorizontalSide(center);
            float sideRadius = Mathf.Max(0.08f, spatial.radius_m * 0.35f);
            float upRadius = 0.12f;
            if (spatial.min_xyz_m != null && spatial.min_xyz_m.Length >= 3 &&
                spatial.max_xyz_m != null && spatial.max_xyz_m.Length >= 3)
            {
                Vector3 min = new Vector3(spatial.min_xyz_m[0], spatial.min_xyz_m[1], spatial.min_xyz_m[2]);
                Vector3 max = new Vector3(spatial.max_xyz_m[0], spatial.max_xyz_m[1], spatial.max_xyz_m[2]);
                if (IsFinite(min) && IsFinite(max))
                {
                    Vector3 half = (max - min) * 0.5f;
                    upRadius = Mathf.Abs(half.y);
                    sideRadius = Mathf.Max(
                        sideRadius,
                        Mathf.Abs(side.x) * Mathf.Abs(half.x) +
                        Mathf.Abs(side.y) * Mathf.Abs(half.y) +
                        Mathf.Abs(side.z) * Mathf.Abs(half.z));
                }
            }

            boardPosition = BuildBoardPosition(center, side, sideRadius, upRadius);
            return true;
        }

        private bool TrySolvePlacement(
            TrackingManager.RoomObjectPointRecord[] points,
            out Vector3 center,
            out Vector3 boardPosition)
        {
            if (points == null || points.Length == 0)
            {
                center = Vector3.zero;
                boardPosition = Vector3.zero;
                return false;
            }

            List<Vector3> positivePoints = new List<Vector3>();
            List<Vector3> allPoints = new List<Vector3>();
            for (int i = 0; i < points.Length; i++)
            {
                TrackingManager.RoomObjectPointRecord point = points[i];
                if (point == null)
                    continue;
                Vector3 world;
                if (point.room_xyz_m != null && point.room_xyz_m.Length >= 3)
                {
                    world = RoomSpatialAnchorManager.RoomToWorldPoint(
                        new Vector3(point.room_xyz_m[0], point.room_xyz_m[1], point.room_xyz_m[2]));
                }
                else if (point.world_xyz_m != null && point.world_xyz_m.Length >= 3)
                {
                    world = new Vector3(point.world_xyz_m[0], point.world_xyz_m[1], point.world_xyz_m[2]);
                }
                else
                {
                    continue;
                }
                if (!IsFinite(world))
                    continue;
                allPoints.Add(world);
                if (point.label > 0)
                    positivePoints.Add(world);
            }

            List<Vector3> source = positivePoints.Count > 0 ? positivePoints : allPoints;
            if (source.Count == 0)
            {
                center = Vector3.zero;
                boardPosition = Vector3.zero;
                return false;
            }

            center = RobustCenter(source);

            Vector3 side = ResolveHorizontalSide(center);
            float sideRadius = 0f;
            float upRadius = 0f;
            for (int i = 0; i < source.Count; i++)
            {
                Vector3 delta = source[i] - center;
                sideRadius = Mathf.Max(sideRadius, Mathf.Abs(Vector3.Dot(delta, side)));
                upRadius = Mathf.Max(upRadius, Mathf.Abs(Vector3.Dot(delta, Vector3.up)));
            }

            boardPosition = BuildBoardPosition(center, side, sideRadius, upRadius);
            return true;
        }

        private Vector3 ResolveHorizontalSide(Vector3 center)
        {
            Vector3 centerToUser = Vector3.ProjectOnPlane(
                xrCamera.transform.position - center,
                Vector3.up);
            Vector3 side = centerToUser.sqrMagnitude > 0.0001f
                ? Vector3.Cross(centerToUser.normalized, Vector3.up)
                : Vector3.zero;
            if (side.sqrMagnitude < 0.0001f)
                side = Vector3.ProjectOnPlane(xrCamera.transform.right, Vector3.up);
            if (side.sqrMagnitude < 0.0001f)
                side = Vector3.ProjectOnPlane(Vector3.Cross(Vector3.up, center - xrCamera.transform.position), Vector3.up);
            if (side.sqrMagnitude < 0.0001f)
                side = Vector3.right;
            side.Normalize();
            return side;
        }

        private Vector3 BuildBoardPosition(Vector3 center, Vector3 side, float sideRadius, float upRadius)
        {
            float sideOffset = Mathf.Clamp(
                sideRadius + boardSizeMeters.x * 0.5f + sideMarginMeters,
                minimumSideOffsetMeters,
                maximumSideOffsetMeters);
            float upOffset = Mathf.Clamp(upwardBiasMeters + upRadius * 0.25f, upwardBiasMeters, maxUpwardBiasMeters);
            return center + side * sideOffset + Vector3.up * upOffset;
        }

        private BoardEntry CreateBoard(string objectId, Vector3 center, Vector3 boardPosition)
        {
            GameObject root = new GameObject(
                "DeviceLiveBoard_" + objectId,
                typeof(RectTransform),
                typeof(Canvas),
                typeof(CanvasScaler),
                typeof(GraphicRaycaster));
            root.transform.SetParent(transform, true);
            root.transform.position = boardPosition;

            RectTransform rootRect = root.GetComponent<RectTransform>();
            rootRect.sizeDelta = new Vector2(boardSizeMeters.x * canvasPixelsPerMeter, boardSizeMeters.y * canvasPixelsPerMeter);
            rootRect.localScale = Vector3.one / canvasPixelsPerMeter;

            Canvas canvas = root.GetComponent<Canvas>();
            canvas.renderMode = RenderMode.WorldSpace;
            canvas.worldCamera = xrCamera;
            canvas.sortingOrder = 20;

            CanvasScaler scaler = root.GetComponent<CanvasScaler>();
            scaler.dynamicPixelsPerUnit = canvasPixelsPerMeter;
            scaler.referencePixelsPerUnit = 100f;

            Vector3 offset = boardPosition - center;
            BoardEntry entry = new BoardEntry
            {
                ObjectId = objectId,
                Root = root,
                DeviceCenter = center,
                OrbitRadius = Mathf.Max(0.05f, Vector3.ProjectOnPlane(offset, Vector3.up).magnitude),
                UpOffset = offset.y,
                CanvasRoot = rootRect,
                GraphicRaycaster = root.GetComponent<GraphicRaycaster>(),
            };

            GameObject shadow = CreateImage(rootRect, "Shadow", shadowColor);
            RectTransform shadowRect = (RectTransform)shadow.transform;
            Stretch(shadowRect, -7f, -7f, -7f, -7f);
            shadowRect.anchoredPosition = new Vector2(8f, -8f);

            GameObject border = CreateImage(rootRect, "Border", borderColor);
            Stretch((RectTransform)border.transform, -3f, -3f, -3f, -3f);

            GameObject panel = CreateImage(rootRect, "WhitePanel", panelColor);
            Stretch((RectTransform)panel.transform, 5f, 5f, 5f, 5f);

            entry.Title = CreateText(rootRect, "Title", "Waiting for binding", 24f, FontStyles.Bold, TextAlignmentOptions.Left);
            entry.Title.color = new Color(0.04f, 0.05f, 0.07f, 1f);
            SetTopLeft(entry.Title.rectTransform, 18f, 14f, 458f, 34f);

            entry.Status = CreateText(rootRect, "Status", "Loading live device data...", 15f, FontStyles.Normal, TextAlignmentOptions.Left);
            entry.Status.color = new Color(0.32f, 0.36f, 0.42f, 1f);
            SetTopLeft(entry.Status.rectTransform, 18f, 48f, 458f, 24f);

            entry.DataTab = CreateButton(rootRect, "DataTab", "Data", new Color(0.12f, 0.42f, 0.68f, 1f));
            SetTopLeft((RectTransform)entry.DataTab.transform, 18f, 78f, 104f, 36f);
            entry.DataTab.onClick.AddListener(() => SetBoardTab(entry, false));

            entry.OperationsTab = CreateButton(rootRect, "OperationsTab", "Controls", new Color(0.28f, 0.31f, 0.36f, 1f));
            SetTopLeft((RectTransform)entry.OperationsTab.transform, 128f, 78f, 122f, 36f);
            entry.OperationsTab.onClick.AddListener(() => SetBoardTab(entry, true));

            entry.RowButtons = new Button[5];
            for (int i = 0; i < entry.RowButtons.Length; i++)
            {
                int rowIndex = i;
                Button row = CreateButton(
                    rootRect,
                    "LiveRow_" + i,
                    string.Empty,
                    new Color(0.91f, 0.93f, 0.95f, 1f),
                    16f,
                    TextAlignmentOptions.Left);
                SetTopLeft((RectTransform)row.transform, 18f, 122f + i * 42f, 458f, 36f);
                row.GetComponentInChildren<TextMeshProUGUI>().color = new Color(0.05f, 0.06f, 0.08f, 1f);
                row.onClick.AddListener(() => OpenRowEditor(entry, rowIndex));
                entry.RowButtons[i] = row;
            }

            entry.PreviousButton = CreateButton(rootRect, "PreviousPage", "<", new Color(0.25f, 0.28f, 0.32f, 1f));
            SetBottomLeft((RectTransform)entry.PreviousButton.transform, 18f, 14f, 48f, 34f);
            entry.PreviousButton.onClick.AddListener(() => ChangePage(entry, -1));

            entry.NextButton = CreateButton(rootRect, "NextPage", ">", new Color(0.25f, 0.28f, 0.32f, 1f));
            SetBottomLeft((RectTransform)entry.NextButton.transform, 72f, 14f, 48f, 34f);
            entry.NextButton.onClick.AddListener(() => ChangePage(entry, 1));

            entry.EditorRoot = CreateImage(rootRect, "Editor", new Color(0.96f, 0.97f, 0.98f, 1f));
            RectTransform editorRect = (RectTransform)entry.EditorRoot.transform;
            Stretch(editorRect, 12f, 12f, 112f, 12f);
            entry.EditorRoot.GetComponent<Image>().raycastTarget = true;

            entry.EditorTitle = CreateText(editorRect, "EditorTitle", "Set value", 20f, FontStyles.Bold, TextAlignmentOptions.Left);
            entry.EditorTitle.color = new Color(0.04f, 0.05f, 0.07f, 1f);
            SetTopLeft(entry.EditorTitle.rectTransform, 14f, 12f, 430f, 32f);

            entry.EditorInput = CreateInput(editorRect, "ValueInput", "MQTT value");
            SetTopLeft((RectTransform)entry.EditorInput.transform, 14f, 54f, 430f, 48f);
            QuestSystemKeyboardInputBridge bridge = entry.EditorInput.gameObject.AddComponent<QuestSystemKeyboardInputBridge>();
            bridge.Configure(entry.EditorInput, "MQTT value", false, 256);

            Button send = CreateButton(editorRect, "Send", "Publish", new Color(0.08f, 0.48f, 0.30f, 1f));
            SetBottomRight((RectTransform)send.transform, 14f, 12f, 112f, 40f);
            send.onClick.AddListener(() => StartCoroutine(PublishEditorValueAsync(entry)));

            Button cancel = CreateButton(editorRect, "Cancel", "Cancel", new Color(0.36f, 0.39f, 0.44f, 1f));
            SetBottomRight((RectTransform)cancel.transform, 134f, 12f, 104f, 40f);
            cancel.onClick.AddListener(() => CloseEditor(entry));
            entry.EditorRoot.SetActive(false);

            RenderBoard(entry);
            return entry;
        }

        private static GameObject CreateImage(Transform parent, string name, Color color)
        {
            GameObject go = new GameObject(name, typeof(RectTransform), typeof(CanvasRenderer), typeof(Image));
            go.transform.SetParent(parent, false);
            Image image = go.GetComponent<Image>();
            image.color = color;
            image.raycastTarget = false;
            return go;
        }

        private static TextMeshProUGUI CreateText(
            Transform parent,
            string name,
            string text,
            float fontSize,
            FontStyles style,
            TextAlignmentOptions alignment)
        {
            GameObject go = new GameObject(name, typeof(RectTransform), typeof(TextMeshProUGUI));
            go.transform.SetParent(parent, false);
            TextMeshProUGUI label = go.GetComponent<TextMeshProUGUI>();
            label.text = text;
            label.fontSize = fontSize;
            label.fontStyle = style;
            label.alignment = alignment;
            label.textWrappingMode = TextWrappingModes.NoWrap;
            label.overflowMode = TextOverflowModes.Ellipsis;
            label.raycastTarget = false;
            return label;
        }

        private static Button CreateButton(
            Transform parent,
            string name,
            string text,
            Color color,
            float fontSize = 18f,
            TextAlignmentOptions alignment = TextAlignmentOptions.Center)
        {
            GameObject go = new GameObject(name, typeof(RectTransform), typeof(Image), typeof(Button));
            go.transform.SetParent(parent, false);
            go.GetComponent<Image>().color = color;
            Button button = go.GetComponent<Button>();
            ConfigureButtonColors(button, color);
            TextMeshProUGUI label = CreateText(go.transform, "Label", text, fontSize, FontStyles.Bold, alignment);
            label.color = Color.white;
            Stretch(label.rectTransform, 10f, 10f, 4f, 4f);
            return button;
        }

        private static TMP_InputField CreateInput(Transform parent, string name, string placeholderText)
        {
            GameObject go = new GameObject(name, typeof(RectTransform), typeof(Image), typeof(TMP_InputField));
            go.transform.SetParent(parent, false);
            go.GetComponent<Image>().color = Color.white;
            RectTransform viewport = new GameObject("Viewport", typeof(RectTransform)).GetComponent<RectTransform>();
            viewport.SetParent(go.transform, false);
            Stretch(viewport, 10f, 10f, 6f, 6f);

            TextMeshProUGUI text = CreateText(viewport, "Text", string.Empty, 20f, FontStyles.Normal, TextAlignmentOptions.Left);
            text.color = new Color(0.04f, 0.05f, 0.07f, 1f);
            text.raycastTarget = true;
            Stretch(text.rectTransform, 2f, 2f, 2f, 2f);
            TextMeshProUGUI placeholder = CreateText(
                viewport,
                "Placeholder",
                placeholderText,
                18f,
                FontStyles.Italic,
                TextAlignmentOptions.Left);
            placeholder.color = new Color(0.42f, 0.46f, 0.50f, 1f);
            Stretch(placeholder.rectTransform, 2f, 2f, 2f, 2f);

            TMP_InputField input = go.GetComponent<TMP_InputField>();
            input.textViewport = viewport;
            input.textComponent = text;
            input.placeholder = placeholder;
            input.lineType = TMP_InputField.LineType.SingleLine;
            input.characterLimit = 256;
            return input;
        }

        private static void ConfigureButtonColors(Button button, Color normal)
        {
            ColorBlock colors = button.colors;
            colors.normalColor = normal;
            colors.highlightedColor = Color.Lerp(normal, Color.white, 0.18f);
            colors.pressedColor = Color.Lerp(normal, Color.black, 0.16f);
            colors.selectedColor = colors.highlightedColor;
            colors.disabledColor = new Color(normal.r, normal.g, normal.b, 0.32f);
            colors.fadeDuration = 0.06f;
            button.colors = colors;
        }

        private static void SetButtonColor(Button button, Color color)
        {
            if (button == null)
                return;
            Image image = button.GetComponent<Image>();
            if (image != null)
                image.color = color;
            ConfigureButtonColors(button, color);
        }

        private static void EnsureEventSystem()
        {
            if (FindFirstObjectByType<EventSystem>() != null)
                return;
            GameObject eventSystem = new GameObject(
                "EventSystem",
                typeof(EventSystem),
                typeof(InputSystemUIInputModule));
            eventSystem.GetComponent<InputSystemUIInputModule>().AssignDefaultActions();
            eventSystem.SetActive(true);
        }

        private static string ShortText(string value, int maxLength)
        {
            string text = (value ?? string.Empty).Replace('\r', ' ').Replace('\n', ' ').Trim();
            if (text.Length <= maxLength)
                return text;
            return text.Substring(0, Mathf.Max(0, maxLength - 3)) + "...";
        }

        private void UpdateBoardPose(BoardEntry entry, bool snap = false)
        {
            if (entry == null || entry.Root == null || xrCamera == null)
                return;

            Vector3 side = ResolveHorizontalSide(entry.DeviceCenter);
            Vector3 targetPosition =
                entry.DeviceCenter +
                side * entry.OrbitRadius +
                Vector3.up * entry.UpOffset;
            if (snap)
                entry.Root.transform.position = targetPosition;
            else
            {
                float positionT = 1f - Mathf.Exp(-positionLerp * Time.deltaTime);
                entry.Root.transform.position = Vector3.Lerp(
                    entry.Root.transform.position,
                    targetPosition,
                    positionT);
            }

            Vector3 forward = entry.Root.transform.position - xrCamera.transform.position;
            if (forward.sqrMagnitude < 0.0001f)
                forward = xrCamera.transform.forward;
            Quaternion targetRotation = Quaternion.LookRotation(forward.normalized, Vector3.up);
            if (snap)
            {
                entry.Root.transform.rotation = targetRotation;
                return;
            }

            float t = 1f - Mathf.Exp(-rotationLerp * Time.deltaTime);
            entry.Root.transform.rotation = Quaternion.Slerp(entry.Root.transform.rotation, targetRotation, t);
        }

        private void SetBoardTab(BoardEntry entry, bool operations)
        {
            if (entry == null)
                return;
            entry.ShowingOperations = operations;
            entry.Page = 0;
            CloseEditor(entry);
            RenderBoard(entry);
        }

        private void ChangePage(BoardEntry entry, int delta)
        {
            if (entry == null)
                return;
            int count = entry.ShowingOperations ? entry.Operations.Length : entry.Data.Length;
            int maxPage = Mathf.Max(0, Mathf.CeilToInt(count / (float)entry.RowButtons.Length) - 1);
            entry.Page = Mathf.Clamp(entry.Page + delta, 0, maxPage);
            RenderBoard(entry);
        }

        private void RenderBoard(BoardEntry entry)
        {
            if (entry == null || entry.Root == null)
                return;

            entry.Title.text = entry.Bound
                ? (string.IsNullOrWhiteSpace(entry.DisplayName) ? "Bound network device" : entry.DisplayName)
                : "Unbound device";
            if (!entry.Bound)
                entry.Status.text = "Select the yellow marker to bind this object";
            else if (string.IsNullOrWhiteSpace(entry.DisplayName))
                entry.Status.text = "Bound device is not currently discovered";
            else
                entry.Status.text = entry.Online ? "Live MQTT profile" : "Stored profile (offline)";

            SetButtonColor(
                entry.DataTab,
                entry.ShowingOperations ? new Color(0.28f, 0.31f, 0.36f, 1f) : new Color(0.12f, 0.42f, 0.68f, 1f));
            SetButtonColor(
                entry.OperationsTab,
                entry.ShowingOperations ? new Color(0.12f, 0.42f, 0.68f, 1f) : new Color(0.28f, 0.31f, 0.36f, 1f));

            int count = entry.ShowingOperations ? entry.Operations.Length : entry.Data.Length;
            int pageSize = entry.RowButtons.Length;
            int maxPage = Mathf.Max(0, Mathf.CeilToInt(count / (float)pageSize) - 1);
            entry.Page = Mathf.Clamp(entry.Page, 0, maxPage);
            int start = entry.Page * pageSize;
            for (int i = 0; i < entry.RowButtons.Length; i++)
            {
                Button row = entry.RowButtons[i];
                int index = start + i;
                bool visible = index < count;
                row.gameObject.SetActive(visible);
                if (!visible)
                    continue;

                TextMeshProUGUI label = row.GetComponentInChildren<TextMeshProUGUI>();
                if (entry.ShowingOperations)
                {
                    LiveOperationRecord operation = entry.Operations[index];
                    string key = string.IsNullOrWhiteSpace(operation.sensor_key)
                        ? ShortText(operation.action, 18)
                        : ShortText(operation.sensor_key, 18);
                    string values = operation.accepted_values != null && operation.accepted_values.Length > 0
                        ? string.Join(" | ", operation.accepted_values)
                        : "enter value";
                    label.text = $"{key}    {ShortText(values, 34)}";
                    row.interactable = entry.Bound && entry.Online;
                }
                else
                {
                    LiveDataRecord data = entry.Data[index];
                    label.text =
                        $"{ShortText(data.key, 20)}    {ShortText(data.value_text, 26)}" +
                        $"{(string.IsNullOrWhiteSpace(data.unit) ? "" : " " + data.unit)}  [edit]";
                    row.interactable = entry.Bound;
                }
            }

            entry.PreviousButton.interactable = entry.Page > 0;
            entry.NextButton.interactable = entry.Page < maxPage;
        }

        private void OpenRowEditor(BoardEntry entry, int visibleRowIndex)
        {
            if (entry == null || visibleRowIndex < 0 || visibleRowIndex >= entry.RowButtons.Length)
                return;
            int index = entry.Page * entry.RowButtons.Length + visibleRowIndex;
            entry.EditingDataKey = string.Empty;
            entry.EditingOperationTopic = string.Empty;

            if (entry.ShowingOperations)
            {
                if (index < 0 || index >= entry.Operations.Length)
                    return;
                LiveOperationRecord operation = entry.Operations[index];
                entry.EditingOperationTopic = operation.topic;
                entry.EditorTitle.text =
                    "Publish " +
                    (string.IsNullOrWhiteSpace(operation.sensor_key) ? operation.action : operation.sensor_key);
                entry.EditorInput.text =
                    operation.accepted_values != null && operation.accepted_values.Length > 0
                        ? operation.accepted_values[0]
                        : string.Empty;
            }
            else
            {
                if (index < 0 || index >= entry.Data.Length)
                    return;
                LiveDataRecord data = entry.Data[index];
                entry.EditingDataKey = data.key;
                entry.EditorTitle.text = "Publish " + data.key;
                entry.EditorInput.text = data.value_text;
            }

            entry.EditorRoot.SetActive(true);
            entry.EditorInput.ActivateInputField();
            QuestSystemKeyboard.OpenFor(
                entry.EditorInput,
                "MQTT value",
                multiline: false,
                characterLimit: 256);
        }

        private void CloseEditor(BoardEntry entry)
        {
            if (entry == null)
                return;
            QuestSystemKeyboard.CloseFor(entry.EditorInput);
            entry.EditingDataKey = string.Empty;
            entry.EditingOperationTopic = string.Empty;
            if (entry.EditorRoot != null)
                entry.EditorRoot.SetActive(false);
        }

        private IEnumerator RefreshLiveDevicesAsync()
        {
            if (trackingManager == null)
                trackingManager = FindFirstObjectByType<TrackingManager>();
            if (trackingManager == null)
                yield break;

            _liveRequestInFlight = true;
            string url = trackingManager.BuildViewerUrl(
                "/api/room/object/device/live?room_id=" + UnityWebRequest.EscapeURL(RoomCoordinateSystemPanel.CurrentRoomId) +
                "&room_name=" + UnityWebRequest.EscapeURL(RoomCoordinateSystemPanel.CurrentRoomName) +
                "&device_id=" + UnityWebRequest.EscapeURL(SystemInfo.deviceUniqueIdentifier) +
                "&device_name=" + UnityWebRequest.EscapeURL(SystemInfo.deviceName) +
                "&device_model=" + UnityWebRequest.EscapeURL(SystemInfo.deviceModel));

            using (UnityWebRequest request = UnityWebRequest.Get(url))
            {
                request.timeout = 10;
                yield return request.SendWebRequest();
                _liveRequestInFlight = false;
                if (request.result != UnityWebRequest.Result.Success)
                {
                    foreach (BoardEntry entry in _boards.Values)
                        entry.Status.text = "Live data unavailable: " + ShortText(request.error, 36);
                    yield break;
                }

                LiveDevicesResponse response;
                try
                {
                    response = JsonUtility.FromJson<LiveDevicesResponse>(request.downloadHandler.text);
                }
                catch (Exception ex)
                {
                    foreach (BoardEntry entry in _boards.Values)
                        entry.Status.text = "Live data JSON error: " + ShortText(ex.Message, 32);
                    yield break;
                }

                if (response == null || !response.ok)
                    yield break;
                Dictionary<string, LiveDeviceRecord> byObject = new Dictionary<string, LiveDeviceRecord>();
                LiveDeviceRecord[] records = response.devices ?? Array.Empty<LiveDeviceRecord>();
                for (int i = 0; i < records.Length; i++)
                {
                    if (records[i] != null && !string.IsNullOrWhiteSpace(records[i].object_id))
                        byObject[records[i].object_id] = records[i];
                }

                foreach (KeyValuePair<string, BoardEntry> item in _boards)
                {
                    if (byObject.TryGetValue(item.Key, out LiveDeviceRecord record))
                        ApplyLiveRecord(item.Value, record);
                    else
                        item.Value.Status.text = "Object is not present in the current room response";
                }
            }
        }

        private void ApplyLiveRecord(BoardEntry entry, LiveDeviceRecord record)
        {
            LiveNetworkProfile profile = record.profile;
            string incomingCanonicalId = !string.IsNullOrWhiteSpace(record.canonical_device_id)
                ? record.canonical_device_id
                : profile != null ? profile.canonical_device_id : string.Empty;
            bool bindingChanged =
                !string.IsNullOrWhiteSpace(entry.CanonicalDeviceId) &&
                !string.IsNullOrWhiteSpace(incomingCanonicalId) &&
                !string.Equals(entry.CanonicalDeviceId, incomingCanonicalId, StringComparison.Ordinal);

            if (!record.bound || bindingChanged)
            {
                entry.Data = Array.Empty<LiveDataRecord>();
                entry.Operations = Array.Empty<LiveOperationRecord>();
            }

            entry.Bound = record.bound;
            entry.CanonicalDeviceId = record.bound ? incomingCanonicalId : string.Empty;
            if (profile != null && !string.IsNullOrWhiteSpace(profile.canonical_device_id))
            {
                entry.DisplayName = profile.display_name;
                entry.Online = profile.online;
                entry.Data = MergeDataRows(entry.Data, profile.data);
                entry.Operations = MergeOperationRows(entry.Operations, profile.operations);
            }
            else
            {
                if (!string.IsNullOrWhiteSpace(record.binding_name))
                    entry.DisplayName = record.binding_name;
                entry.Online = false;
            }
            RenderBoard(entry);
        }

        private static LiveDataRecord[] MergeDataRows(
            LiveDataRecord[] existing,
            LiveDataRecord[] incoming)
        {
            var result = new List<LiveDataRecord>();
            var indexes = new Dictionary<string, int>(StringComparer.Ordinal);
            foreach (LiveDataRecord row in existing ?? Array.Empty<LiveDataRecord>())
            {
                if (row == null || string.IsNullOrWhiteSpace(row.key))
                    continue;
                indexes[row.key] = result.Count;
                result.Add(row);
            }

            foreach (LiveDataRecord row in incoming ?? Array.Empty<LiveDataRecord>())
            {
                if (row == null || string.IsNullOrWhiteSpace(row.key))
                    continue;
                if (!indexes.TryGetValue(row.key, out int index))
                {
                    indexes[row.key] = result.Count;
                    result.Add(row);
                    continue;
                }

                LiveDataRecord previous = result[index];
                if (row.timestamp <= 0.0 || previous == null || row.timestamp >= previous.timestamp)
                    result[index] = row;
            }
            return result.ToArray();
        }

        private static LiveOperationRecord[] MergeOperationRows(
            LiveOperationRecord[] existing,
            LiveOperationRecord[] incoming)
        {
            var result = new List<LiveOperationRecord>();
            var indexes = new Dictionary<string, int>(StringComparer.Ordinal);
            foreach (LiveOperationRecord row in existing ?? Array.Empty<LiveOperationRecord>())
            {
                if (row == null)
                    continue;
                string key = OperationKey(row);
                indexes[key] = result.Count;
                result.Add(row);
            }

            foreach (LiveOperationRecord row in incoming ?? Array.Empty<LiveOperationRecord>())
            {
                if (row == null)
                    continue;
                string key = OperationKey(row);
                if (indexes.TryGetValue(key, out int index))
                    result[index] = row;
                else
                {
                    indexes[key] = result.Count;
                    result.Add(row);
                }
            }
            return result.ToArray();
        }

        private static string OperationKey(LiveOperationRecord row)
        {
            return (row.topic ?? string.Empty) + "\n" +
                   (row.action ?? string.Empty) + "\n" +
                   (row.sensor_key ?? string.Empty);
        }

        private IEnumerator PublishEditorValueAsync(BoardEntry entry)
        {
            if (entry == null || trackingManager == null || entry.EditorInput == null)
                yield break;
            string dataKey = entry.EditingDataKey;
            string operationTopic = entry.EditingOperationTopic;
            if (string.IsNullOrWhiteSpace(dataKey) && string.IsNullOrWhiteSpace(operationTopic))
                yield break;

            LiveControlRequest payload = new LiveControlRequest
            {
                room_id = RoomCoordinateSystemPanel.CurrentRoomId,
                room_name = RoomCoordinateSystemPanel.CurrentRoomName,
                device_id = SystemInfo.deviceUniqueIdentifier,
                device_name = SystemInfo.deviceName,
                device_model = SystemInfo.deviceModel,
                object_id = entry.ObjectId,
                data_key = dataKey,
                operation_topic = operationTopic,
                value_text = entry.EditorInput.text ?? string.Empty,
            };
            entry.Status.text = "Publishing MQTT value...";
            string body = JsonUtility.ToJson(payload);
            using (UnityWebRequest request = new UnityWebRequest(
                       trackingManager.BuildViewerUrl("/api/room/object/device/control"),
                       UnityWebRequest.kHttpVerbPOST))
            {
                request.uploadHandler = new UploadHandlerRaw(System.Text.Encoding.UTF8.GetBytes(body));
                request.downloadHandler = new DownloadHandlerBuffer();
                request.SetRequestHeader("Content-Type", "application/json");
                request.timeout = 15;
                yield return request.SendWebRequest();
                if (request.result != UnityWebRequest.Result.Success)
                {
                    entry.Status.text = "Publish failed: " + ShortText(request.downloadHandler.text, 38);
                    yield break;
                }
            }

            CloseEditor(entry);
            entry.Status.text = "Published. Waiting for device update...";
            _nextLiveRefreshAt = 0f;
        }

        private bool UpdateBoardPointer(Ray ray, bool triggerPressedDown)
        {
            BoardEntry closestEntry = null;
            GameObject closestTarget = null;
            PointerEventData closestPointer = null;
            float closestDistance = float.PositiveInfinity;

            foreach (BoardEntry entry in _boards.Values)
            {
                if (TryRaycastBoard(entry, ray, out GameObject target, out PointerEventData pointer, out float distance) &&
                    distance < closestDistance)
                {
                    closestEntry = entry;
                    closestTarget = target;
                    closestPointer = pointer;
                    closestDistance = distance;
                }
            }

            foreach (BoardEntry entry in _boards.Values)
            {
                if (entry != closestEntry)
                    SetBoardHover(entry, null, entry.PointerData);
            }
            if (closestEntry == null)
                return false;

            SetBoardHover(closestEntry, closestTarget, closestPointer);
            if (triggerPressedDown && closestTarget != null)
                ExecuteEvents.ExecuteHierarchy(closestTarget, closestPointer, ExecuteEvents.pointerClickHandler);
            return triggerPressedDown;
        }

        private bool TryRaycastBoard(
            BoardEntry entry,
            Ray ray,
            out GameObject target,
            out PointerEventData pointerData,
            out float distance)
        {
            target = null;
            pointerData = null;
            distance = 0f;
            if (entry == null || entry.Root == null || entry.CanvasRoot == null ||
                entry.GraphicRaycaster == null || xrCamera == null)
                return false;

            Plane plane = new Plane(entry.CanvasRoot.forward, entry.CanvasRoot.position);
            if (!plane.Raycast(ray, out distance) || distance < 0f)
                return false;
            Vector3 worldPoint = ray.GetPoint(distance);
            Vector3 local = entry.CanvasRoot.InverseTransformPoint(worldPoint);
            if (!entry.CanvasRoot.rect.Contains(new Vector2(local.x, local.y)))
                return false;

            EventSystem eventSystem = EventSystem.current;
            if (eventSystem == null)
                return true;
            if (entry.PointerData == null || entry.PointerEventSystem != eventSystem)
            {
                entry.PointerData = new PointerEventData(eventSystem);
                entry.PointerEventSystem = eventSystem;
            }
            entry.PointerData.Reset();
            entry.PointerData.position = Vector2.zero;
            entry.PointerData.button = PointerEventData.InputButton.Left;
            entry.PointerData.pointerId = -118;
            entry.RaycastResults.Clear();

            Graphic closestGraphic = null;
            int highestDepth = int.MinValue;
            Graphic[] graphics = entry.Root.GetComponentsInChildren<Graphic>(false);
            for (int i = 0; i < graphics.Length; i++)
            {
                Graphic graphic = graphics[i];
                if (graphic == null || !graphic.raycastTarget ||
                    !graphic.isActiveAndEnabled || graphic.canvasRenderer.cull)
                {
                    continue;
                }

                RectTransform graphicRect = graphic.rectTransform;
                Vector3 graphicLocal = graphicRect.InverseTransformPoint(worldPoint);
                if (!graphicRect.rect.Contains(new Vector2(graphicLocal.x, graphicLocal.y)))
                    continue;
                if (graphic.depth < highestDepth)
                    continue;

                closestGraphic = graphic;
                highestDepth = graphic.depth;
            }

            if (closestGraphic != null)
            {
                target = closestGraphic.gameObject;
                entry.PointerData.pointerCurrentRaycast = new RaycastResult
                {
                    gameObject = target,
                    module = entry.GraphicRaycaster,
                    distance = distance,
                    worldPosition = worldPoint,
                    worldNormal = entry.CanvasRoot.forward,
                };
            }
            pointerData = entry.PointerData;
            return true;
        }

        private static void SetBoardHover(BoardEntry entry, GameObject target, PointerEventData pointerData)
        {
            if (entry == null || entry.HoveredObject == target)
                return;
            if (entry.HoveredObject != null && entry.PointerData != null)
                ExecuteEvents.ExecuteHierarchy(entry.HoveredObject, entry.PointerData, ExecuteEvents.pointerExitHandler);
            entry.HoveredObject = target;
            if (target != null && pointerData != null)
                ExecuteEvents.ExecuteHierarchy(target, pointerData, ExecuteEvents.pointerEnterHandler);
        }

        private void RemoveForObjectInternal(string objectId)
        {
            if (!_boards.TryGetValue(objectId, out BoardEntry entry))
                return;
            if (entry.Root != null)
                Destroy(entry.Root);
            _boards.Remove(objectId);
            DeviceSpatialMarkerManager.RemoveForObject(objectId);
        }

        private void ClearBoardsInternal()
        {
            foreach (BoardEntry entry in _boards.Values)
            {
                if (entry.Root != null)
                    Destroy(entry.Root);
            }
            _boards.Clear();
            DeviceSpatialMarkerManager.ClearMarkers();
        }

        private static Vector3 RobustCenter(List<Vector3> points)
        {
            if (points.Count == 1)
                return points[0];

            float[] xs = new float[points.Count];
            float[] ys = new float[points.Count];
            float[] zs = new float[points.Count];
            for (int i = 0; i < points.Count; i++)
            {
                xs[i] = points[i].x;
                ys[i] = points[i].y;
                zs[i] = points[i].z;
            }
            return new Vector3(Median(xs), Median(ys), Median(zs));
        }

        private static float Median(float[] values)
        {
            System.Array.Sort(values);
            int mid = values.Length / 2;
            if ((values.Length & 1) == 1)
                return values[mid];
            return (values[mid - 1] + values[mid]) * 0.5f;
        }

        private static bool IsFinite(Vector3 value)
        {
            return IsFinite(value.x) && IsFinite(value.y) && IsFinite(value.z);
        }

        private static bool IsFinite(float value)
        {
            return !float.IsNaN(value) && !float.IsInfinity(value);
        }

        private static void Stretch(RectTransform rectTransform, float left, float right, float top, float bottom)
        {
            rectTransform.anchorMin = Vector2.zero;
            rectTransform.anchorMax = Vector2.one;
            rectTransform.offsetMin = new Vector2(left, bottom);
            rectTransform.offsetMax = new Vector2(-right, -top);
        }

        private static void SetTopLeft(RectTransform rect, float left, float top, float width, float height)
        {
            rect.anchorMin = new Vector2(0f, 1f);
            rect.anchorMax = new Vector2(0f, 1f);
            rect.pivot = new Vector2(0f, 1f);
            rect.anchoredPosition = new Vector2(left, -top);
            rect.sizeDelta = new Vector2(width, height);
        }

        private static void SetBottomLeft(RectTransform rect, float left, float bottom, float width, float height)
        {
            rect.anchorMin = Vector2.zero;
            rect.anchorMax = Vector2.zero;
            rect.pivot = Vector2.zero;
            rect.anchoredPosition = new Vector2(left, bottom);
            rect.sizeDelta = new Vector2(width, height);
        }

        private static void SetBottomRight(RectTransform rect, float right, float bottom, float width, float height)
        {
            rect.anchorMin = Vector2.right;
            rect.anchorMax = Vector2.right;
            rect.pivot = Vector2.right;
            rect.anchoredPosition = new Vector2(-right, bottom);
            rect.sizeDelta = new Vector2(width, height);
        }

        [Serializable]
        private sealed class LiveDevicesResponse
        {
            public bool ok = false;
            public string reason = string.Empty;
            public LiveDeviceRecord[] devices = Array.Empty<LiveDeviceRecord>();
        }

        [Serializable]
        private sealed class LiveDeviceRecord
        {
            public string object_id = string.Empty;
            public bool bound = false;
            public string canonical_device_id = string.Empty;
            public string binding_name = string.Empty;
            public LiveNetworkProfile profile = new LiveNetworkProfile();
        }

        [Serializable]
        private sealed class LiveNetworkProfile
        {
            public string canonical_device_id = string.Empty;
            public string display_name = string.Empty;
            public string summary = string.Empty;
            public bool online = false;
            public double last_seen = 0.0;
            public LiveDataRecord[] data = Array.Empty<LiveDataRecord>();
            public LiveOperationRecord[] operations = Array.Empty<LiveOperationRecord>();
        }

        [Serializable]
        private sealed class LiveDataRecord
        {
            public string key = string.Empty;
            public string value_text = string.Empty;
            public string unit = string.Empty;
            public double timestamp = 0.0;
            public bool writable = false;
            public string write_topic = string.Empty;
        }

        [Serializable]
        private sealed class LiveOperationRecord
        {
            public string topic = string.Empty;
            public string action = string.Empty;
            public string sensor_key = string.Empty;
            public string[] accepted_values = Array.Empty<string>();
            public float confidence = 0f;
        }

        [Serializable]
        private sealed class LiveControlRequest
        {
            public string room_id = string.Empty;
            public string room_name = string.Empty;
            public string device_id = string.Empty;
            public string device_name = string.Empty;
            public string device_model = string.Empty;
            public string object_id = string.Empty;
            public string data_key = string.Empty;
            public string operation_topic = string.Empty;
            public string value_text = string.Empty;
        }
    }
}
