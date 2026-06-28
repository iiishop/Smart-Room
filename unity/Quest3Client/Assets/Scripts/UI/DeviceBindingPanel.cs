using System;
using System.Collections;
using System.Collections.Generic;
using System.Text;
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
    public sealed class DeviceBindingPanel : MonoBehaviour
    {
        private const string DefaultObjectName = "DeviceBindingPanel";
        private const float PanelWidth = 840f;
        private const float PanelHeight = 660f;
        private const float RowHeight = 54f;
        private const float RowGap = 6f;
        private const int CandidateLimit = 10;

        [Header("References")]
        [SerializeField] private Camera uiCamera;
        [SerializeField] private Transform leftHandAnchor;
        [SerializeField] private ControllerRaycaster controllerRaycaster;
        [SerializeField] private TrackingManager trackingManager;

        [Header("Placement")]
        [SerializeField] private Vector3 leftHandLocalOffset = new Vector3(0.02f, 0.16f, 0.30f);
        [SerializeField] private Vector3 headFallbackOffset = new Vector3(-0.34f, -0.04f, 0.74f);
        [SerializeField] private float positionLerp = 12f;
        [SerializeField] private float rotationLerp = 12f;
        [SerializeField] private float canvasScale = 0.00095f;

        [Header("Refresh")]
        [SerializeField] private float refreshIntervalSeconds = 1.0f;

        private readonly List<RaycastResult> _uiRaycastResults = new List<RaycastResult>();
        private readonly Dictionary<string, TrackingManager.PairingCandidateRecord> _candidateById =
            new Dictionary<string, TrackingManager.PairingCandidateRecord>();

        private RectTransform _canvasRoot;
        private RectTransform _listRoot;
        private GraphicRaycaster _graphicRaycaster;
        private CanvasGroup _canvasGroup;
        private TextMeshProUGUI _titleText;
        private TextMeshProUGUI _statusText;
        private TextMeshProUGUI _bindingText;
        private Button _refreshButton;
        private Button _unbindButton;
        private GameObject _emptyObject;
        private PointerEventData _pointerEventData;
        private EventSystem _pointerEventSystem;
        private GameObject _hoveredObject;
        private GameObject _pressedObject;
        private string _objectId = string.Empty;
        private string _objectName = string.Empty;
        private bool _panelVisible;
        private bool _initializedPose;
        private bool _requestInFlight;
        private bool _refreshRequested;
        private float _nextRefreshAt;
        private float _openedAt;
        private string _activeStatusStage = string.Empty;
        private string _activeStatusMessage = string.Empty;
        private float _activeStatusStartedAt;
        private int _activeStatusAnimationFrame = -1;

        private static DeviceBindingPanel _instance;

        public static bool IsPanelVisible => _instance != null && _instance._panelVisible;

        public static DeviceBindingPanel EnsureExists(Camera camera = null)
        {
            if (_instance != null)
            {
                _instance.SetCamera(camera);
                return _instance;
            }

            DeviceBindingPanel existing = FindFirstObjectByType<DeviceBindingPanel>();
            if (existing != null)
            {
                _instance = existing;
                existing.SetCamera(camera);
                return existing;
            }

            GameObject go = GameObject.Find(DefaultObjectName);
            if (go == null)
                go = new GameObject(DefaultObjectName);

            DeviceBindingPanel panel = go.GetComponent<DeviceBindingPanel>();
            if (panel == null)
                panel = go.AddComponent<DeviceBindingPanel>();
            panel.SetCamera(camera);
            return panel;
        }

        public static void OpenForObject(string objectId, Camera camera = null)
        {
            if (string.IsNullOrWhiteSpace(objectId))
                return;
            EnsureExists(camera).ShowForObject(objectId);
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

            FollowLeftHandWithLag();
            UpdateFadeIn();
            UpdateActiveStatus();
            UpdateControllerUiInput();

            if (!_requestInFlight && (_refreshRequested || Time.time >= _nextRefreshAt))
            {
                _refreshRequested = false;
                _nextRefreshAt = Time.time + Mathf.Max(0.25f, refreshIntervalSeconds);
                StartCoroutine(RefreshPairingAsync());
            }
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
            if (trackingManager == null)
                trackingManager = FindFirstObjectByType<TrackingManager>();
            if (controllerRaycaster == null)
                controllerRaycaster = FindFirstObjectByType<ControllerRaycaster>();
            if (leftHandAnchor == null)
                leftHandAnchor = FindLeftHandAnchor();
        }

        private static Transform FindLeftHandAnchor()
        {
            string[] names =
            {
                "LeftHandAnchor",
                "LeftControllerAnchor",
                "LeftHand Controller",
                "LeftController",
                "LTouch"
            };
            for (int i = 0; i < names.Length; i++)
            {
                GameObject found = GameObject.Find(names[i]);
                if (found != null)
                    return found.transform;
            }
            return null;
        }

        private void ShowForObject(string objectId)
        {
            ResolveReferences();
            _objectId = objectId;
            _objectName = string.Empty;
            _panelVisible = true;
            _initializedPose = false;
            _openedAt = Time.time;
            _refreshRequested = true;
            _nextRefreshAt = 0f;
            _activeStatusStage = string.Empty;
            _activeStatusMessage = string.Empty;
            if (_canvasRoot != null)
                _canvasRoot.gameObject.SetActive(true);
            if (_canvasGroup != null)
                _canvasGroup.alpha = 0f;
            ClearRows();
            SetTitle("Network Binding");
            SetActiveStatus("loading", "Loading pairing status...");
            SetBinding(null);
        }

        private void HidePanel()
        {
            _panelVisible = false;
            _activeStatusStage = string.Empty;
            _activeStatusMessage = string.Empty;
            ClearHover();
            _pressedObject = null;
            if (_canvasRoot != null)
                _canvasRoot.gameObject.SetActive(false);
        }

        private void FollowLeftHandWithLag()
        {
            if (uiCamera == null)
                return;

            Vector3 targetPosition = leftHandAnchor != null
                ? leftHandAnchor.TransformPoint(leftHandLocalOffset)
                : uiCamera.transform.TransformPoint(headFallbackOffset);

            Vector3 toHead = uiCamera.transform.position - targetPosition;
            Quaternion targetRotation = toHead.sqrMagnitude > 0.0001f
                ? Quaternion.LookRotation(-toHead.normalized, Vector3.up)
                : uiCamera.transform.rotation;

            if (!_initializedPose)
            {
                transform.SetPositionAndRotation(targetPosition, targetRotation);
                _initializedPose = true;
                return;
            }

            float pt = 1f - Mathf.Exp(-positionLerp * Time.deltaTime);
            float rt = 1f - Mathf.Exp(-rotationLerp * Time.deltaTime);
            transform.position = Vector3.Lerp(transform.position, targetPosition, pt);
            transform.rotation = Quaternion.Slerp(transform.rotation, targetRotation, rt);
        }

        private void UpdateFadeIn()
        {
            if (_canvasGroup == null)
                return;
            float t = Mathf.InverseLerp(0f, 0.18f, Time.time - _openedAt);
            _canvasGroup.alpha = Mathf.Clamp01(t);
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
            _pointerEventData.pointerId = -104;

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

            GameObject canvasObject = new GameObject(
                "DeviceBindingCanvas",
                typeof(RectTransform),
                typeof(Canvas),
                typeof(CanvasScaler),
                typeof(GraphicRaycaster),
                typeof(TrackedDeviceRaycaster),
                typeof(CanvasGroup),
                typeof(Image));
            canvasObject.transform.SetParent(transform, false);

            _canvasRoot = canvasObject.GetComponent<RectTransform>();
            _canvasRoot.sizeDelta = new Vector2(PanelWidth, PanelHeight);
            _canvasRoot.localScale = Vector3.one * canvasScale;

            Canvas canvas = canvasObject.GetComponent<Canvas>();
            canvas.renderMode = RenderMode.WorldSpace;
            canvas.worldCamera = uiCamera;
            canvas.sortingOrder = 28;
            _graphicRaycaster = canvasObject.GetComponent<GraphicRaycaster>();
            _canvasGroup = canvasObject.GetComponent<CanvasGroup>();

            CanvasScaler scaler = canvasObject.GetComponent<CanvasScaler>();
            scaler.dynamicPixelsPerUnit = 1000f;
            scaler.referencePixelsPerUnit = 100f;

            canvasObject.GetComponent<Image>().color = new Color(0.035f, 0.04f, 0.05f, 0.94f);

            _titleText = CreateText(_canvasRoot, "Title", "Network Binding", 34f, FontStyles.Bold, TextAlignmentOptions.Left);
            SetTopLeft(_titleText.rectTransform, 28f, 22f, 510f, 42f);

            Button closeButton = CreateButton(_canvasRoot, "Close", "Close", new Color(0.22f, 0.24f, 0.28f, 0.95f));
            SetTopRight((RectTransform)closeButton.transform, 24f, 18f, 104f, 46f);
            closeButton.onClick.AddListener(HidePanel);

            _refreshButton = CreateButton(_canvasRoot, "Refresh", "Refresh", new Color(0.15f, 0.34f, 0.58f, 0.95f));
            SetTopRight((RectTransform)_refreshButton.transform, 138f, 18f, 110f, 46f);
            _refreshButton.onClick.AddListener(RequestRefresh);

            _unbindButton = CreateButton(_canvasRoot, "Unbind", "Unbind", new Color(0.42f, 0.22f, 0.12f, 0.95f));
            SetTopRight((RectTransform)_unbindButton.transform, 258f, 18f, 108f, 46f);
            _unbindButton.onClick.AddListener(() => StartCoroutine(UnbindAsync()));

            _statusText = CreateText(_canvasRoot, "Status", string.Empty, 19f, FontStyles.Normal, TextAlignmentOptions.Left);
            SetTopLeft(_statusText.rectTransform, 28f, 70f, 784f, 44f);

            _bindingText = CreateText(_canvasRoot, "Binding", "Binding: none", 19f, FontStyles.Normal, TextAlignmentOptions.Left);
            _bindingText.color = new Color(0.88f, 0.92f, 0.96f, 1f);
            SetTopLeft(_bindingText.rectTransform, 28f, 112f, 784f, 36f);

            _listRoot = AddRect(_canvasRoot, "CandidateList");
            Stretch(_listRoot, 28f, 28f, 158f, 24f);

            _emptyObject = CreateText(_listRoot, "Empty", "No candidates yet.", 24f, FontStyles.Normal, TextAlignmentOptions.Center).gameObject;
            Stretch((RectTransform)_emptyObject.transform, 12f, 12f, 24f, 24f);
        }

        private void RequestRefresh()
        {
            if (!_requestInFlight)
                StartCoroutine(RequestPairingRefreshAsync());
        }

        private IEnumerator RequestPairingRefreshAsync()
        {
            if (trackingManager == null || string.IsNullOrWhiteSpace(_objectId))
                yield break;

            SetActiveStatus("refresh", "Starting VLM or refreshing match candidates...");
            string url = trackingManager.BuildViewerUrl("/api/room/object/pairing/refresh");
            ObjectActionPayload payload = BuildObjectPayload(_objectId, string.Empty);
            using (UnityWebRequest request = JsonPost(url, payload))
            {
                yield return request.SendWebRequest();
                if (request.result != UnityWebRequest.Result.Success)
                {
                    SetStatus("Refresh failed: " + ShortText(request.downloadHandler.text, 90));
                    yield break;
                }
            }
            _refreshRequested = true;
        }

        private IEnumerator RefreshPairingAsync()
        {
            if (trackingManager == null || string.IsNullOrWhiteSpace(_objectId))
                yield break;

            _requestInFlight = true;
            string url = trackingManager.BuildViewerUrl(
                "/api/room/object/pairing/candidates?room_id=" + UnityWebRequest.EscapeURL(RoomCoordinateSystemPanel.CurrentRoomId) +
                "&room_name=" + UnityWebRequest.EscapeURL(RoomCoordinateSystemPanel.CurrentRoomName) +
                "&device_id=" + UnityWebRequest.EscapeURL(SystemInfo.deviceUniqueIdentifier) +
                "&device_name=" + UnityWebRequest.EscapeURL(SystemInfo.deviceName) +
                "&device_model=" + UnityWebRequest.EscapeURL(SystemInfo.deviceModel) +
                "&object_id=" + UnityWebRequest.EscapeURL(_objectId) +
                "&limit=" + CandidateLimit +
                "&compact=1");

            using (UnityWebRequest request = UnityWebRequest.Get(url))
            {
                request.timeout = 15;
                yield return request.SendWebRequest();
                _requestInFlight = false;
                if (request.result != UnityWebRequest.Result.Success)
                {
                    SetStatus("Candidates error: " + ShortText(request.downloadHandler.text, 90));
                    yield break;
                }

                PairingCandidatesResponse response = null;
                try
                {
                    response = JsonUtility.FromJson<PairingCandidatesResponse>(request.downloadHandler.text);
                }
                catch (Exception ex)
                {
                    SetStatus("Candidates JSON error: " + ex.Message);
                    yield break;
                }

                if (response == null || !response.ok)
                {
                    SetStatus("Candidates error: " + (response != null ? response.reason : "unknown"));
                    yield break;
                }

                RenderResponse(response);
            }
        }

        private void RenderResponse(PairingCandidatesResponse response)
        {
            _objectName = string.IsNullOrWhiteSpace(response.object_name) ? _objectId : response.object_name;
            SetTitle("Network Binding: " + ShortText(_objectName, 34));
            SetBinding(response.binding);

            string status = BuildStatus(response);
            string vlm = string.IsNullOrWhiteSpace(response.vlm_status) ? "not_started" : response.vlm_status;
            string pairing = string.IsNullOrWhiteSpace(response.pairing_status) ? "not_started" : response.pairing_status;
            if (vlm == "processing")
                SetActiveStatus("vlm", status);
            else if (pairing == "processing")
                SetActiveStatus("pairing", status);
            else if (pairing == "waiting_for_vlm" &&
                     string.IsNullOrWhiteSpace(response.vlm_error) &&
                     string.IsNullOrWhiteSpace(response.pairing_error))
                SetActiveStatus("waiting_for_vlm", "Waiting for VLM analysis...");
            else
                SetStatus(
                    status,
                    !string.IsNullOrWhiteSpace(response.vlm_error) ||
                    !string.IsNullOrWhiteSpace(response.pairing_error));

            TrackingManager.PairingCandidateRecord[] candidates = response.candidates != null
                ? response.candidates
                : Array.Empty<TrackingManager.PairingCandidateRecord>();
            RenderCandidates(candidates);
        }

        private string BuildStatus(PairingCandidatesResponse response)
        {
            string vlm = string.IsNullOrWhiteSpace(response.vlm_status) ? "not_started" : response.vlm_status;
            string pairing = string.IsNullOrWhiteSpace(response.pairing_status) ? "not_started" : response.pairing_status;
            if (vlm == "processing")
                return "VLM is analyzing device images...";
            if (!string.IsNullOrWhiteSpace(response.vlm_error))
                return "VLM: " + ShortText(response.vlm_error, 92);
            if (pairing == "processing")
                return "Matching visual profile with network devices...";
            if (!string.IsNullOrWhiteSpace(response.pairing_error))
                return "Matching: " + ShortText(response.pairing_error, 92);
            if (!string.IsNullOrWhiteSpace(response.pairing_warning))
                return "Matching fallback: " + ShortText(response.pairing_warning, 88);
            int count = response.candidates != null ? response.candidates.Length : 0;
            if (count == 0)
                return "No candidates. Check discovery runtime or refresh.";
            return response.evaluated_candidate_count > 0
                ? $"Top {count} of {response.evaluated_candidate_count} evaluated network device(s)"
                : $"Top {count} network candidate(s)";
        }

        private void SetBinding(TrackingManager.NetworkBindingRecord binding)
        {
            bool hasBinding = binding != null && !string.IsNullOrWhiteSpace(binding.canonical_device_id);
            _bindingText.text = hasBinding
                ? $"Binding: {ShortText(binding.display_name, 62)}  ({binding.score}%)"
                : "Binding: none";
            if (_unbindButton != null)
                _unbindButton.interactable = hasBinding;
        }

        private void RenderCandidates(TrackingManager.PairingCandidateRecord[] candidates)
        {
            ClearRows();
            if (_emptyObject != null)
                _emptyObject.SetActive(candidates == null || candidates.Length == 0);
            if (candidates == null)
                return;

            float y = 0f;
            for (int i = 0; i < candidates.Length && i < CandidateLimit; i++)
            {
                TrackingManager.PairingCandidateRecord candidate = candidates[i];
                if (candidate == null || string.IsNullOrWhiteSpace(candidate.canonical_device_id))
                    continue;
                CreateCandidateRow(candidate, y);
                y += RowHeight + RowGap;
            }
        }

        private void ClearRows()
        {
            _candidateById.Clear();
            if (_listRoot == null)
                return;
            for (int i = _listRoot.childCount - 1; i >= 0; i--)
            {
                Transform child = _listRoot.GetChild(i);
                if (_emptyObject != null && child == _emptyObject.transform)
                    continue;
                Destroy(child.gameObject);
            }
        }

        private void CreateCandidateRow(TrackingManager.PairingCandidateRecord candidate, float y)
        {
            string candidateId = candidate.canonical_device_id;
            _candidateById[candidateId] = candidate;

            GameObject rowObject = new GameObject("Candidate_" + candidate.rank, typeof(RectTransform), typeof(Image), typeof(Button));
            rowObject.transform.SetParent(_listRoot, false);
            RectTransform rowRect = (RectTransform)rowObject.transform;
            rowRect.anchorMin = new Vector2(0f, 1f);
            rowRect.anchorMax = new Vector2(1f, 1f);
            rowRect.pivot = new Vector2(0.5f, 1f);
            rowRect.offsetMin = new Vector2(0f, -y - RowHeight);
            rowRect.offsetMax = new Vector2(0f, -y);

            Color rowColor = new Color(0.10f, 0.115f, 0.135f, 0.96f);
            rowObject.GetComponent<Image>().color = rowColor;
            Button rowButton = rowObject.GetComponent<Button>();
            ConfigureButtonColors(rowButton, rowColor);
            rowButton.onClick.AddListener(() => StartCoroutine(BindCandidateAsync(candidateId)));

            TextMeshProUGUI nameText = CreateText(
                rowRect,
                "Name",
                $"{candidate.rank}. {ShortText(candidate.display_name, 58)}",
                18f,
                FontStyles.Bold,
                TextAlignmentOptions.Left);
            SetTopLeft(nameText.rectTransform, 12f, 7f, 560f, 22f);

            TextMeshProUGUI scoreText = CreateText(
                rowRect,
                "Score",
                $"{candidate.score}%",
                22f,
                FontStyles.Bold,
                TextAlignmentOptions.Center);
            scoreText.color = ScoreColor(candidate.score);
            SetTopRight(scoreText.rectTransform, 10f, 9f, 74f, 26f);

            string details = BuildCandidateDetails(candidate);
            TextMeshProUGUI detailText = CreateText(rowRect, "Detail", details, 14f, FontStyles.Normal, TextAlignmentOptions.Left);
            detailText.color = new Color(0.74f, 0.79f, 0.84f, 1f);
            SetTopLeft(detailText.rectTransform, 12f, 31f, 730f, 18f);
        }

        private static string BuildCandidateDetails(TrackingManager.PairingCandidateRecord candidate)
        {
            TrackingManager.NetworkProfileRecord profile = candidate.profile;
            string type = profile != null && !string.IsNullOrWhiteSpace(profile.device_type)
                ? profile.device_type
                : "unknown";
            string caps = profile != null && profile.capabilities != null && profile.capabilities.Length > 0
                ? string.Join(", ", profile.capabilities)
                : "";
            string address = profile != null ? profile.address_summary : "";
            string ids = profile != null ? profile.identifier_summary : "";
            string evidence = $"evidence {candidate.evidence_coverage_percent}%";
            string right = !string.IsNullOrWhiteSpace(address) ? address : ids;
            return ShortText($"{type} | {caps} | {evidence} | {right}", 96);
        }

        private IEnumerator BindCandidateAsync(string canonicalId)
        {
            if (trackingManager == null || string.IsNullOrWhiteSpace(_objectId) || string.IsNullOrWhiteSpace(canonicalId))
                yield break;

            TrackingManager.PairingCandidateRecord candidate = null;
            _candidateById.TryGetValue(canonicalId, out candidate);
            SetActiveStatus(
                "binding",
                "Binding " + ShortText(candidate != null ? candidate.display_name : canonicalId, 56) + "...");

            string url = trackingManager.BuildViewerUrl("/api/room/object/pairing/bind");
            ObjectActionPayload payload = BuildObjectPayload(_objectId, canonicalId);
            using (UnityWebRequest request = JsonPost(url, payload))
            {
                yield return request.SendWebRequest();
                if (request.result != UnityWebRequest.Result.Success)
                {
                    SetStatus("Bind failed: " + ShortText(request.downloadHandler.text, 96));
                    yield break;
                }

                BindingResponse response = null;
                try
                {
                    response = JsonUtility.FromJson<BindingResponse>(request.downloadHandler.text);
                }
                catch (Exception ex)
                {
                    SetStatus("Bind JSON error: " + ex.Message);
                    yield break;
                }

                if (response == null || !response.ok)
                {
                    SetStatus("Bind failed: " + (response != null ? response.reason : "unknown"));
                    yield break;
                }

                SetBinding(response.binding);
                SetStatus("Bound. You can change it later from this panel.");
                _refreshRequested = true;
            }
        }

        private IEnumerator UnbindAsync()
        {
            if (trackingManager == null || string.IsNullOrWhiteSpace(_objectId))
                yield break;

            SetActiveStatus("unbinding", "Removing binding...");
            string url = trackingManager.BuildViewerUrl("/api/room/object/pairing/unbind");
            ObjectActionPayload payload = BuildObjectPayload(_objectId, string.Empty);
            using (UnityWebRequest request = JsonPost(url, payload))
            {
                yield return request.SendWebRequest();
                if (request.result != UnityWebRequest.Result.Success)
                {
                    SetStatus("Unbind failed: " + ShortText(request.downloadHandler.text, 96));
                    yield break;
                }

                SetBinding(null);
                SetStatus("Binding removed.");
                _refreshRequested = true;
            }
        }

        private static UnityWebRequest JsonPost(string url, object payload)
        {
            string json = JsonUtility.ToJson(payload);
            byte[] body = Encoding.UTF8.GetBytes(json);
            UnityWebRequest request = new UnityWebRequest(url, "POST");
            request.uploadHandler = new UploadHandlerRaw(body);
            request.downloadHandler = new DownloadHandlerBuffer();
            request.SetRequestHeader("Content-Type", "application/json");
            request.timeout = 20;
            return request;
        }

        private static ObjectActionPayload BuildObjectPayload(string objectId, string canonicalId)
        {
            return new ObjectActionPayload
            {
                room_id = RoomCoordinateSystemPanel.CurrentRoomId,
                room_name = RoomCoordinateSystemPanel.CurrentRoomName,
                device_id = SystemInfo.deviceUniqueIdentifier,
                device_name = SystemInfo.deviceName,
                device_model = SystemInfo.deviceModel,
                object_id = objectId,
                object_session_id = objectId,
                canonical_device_id = canonicalId,
                timestamp_ms = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
            };
        }

        private void SetTitle(string text)
        {
            if (_titleText != null)
                _titleText.text = text;
        }

        private void SetStatus(string text, bool forceError = false)
        {
            _activeStatusStage = string.Empty;
            _activeStatusMessage = string.Empty;
            if (_statusText != null)
            {
                _statusText.text = ShortText(text, 120);
                bool isError = forceError ||
                    text.IndexOf("error", StringComparison.OrdinalIgnoreCase) >= 0 ||
                    text.IndexOf("failed", StringComparison.OrdinalIgnoreCase) >= 0 ||
                    text.IndexOf("not configured", StringComparison.OrdinalIgnoreCase) >= 0;
                _statusText.color = isError
                    ? new Color(1f, 0.34f, 0.28f, 1f)
                    : new Color(0.82f, 0.88f, 0.94f, 1f);
            }
        }

        private void SetActiveStatus(string stage, string message)
        {
            if (!string.Equals(_activeStatusStage, stage, StringComparison.Ordinal))
            {
                _activeStatusStage = stage;
                _activeStatusStartedAt = Time.time;
                _activeStatusAnimationFrame = -1;
            }
            _activeStatusMessage = message;
            if (_statusText != null)
                _statusText.color = new Color(0.28f, 0.82f, 1f, 1f);
            UpdateActiveStatus(force: true);
        }

        private void UpdateActiveStatus(bool force = false)
        {
            if (_statusText == null || string.IsNullOrWhiteSpace(_activeStatusStage))
                return;
            int frame = Mathf.FloorToInt((Time.time - _activeStatusStartedAt) * 4f);
            if (!force && frame == _activeStatusAnimationFrame)
                return;
            _activeStatusAnimationFrame = frame;
            float elapsed = Mathf.Max(0f, Time.time - _activeStatusStartedAt);
            string[] spinner = { "|", "/", "-", "\\" };
            _statusText.text =
                spinner[Mathf.Abs(frame) % spinner.Length] + " " +
                ShortText(_activeStatusMessage, 98) +
                $"  {elapsed:F0}s";
        }

        private static Color ScoreColor(int score)
        {
            if (score >= 75)
                return new Color(0.18f, 0.92f, 0.42f, 1f);
            if (score >= 45)
                return new Color(1f, 0.78f, 0.18f, 1f);
            return new Color(1f, 0.36f, 0.30f, 1f);
        }

        private static string ShortText(string text, int maxLength)
        {
            if (string.IsNullOrWhiteSpace(text))
                return string.Empty;
            string cleaned = text.Replace('\r', ' ').Replace('\n', ' ').Trim();
            while (cleaned.Contains("  ", StringComparison.Ordinal))
                cleaned = cleaned.Replace("  ", " ");
            if (cleaned.Length <= maxLength)
                return cleaned;
            return cleaned.Substring(0, Mathf.Max(0, maxLength - 3)) + "...";
        }

        private static RectTransform AddRect(Transform parent, string name)
        {
            GameObject go = new GameObject(name, typeof(RectTransform));
            go.transform.SetParent(parent, false);
            return (RectTransform)go.transform;
        }

        private static TextMeshProUGUI CreateText(Transform parent, string name, string text, float fontSize, FontStyles style, TextAlignmentOptions alignment)
        {
            GameObject go = new GameObject(name, typeof(RectTransform), typeof(CanvasRenderer), typeof(TextMeshProUGUI));
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
            GameObject go = new GameObject(name, typeof(RectTransform), typeof(Image), typeof(Button));
            go.transform.SetParent(parent, false);
            Image image = go.GetComponent<Image>();
            image.color = color;
            Button button = go.GetComponent<Button>();
            ConfigureButtonColors(button, color);

            TextMeshProUGUI label = CreateText(go.transform, "Label", text, 18f, FontStyles.Bold, TextAlignmentOptions.Center);
            Stretch(label.rectTransform, 4f, 4f, 4f, 4f);
            label.overflowMode = TextOverflowModes.Overflow;
            return button;
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

        private static void EnsureEventSystem()
        {
            if (FindFirstObjectByType<EventSystem>() != null)
                return;

            GameObject eventSystem = new GameObject("EventSystem", typeof(EventSystem), typeof(InputSystemUIInputModule));
            eventSystem.GetComponent<InputSystemUIInputModule>().AssignDefaultActions();
            eventSystem.SetActive(true);
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

        [Serializable]
        private sealed class PairingCandidatesResponse
        {
            public bool ok = false;
            public string reason = string.Empty;
            public string object_id = string.Empty;
            public string object_name = string.Empty;
            public string vlm_status = string.Empty;
            public string vlm_error = string.Empty;
            public string pairing_status = string.Empty;
            public string pairing_error = string.Empty;
            public string pairing_warning = string.Empty;
            public TrackingManager.PairingCandidateRecord[] candidates = Array.Empty<TrackingManager.PairingCandidateRecord>();
            public TrackingManager.NetworkBindingRecord binding = new TrackingManager.NetworkBindingRecord();
            public long started_at_ms = 0;
            public long completed_at_ms = 0;
            public int evaluated_candidate_count = 0;
            public int llm_candidate_count = 0;
        }

        [Serializable]
        private sealed class BindingResponse
        {
            public bool ok = false;
            public string reason = string.Empty;
            public string object_id = string.Empty;
            public TrackingManager.NetworkBindingRecord binding = new TrackingManager.NetworkBindingRecord();
        }

        [Serializable]
        private sealed class ObjectActionPayload
        {
            public string room_id = string.Empty;
            public string room_name = string.Empty;
            public string device_id = string.Empty;
            public string device_name = string.Empty;
            public string device_model = string.Empty;
            public string object_id = string.Empty;
            public string object_session_id = string.Empty;
            public string canonical_device_id = string.Empty;
            public long timestamp_ms = 0;
        }
    }
}
