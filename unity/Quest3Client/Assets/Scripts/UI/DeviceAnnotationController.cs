using System.Threading.Tasks;
using SmartRoom.Interaction;
using SmartRoom.Tracking;
using TMPro;
using UnityEngine;

namespace SmartRoom.UI
{
    public sealed class DeviceAnnotationController : MonoBehaviour
    {
        private const string DefaultObjectName = "DeviceAnnotationController";
        private const int RingSegments = 96;

        [Header("References")]
        [SerializeField] private Camera xrCamera;
        [SerializeField] private TrackingManager trackingManager;
        [SerializeField] private Transform leftHandAnchor;

        [Header("Input")]
        [SerializeField] private float ringDelaySeconds = 0.2f;
        [SerializeField] private float actionHoldSeconds = 1.2f;
        [SerializeField] private float leftGrabHoldSeconds = 0.3f;
        [SerializeField] private float grabPressThreshold = 0.72f;
        [SerializeField] private float grabReleaseThreshold = 0.35f;

        [Header("Ring")]
        [SerializeField] private Vector3 ringLeftHandOffset = new Vector3(0.02f, 0.12f, 0.22f);
        [SerializeField] private float ringRadius = 0.055f;
        [SerializeField] private float ringWidth = 0.006f;

        private GameObject _ringRoot;
        private LineRenderer _ringBack;
        private LineRenderer _ringProgress;
        private TextMeshPro _ringLabel;
        private Material _ringBackMaterial;
        private Material _ringProgressMaterial;
        private Material _ringAbortMaterial;
        private HoldAction _activeHold = HoldAction.None;
        private float _holdStartTime;
        private bool _holdTriggered;
        private bool _leftGrabHeld;
        private bool _leftGrabOpened;
        private float _leftGrabStartTime;
        private bool _actionInFlight;
        private bool _lastSaveHeld;
        private bool _lastAbandonHeld;

        private static DeviceAnnotationController _instance;
        private static readonly int BaseColorId = Shader.PropertyToID("_BaseColor");
        private static readonly int ColorId = Shader.PropertyToID("_Color");

        private enum HoldAction
        {
            None,
            Save,
            Abandon
        }

        public static DeviceAnnotationController EnsureExists(Camera camera = null)
        {
            if (_instance != null)
            {
                _instance.SetCamera(camera);
                return _instance;
            }

            DeviceAnnotationController existing = FindFirstObjectByType<DeviceAnnotationController>();
            if (existing != null)
            {
                _instance = existing;
                existing.SetCamera(camera);
                return existing;
            }

            GameObject go = GameObject.Find(DefaultObjectName);
            if (go == null)
                go = new GameObject(DefaultObjectName);

            DeviceAnnotationController controller = go.GetComponent<DeviceAnnotationController>();
            if (controller == null)
                controller = go.AddComponent<DeviceAnnotationController>();
            controller.SetCamera(camera);
            return controller;
        }

        public static void StartNewObjectFromMenu()
        {
            EnsureExists(Camera.main).StartNewObjectAfterAbandon();
        }

        public static void ShowPromptMarkers(TrackingManager.RoomObjectPointRecord[] points)
        {
            PromptPointMarkerManager.ClearMarkers();
            if (points == null)
                return;

            for (int i = 0; i < points.Length; i++)
            {
                TrackingManager.RoomObjectPointRecord point = points[i];
                if (point == null || point.world_xyz_m == null || point.world_xyz_m.Length < 3)
                    continue;
                Vector3 world = new Vector3(point.world_xyz_m[0], point.world_xyz_m[1], point.world_xyz_m[2]);
                PromptPointMarkerManager.AddMarker(world, point.label);
            }
        }

        public void SetCamera(Camera camera)
        {
            if (camera != null)
                xrCamera = camera;
        }

        private void Awake()
        {
            _instance = this;
            ResolveReferences();
            BuildRing();
            HideRing();
        }

        private void OnDestroy()
        {
            if (_instance == this)
                _instance = null;
            DestroyMaterial(_ringBackMaterial);
            DestroyMaterial(_ringProgressMaterial);
            DestroyMaterial(_ringAbortMaterial);
        }

        private void Update()
        {
            ResolveReferences();
            HandleLeftGrabPanelOpen();

            if (!CanUseHoldActions())
            {
                ResetHold();
                return;
            }

            bool saveHeld = IsLeftYHeld();
            bool abandonHeld = IsLeftXHeld();
            if (saveHeld != _lastSaveHeld || abandonHeld != _lastAbandonHeld)
            {
                Debug.Log($"[DeviceAnnotationController] left buttons: X={abandonHeld} Y={saveHeld}");
                _lastSaveHeld = saveHeld;
                _lastAbandonHeld = abandonHeld;
            }

            if (saveHeld && !abandonHeld)
                UpdateHold(HoldAction.Save);
            else if (abandonHeld && !saveHeld)
                UpdateHold(HoldAction.Abandon);
            else
                ResetHold();
        }

        private bool CanUseHoldActions()
        {
            return RoomCoordinateSystemPanel.HasEnteredRoom
                   && !RoomCoordinateSystemPanel.IsPanelVisible
                   && !DeviceArchivePanel.IsPanelVisible
                   && !_actionInFlight;
        }

        private static bool IsLeftXHeld()
        {
            return OVRInput.Get(OVRInput.RawButton.X)
                   || OVRInput.Get(OVRInput.Button.Three)
                   || OVRInput.Get(OVRInput.Button.Three, OVRInput.Controller.LTouch);
        }

        private static bool IsLeftYHeld()
        {
            return OVRInput.Get(OVRInput.RawButton.Y)
                   || OVRInput.Get(OVRInput.Button.Four)
                   || OVRInput.Get(OVRInput.Button.Four, OVRInput.Controller.LTouch);
        }

        private void ResolveReferences()
        {
            if (xrCamera == null)
                xrCamera = Camera.main;
            if (trackingManager == null)
                trackingManager = FindFirstObjectByType<TrackingManager>();
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

        private void HandleLeftGrabPanelOpen()
        {
            if (!RoomCoordinateSystemPanel.HasEnteredRoom || RoomCoordinateSystemPanel.IsPanelVisible)
            {
                ResetLeftGrab();
                return;
            }

            if (DeviceArchivePanel.IsPanelVisible)
            {
                ResetLeftGrab();
                return;
            }

            float grabValue = OVRInput.Get(OVRInput.RawAxis1D.LHandTrigger);
            bool pressed = grabValue >= grabPressThreshold;
            bool released = _leftGrabHeld && grabValue <= grabReleaseThreshold;
            if (released || !pressed)
            {
                ResetLeftGrab();
                return;
            }

            if (!_leftGrabHeld)
            {
                _leftGrabHeld = true;
                _leftGrabOpened = false;
                _leftGrabStartTime = Time.time;
                return;
            }

            if (!_leftGrabOpened && Time.time - _leftGrabStartTime >= leftGrabHoldSeconds)
            {
                _leftGrabOpened = true;
                DeviceArchivePanel.OpenPanel();
            }
        }

        private void ResetLeftGrab()
        {
            _leftGrabHeld = false;
            _leftGrabOpened = false;
            _leftGrabStartTime = 0f;
        }

        private void UpdateHold(HoldAction action)
        {
            if (_activeHold != action)
            {
                _activeHold = action;
                _holdStartTime = Time.time;
                _holdTriggered = false;
            }

            float elapsed = Time.time - _holdStartTime;
            if (elapsed >= ringDelaySeconds)
            {
                float progress = Mathf.InverseLerp(ringDelaySeconds, actionHoldSeconds, elapsed);
                ShowRing(action, Mathf.Clamp01(progress));
            }

            if (!_holdTriggered && elapsed >= actionHoldSeconds)
            {
                _holdTriggered = true;
                HideRing();
                if (action == HoldAction.Save)
                    _ = CompleteCurrentObjectAsync();
                else if (action == HoldAction.Abandon)
                    _ = AbandonCurrentObjectAndStartNewAsync();
            }
        }

        private void ResetHold()
        {
            _activeHold = HoldAction.None;
            _holdStartTime = 0f;
            _holdTriggered = false;
            HideRing();
        }

        private void StartNewObjectAfterAbandon()
        {
            if (_actionInFlight)
                return;
            _ = AbandonCurrentObjectAndStartNewAsync();
        }

        private async Task CompleteCurrentObjectAsync()
        {
            if (_actionInFlight)
                return;
            _actionInFlight = true;
            try
            {
                if (trackingManager == null)
                    return;

                string objectId = RoomObjectSession.CurrentObjectId;
                TrackingManager.ObjectActionResponse saved =
                    await trackingManager.CompleteObjectAsync(objectId, RoomObjectSession.CurrentEditSessionId);
                if (saved == null || !saved.ok)
                    return;

                TrackingManager.RoomObjectPointRecord[] boardPoints = saved.points;
                TrackingManager.RoomObjectSpatialRecord boardSpatial = saved.spatial;
                TrackingManager.ObjectActionResponse edit = await trackingManager.BeginEditObjectAsync(objectId);
                if (edit != null && edit.ok)
                {
                    RoomObjectSession.EnterSavedObject(objectId, edit.edit_session_id);
                    ShowPromptMarkers(edit.points);
                    boardPoints = edit.points;
                    if (edit.spatial != null && edit.spatial.valid)
                        boardSpatial = edit.spatial;
                }
                else
                {
                    RoomObjectSession.EnterSavedObject(objectId, string.Empty);
                    ShowPromptMarkers(saved.points);
                }

                DevicePlaceholderBoardManager.PlaceForObject(objectId, boardSpatial, boardPoints, xrCamera);
            }
            finally
            {
                _actionInFlight = false;
                ResetHold();
            }
        }

        private async Task AbandonCurrentObjectAndStartNewAsync()
        {
            if (_actionInFlight)
                return;
            _actionInFlight = true;
            try
            {
                string objectId = RoomObjectSession.HasCurrentObject ? RoomObjectSession.CurrentObjectId : string.Empty;
                if (!string.IsNullOrWhiteSpace(objectId) && trackingManager != null)
                {
                    TrackingManager.ObjectActionResponse abandoned =
                        await trackingManager.AbandonObjectAsync(objectId, RoomObjectSession.CurrentEditSessionId);
                    if (abandoned == null || !abandoned.ok)
                        return;
                }

                RoomCaptureSession.Reset();
                RoomObjectSession.StartNewObject();
            }
            finally
            {
                _actionInFlight = false;
                ResetHold();
            }
        }

        private void BuildRing()
        {
            if (_ringRoot != null)
                return;

            _ringRoot = new GameObject("AnnotationHoldRing");
            _ringRoot.transform.SetParent(transform, false);

            _ringBackMaterial = CreateMaterial(new Color(1f, 1f, 1f, 0.25f));
            _ringProgressMaterial = CreateMaterial(new Color(0.12f, 0.9f, 0.42f, 1f));
            _ringAbortMaterial = CreateMaterial(new Color(1f, 0.12f, 0.08f, 1f));

            _ringBack = CreateRingLine("BackRing", _ringBackMaterial, RingSegments, true);
            _ringProgress = CreateRingLine("ProgressRing", _ringProgressMaterial, RingSegments, false);

            GameObject labelObject = new GameObject("Label", typeof(TextMeshPro));
            labelObject.transform.SetParent(_ringRoot.transform, false);
            labelObject.transform.localPosition = new Vector3(0f, -ringRadius - 0.035f, 0f);
            _ringLabel = labelObject.GetComponent<TextMeshPro>();
            _ringLabel.fontSize = 0.055f;
            _ringLabel.alignment = TextAlignmentOptions.Center;
            _ringLabel.color = Color.white;
            _ringLabel.outlineColor = Color.black;
            _ringLabel.outlineWidth = 0.2f;
            RectTransform rect = labelObject.GetComponent<RectTransform>();
            if (rect != null)
                rect.sizeDelta = new Vector2(0.24f, 0.08f);
        }

        private LineRenderer CreateRingLine(string name, Material material, int segments, bool closed)
        {
            GameObject go = new GameObject(name, typeof(LineRenderer));
            go.transform.SetParent(_ringRoot.transform, false);
            LineRenderer line = go.GetComponent<LineRenderer>();
            line.useWorldSpace = false;
            line.loop = closed;
            line.positionCount = segments;
            line.startWidth = ringWidth;
            line.endWidth = ringWidth;
            line.material = material;
            return line;
        }

        private void ShowRing(HoldAction action, float progress)
        {
            if (_ringRoot == null)
                return;

            UpdateRingPose();
            _ringRoot.SetActive(true);
            _ringProgress.sharedMaterial = action == HoldAction.Abandon ? _ringAbortMaterial : _ringProgressMaterial;
            _ringLabel.text = action == HoldAction.Abandon ? "Abandon" : "Save";

            FillRing(_ringBack, 1f);
            FillRing(_ringProgress, progress);
        }

        private void HideRing()
        {
            if (_ringRoot != null)
                _ringRoot.SetActive(false);
        }

        private void UpdateRingPose()
        {
            if (_ringRoot == null || xrCamera == null)
                return;

            Vector3 targetPosition = leftHandAnchor != null
                ? leftHandAnchor.TransformPoint(ringLeftHandOffset)
                : xrCamera.transform.position + xrCamera.transform.forward * 0.55f + xrCamera.transform.up * -0.1f;
            _ringRoot.transform.position = targetPosition;
            _ringRoot.transform.rotation = Quaternion.LookRotation(targetPosition - xrCamera.transform.position, Vector3.up);
        }

        private void FillRing(LineRenderer line, float progress)
        {
            if (line == null)
                return;

            int count = Mathf.Max(2, Mathf.RoundToInt(RingSegments * Mathf.Clamp01(progress)));
            line.positionCount = count;
            for (int i = 0; i < count; i++)
            {
                float t = count <= 1 ? 0f : (float)i / (RingSegments - 1);
                float angle = 2f * Mathf.PI * t + Mathf.PI * 0.5f;
                line.SetPosition(i, new Vector3(Mathf.Cos(angle) * ringRadius, Mathf.Sin(angle) * ringRadius, 0f));
            }
        }

        private static Material CreateMaterial(Color color)
        {
            Shader shader = Shader.Find("Universal Render Pipeline/Unlit");
            if (shader == null)
                shader = Shader.Find("Unlit/Color");
            Material material = new Material(shader);
            material.SetColor(BaseColorId, color);
            material.SetColor(ColorId, color);
            material.color = color;
            return material;
        }

        private static void DestroyMaterial(Material material)
        {
            if (material != null)
                Destroy(material);
        }
    }
}
