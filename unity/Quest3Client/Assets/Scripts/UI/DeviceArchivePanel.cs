using System;
using System.Collections;
using System.Collections.Generic;
using System.Threading.Tasks;
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
    public sealed class DeviceArchivePanel : MonoBehaviour
    {
        private const string DefaultObjectName = "DeviceArchivePanel";
        private const float PanelWidth = 760f;
        private const float PanelHeight = 540f;
        private const float RowHeight = 92f;
        private const float RowGap = 9f;

        [Header("Follow")]
        [SerializeField] private Transform headTarget;
        [SerializeField] private Vector3 localOffset = new Vector3(-0.05f, -0.08f, 1.0f);
        [SerializeField] private float minFollowSpeed = 1.2f;
        [SerializeField] private float followSpeedPerMeter = 8f;
        [SerializeField] private float maxFollowSpeed = 18f;
        [SerializeField] private float minRotationSpeed = 2.5f;
        [SerializeField] private float rotationSpeedPerDegree = 0.12f;
        [SerializeField] private float maxRotationSpeed = 18f;

        [Header("Canvas")]
        [SerializeField] private Camera uiCamera;
        [SerializeField] private ControllerRaycaster controllerRaycaster;
        [SerializeField] private TrackingManager trackingManager;
        [SerializeField] private float canvasScale = 0.0012f;

        private readonly Dictionary<string, GameObject> _rowMenus = new Dictionary<string, GameObject>();
        private readonly List<RaycastResult> _uiRaycastResults = new List<RaycastResult>();
        private RectTransform _canvasRoot;
        private RectTransform _listRoot;
        private GraphicRaycaster _graphicRaycaster;
        private TextMeshProUGUI _statusText;
        private TextMeshProUGUI _emptyText;
        private GameObject _renameDialog;
        private TMP_InputField _renameInput;
        private string _renameObjectId = string.Empty;
        private bool _panelVisible;
        private bool _initializedPose;
        private PointerEventData _pointerEventData;
        private EventSystem _pointerEventSystem;
        private GameObject _hoveredObject;
        private GameObject _pressedObject;

        private static DeviceArchivePanel _instance;

        public static bool IsPanelVisible => _instance != null && _instance._panelVisible;

        public static DeviceArchivePanel EnsureExists(Camera camera = null)
        {
            if (_instance != null)
            {
                _instance.SetCamera(camera);
                return _instance;
            }

            DeviceArchivePanel existing = FindFirstObjectByType<DeviceArchivePanel>();
            if (existing != null)
            {
                _instance = existing;
                existing.SetCamera(camera);
                return existing;
            }

            GameObject go = GameObject.Find(DefaultObjectName);
            if (go == null)
                go = new GameObject(DefaultObjectName);

            DeviceArchivePanel panel = go.GetComponent<DeviceArchivePanel>();
            if (panel == null)
                panel = go.AddComponent<DeviceArchivePanel>();
            panel.SetCamera(camera);
            return panel;
        }

        public static void OpenPanel()
        {
            EnsureExists(Camera.main).ShowPanel();
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
            BuildUi();
            HidePanel();
        }

        private void LateUpdate()
        {
            ResolveReferences();
            if (!_panelVisible)
                return;

            FollowHeadWithLag();
            UpdateControllerUiInput();
        }

        private void OnDisable()
        {
            ClearHover();
            _pressedObject = null;
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
            if (trackingManager == null)
                trackingManager = FindFirstObjectByType<TrackingManager>();
        }

        private void ShowPanel()
        {
            if (!RoomCoordinateSystemPanel.HasEnteredRoom)
                return;

            ResolveReferences();
            _panelVisible = true;
            _initializedPose = false;
            if (_canvasRoot != null)
                _canvasRoot.gameObject.SetActive(true);
            CloseRenameDialog();
            StartCoroutine(RefreshObjectsAsync());
        }

        private void HidePanel()
        {
            _panelVisible = false;
            ClearHover();
            _pressedObject = null;
            if (_canvasRoot != null)
                _canvasRoot.gameObject.SetActive(false);
        }

        private void FollowHeadWithLag()
        {
            if (headTarget == null)
                return;

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
            if (headToPanel.sqrMagnitude < 0.0001f)
                return;

            Quaternion targetRotation = Quaternion.LookRotation(headToPanel.normalized, Vector3.up);
            float angle = Quaternion.Angle(transform.rotation, targetRotation);
            float rotationSpeed = Mathf.Clamp(minRotationSpeed + angle * rotationSpeedPerDegree, minRotationSpeed, maxRotationSpeed);
            float rt = 1f - Mathf.Exp(-rotationSpeed * Time.deltaTime);
            transform.rotation = Quaternion.Slerp(transform.rotation, targetRotation, rt);
        }

        private void UpdateControllerUiInput()
        {
            if (!TryRaycastPanel(out GameObject currentTarget, out PointerEventData pointerData))
            {
                ClearHover();
                if (OVRInput.GetUp(OVRInput.RawButton.RIndexTrigger))
                    ReleasePressedObject(pointerData, null);
                return;
            }

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
            _pointerEventData.pointerId = -102;

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

        private void BuildUi()
        {
            if (_canvasRoot != null)
                return;

            EnsureEventSystem();

            var canvasObject = new GameObject("DeviceArchiveCanvas",
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
            background.color = new Color(0.035f, 0.04f, 0.05f, 0.9f);

            TextMeshProUGUI title = CreateText(_canvasRoot, "Title", "Completed Devices", 34f, FontStyles.Bold, TextAlignmentOptions.Left);
            SetTopLeft(title.rectTransform, 28f, 24f, 430f, 44f);

            Button closeButton = CreateButton(_canvasRoot, "Close", "Close", new Color(0.22f, 0.24f, 0.28f, 0.95f));
            SetTopRight((RectTransform)closeButton.transform, 24f, 20f, 104f, 48f);
            closeButton.onClick.AddListener(HidePanel);

            _statusText = CreateText(_canvasRoot, "Status", string.Empty, 19f, FontStyles.Normal, TextAlignmentOptions.Left);
            SetTopLeft(_statusText.rectTransform, 28f, 72f, 690f, 54f);

            _listRoot = AddRect(_canvasRoot, "DeviceList");
            Stretch(_listRoot, 28f, 28f, 130f, 26f);

            _emptyText = CreateText(_listRoot, "EmptyText", "No completed devices.", 24f, FontStyles.Normal, TextAlignmentOptions.Center);
            Stretch(_emptyText.rectTransform, 12f, 12f, 24f, 24f);

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

            TextMeshProUGUI title = CreateText(dialogRect, "RenameTitle", "Rename device", 28f, FontStyles.Bold, TextAlignmentOptions.Left);
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

        private IEnumerator RefreshObjectsAsync()
        {
            ClearRows();
            SetStatus("Loading completed devices...");

            if (trackingManager == null)
            {
                SetStatus("TrackingManager missing");
                _emptyText.gameObject.SetActive(true);
                yield break;
            }

            string url = trackingManager.BuildViewerUrl(
                "/api/room/object/list?room_id=" + UnityWebRequest.EscapeURL(RoomCoordinateSystemPanel.CurrentRoomId) +
                "&room_name=" + UnityWebRequest.EscapeURL(RoomCoordinateSystemPanel.CurrentRoomName) +
                "&device_id=" + UnityWebRequest.EscapeURL(SystemInfo.deviceUniqueIdentifier) +
                "&device_name=" + UnityWebRequest.EscapeURL(SystemInfo.deviceName) +
                "&device_model=" + UnityWebRequest.EscapeURL(SystemInfo.deviceModel));

            using (UnityWebRequest request = UnityWebRequest.Get(url))
            {
                yield return request.SendWebRequest();
                if (request.result != UnityWebRequest.Result.Success)
                {
                    SetStatus("List error: " + request.error);
                    _emptyText.gameObject.SetActive(true);
                    yield break;
                }

                ObjectListResponse response = null;
                try
                {
                    response = JsonUtility.FromJson<ObjectListResponse>(request.downloadHandler.text);
                }
                catch (Exception ex)
                {
                    SetStatus("List JSON error: " + ex.Message);
                    _emptyText.gameObject.SetActive(true);
                    yield break;
                }

                DeviceObjectRecord[] records = response != null && response.objects != null
                    ? response.objects
                    : Array.Empty<DeviceObjectRecord>();
                _emptyText.gameObject.SetActive(records.Length == 0);
                SetStatus(records.Length == 0 ? "No completed devices in this room." : $"{records.Length} completed device(s)");

                float y = 0f;
                for (int i = 0; i < records.Length; i++)
                {
                    CreateObjectRow(records[i], y);
                    y += RowHeight + RowGap;
                }
            }
        }

        private void ClearRows()
        {
            _rowMenus.Clear();
            if (_listRoot == null)
                return;
            for (int i = _listRoot.childCount - 1; i >= 0; i--)
            {
                Transform child = _listRoot.GetChild(i);
                if (_emptyText != null && child == _emptyText.transform)
                    continue;
                Destroy(child.gameObject);
            }
        }

        private void CreateObjectRow(DeviceObjectRecord record, float y)
        {
            var rowObject = new GameObject("DeviceRow_" + record.object_id, typeof(RectTransform), typeof(Image), typeof(Button));
            rowObject.transform.SetParent(_listRoot, false);
            RectTransform rowRect = (RectTransform)rowObject.transform;
            rowRect.anchorMin = new Vector2(0f, 1f);
            rowRect.anchorMax = new Vector2(1f, 1f);
            rowRect.pivot = new Vector2(0.5f, 1f);
            rowRect.offsetMin = new Vector2(0f, -y - RowHeight);
            rowRect.offsetMax = new Vector2(0f, -y);

            bool selected = RoomObjectSession.HasCurrentObject && RoomObjectSession.CurrentObjectId == record.object_id;
            Color rowColor = selected ? new Color(0.12f, 0.24f, 0.38f, 0.95f) : new Color(0.12f, 0.13f, 0.15f, 0.95f);
            rowObject.GetComponent<Image>().color = rowColor;

            Button rowButton = rowObject.GetComponent<Button>();
            rowButton.onClick.AddListener(() => EnterObject(record.object_id));
            ConfigureButtonColors(rowButton, rowColor);

            RawImage thumb = CreateThumbnail(rowRect, "Thumbnail");
            SetTopLeft((RectTransform)thumb.transform, 12f, 10f, 96f, 72f);
            StartCoroutine(LoadThumbnailAsync(thumb, record));

            TextMeshProUGUI nameText = CreateText(rowRect, "Name", record.name, 22f, selected ? FontStyles.Bold : FontStyles.Normal, TextAlignmentOptions.Left);
            SetTopLeft(nameText.rectTransform, 124f, 11f, 470f, 30f);

            string bindingLabel = record.network_binding != null && !string.IsNullOrWhiteSpace(record.network_binding.canonical_device_id)
                ? " | bound: " + ShortText(record.network_binding.display_name, 32)
                : "";
            TextMeshProUGUI subText = CreateText(
                rowRect,
                "Sub",
                $"{record.image_count} image(s), +{record.positive_point_count}/-{record.negative_point_count}{bindingLabel}",
                16f,
                FontStyles.Normal,
                TextAlignmentOptions.Left);
            subText.color = new Color(0.72f, 0.77f, 0.82f, 1f);
            SetTopLeft(subText.rectTransform, 124f, 48f, 470f, 24f);

            Button moreButton = CreateButton(rowRect, "More", "...", new Color(0.18f, 0.19f, 0.22f, 0.96f));
            SetTopRight((RectTransform)moreButton.transform, 10f, 26f, 54f, 40f);
            moreButton.onClick.AddListener(() => ToggleRowMenu(record.object_id));

            GameObject menu = CreateRowMenu(rowRect, record);
            menu.SetActive(false);
            _rowMenus[record.object_id] = menu;
        }

        private RawImage CreateThumbnail(Transform parent, string name)
        {
            var go = new GameObject(name, typeof(RectTransform), typeof(CanvasRenderer), typeof(RawImage));
            go.transform.SetParent(parent, false);
            RawImage image = go.GetComponent<RawImage>();
            image.color = Color.white;
            image.raycastTarget = false;
            return image;
        }

        private IEnumerator LoadThumbnailAsync(RawImage target, DeviceObjectRecord record)
        {
            if (target == null || trackingManager == null)
                yield break;

            string url = trackingManager.BuildViewerUrl(
                "/api/room/object/thumbnail?room_id=" + UnityWebRequest.EscapeURL(RoomCoordinateSystemPanel.CurrentRoomId) +
                "&device_id=" + UnityWebRequest.EscapeURL(SystemInfo.deviceUniqueIdentifier) +
                "&object_id=" + UnityWebRequest.EscapeURL(record.object_id) +
                "&t=" + record.thumbnail_version);

            using (UnityWebRequest request = UnityWebRequestTexture.GetTexture(url, nonReadable: false))
            {
                yield return request.SendWebRequest();
                if (request.result == UnityWebRequest.Result.Success && target != null)
                    target.texture = DownloadHandlerTexture.GetContent(request);
            }
        }

        private GameObject CreateRowMenu(RectTransform rowRect, DeviceObjectRecord record)
        {
            var menuObject = new GameObject("Menu_" + record.object_id, typeof(RectTransform), typeof(Image));
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
            renameButton.onClick.AddListener(() => OpenRenameDialog(record.object_id, record.name));

            Button deleteButton = CreateButton(menuRect, "Delete", "Delete", new Color(0.48f, 0.12f, 0.12f, 0.98f));
            SetTopRight((RectTransform)deleteButton.transform, 6f, 6f, 96f, 34f);
            deleteButton.onClick.AddListener(() => DeleteObject(record.object_id));

            return menuObject;
        }

        private void ToggleRowMenu(string objectId)
        {
            foreach (KeyValuePair<string, GameObject> entry in _rowMenus)
            {
                bool shouldShow = entry.Key == objectId && !entry.Value.activeSelf;
                entry.Value.SetActive(shouldShow);
            }
        }

        private async void EnterObject(string objectId)
        {
            if (trackingManager == null || string.IsNullOrWhiteSpace(objectId))
                return;

            SetStatus("Opening device...");
            TrackingManager.ObjectActionResponse response = await trackingManager.BeginEditObjectAsync(objectId);
            if (response == null || !response.ok)
            {
                SetStatus("Open failed: " + (response != null ? response.reason : "unknown"));
                return;
            }

            RoomObjectSession.EnterSavedObject(response.object_id, response.edit_session_id);
            DeviceAnnotationController.ShowPromptMarkers(response.points);
            DevicePlaceholderBoardManager.PlaceForObject(response.object_id, response.spatial, response.points, uiCamera);
            HidePanel();
        }

        private void OpenRenameDialog(string objectId, string currentName)
        {
            _renameObjectId = objectId;
            _renameInput.text = currentName ?? string.Empty;
            _renameDialog.SetActive(true);
            _renameInput.ActivateInputField();
        }

        private async void CommitRename()
        {
            if (trackingManager == null)
                return;

            string nextName = (_renameInput.text ?? string.Empty).Trim();
            if (string.IsNullOrWhiteSpace(_renameObjectId) || string.IsNullOrWhiteSpace(nextName))
            {
                CloseRenameDialog();
                return;
            }

            bool ok = await trackingManager.RenameObjectAsync(_renameObjectId, nextName);
            CloseRenameDialog();
            if (ok)
                StartCoroutine(RefreshObjectsAsync());
            else
                SetStatus("Rename failed");
        }

        private void CloseRenameDialog()
        {
            _renameObjectId = string.Empty;
            if (_renameDialog != null)
                _renameDialog.SetActive(false);
        }

        private async void DeleteObject(string objectId)
        {
            if (trackingManager == null || string.IsNullOrWhiteSpace(objectId))
                return;

            bool ok = await trackingManager.DeleteObjectAsync(objectId);
            if (!ok)
            {
                SetStatus("Delete failed");
                return;
            }

            if (RoomObjectSession.HasCurrentObject && RoomObjectSession.CurrentObjectId == objectId)
                RoomObjectSession.StartNewObject();
            DevicePlaceholderBoardManager.RemoveForObject(objectId);
            StartCoroutine(RefreshObjectsAsync());
        }

        private void SetStatus(string text)
        {
            if (_statusText == null)
                return;
            _statusText.text = ShortText(text, 96);
        }

        private static string ShortText(string text, int maxLength)
        {
            if (string.IsNullOrWhiteSpace(text))
                return string.Empty;
            string cleaned = text.Replace('\r', ' ').Replace('\n', ' ').Trim();
            if (cleaned.Length <= maxLength)
                return cleaned;
            return cleaned.Substring(0, Mathf.Max(0, maxLength - 3)) + "...";
        }

        private static void EnsureEventSystem()
        {
            if (FindFirstObjectByType<EventSystem>() != null)
                return;

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

            TextMeshProUGUI label = CreateText(go.transform, "Label", text, 20f, FontStyles.Bold, TextAlignmentOptions.Center);
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

            TextMeshProUGUI placeholder = CreateText(viewport, "Placeholder", "Device name", 22f, FontStyles.Italic, TextAlignmentOptions.Left);
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

        private static void SetBottomRight(RectTransform rectTransform, float x, float y, float width, float height)
        {
            rectTransform.anchorMin = new Vector2(1f, 0f);
            rectTransform.anchorMax = new Vector2(1f, 0f);
            rectTransform.pivot = new Vector2(1f, 0f);
            rectTransform.anchoredPosition = new Vector2(-x, y);
            rectTransform.sizeDelta = new Vector2(width, height);
        }

        [Serializable]
        private sealed class ObjectListResponse
        {
            public bool ok = false;
            public string room_id = string.Empty;
            public string device_id = string.Empty;
            public DeviceObjectRecord[] objects = Array.Empty<DeviceObjectRecord>();
        }

        [Serializable]
        private sealed class DeviceObjectRecord
        {
            public string object_id = string.Empty;
            public string name = string.Empty;
            public string status = string.Empty;
            public long completed_at_ms = 0;
            public long updated_at_ms = 0;
            public int image_count = 0;
            public int point_count = 0;
            public int positive_point_count = 0;
            public int negative_point_count = 0;
            public long thumbnail_version = 0;
            public TrackingManager.NetworkBindingRecord network_binding = new TrackingManager.NetworkBindingRecord();
        }
    }
}
