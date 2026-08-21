using System;
using System.Collections;
using SmartRoom.Tracking;
using TMPro;
using UnityEngine;
using UnityEngine.Networking;

namespace SmartRoom.UI
{
    public sealed class RoomPreviewPanel : MonoBehaviour
    {
        private const string DefaultObjectName = "RoomPreviewPanel";
        private const float AspectRatio = 16f / 9f;

        [Header("References")]
        [SerializeField] private Camera xrCamera;
        [SerializeField] private TrackingManager trackingManager;
        [SerializeField] private Transform leftHandAnchor;

        [Header("Placement")]
        [SerializeField] private Vector3 leftHandLocalOffset = new Vector3(0.02f, 0.17f, 0.28f);
        [SerializeField] private Vector3 headFallbackOffset = new Vector3(-0.32f, -0.04f, 0.72f);
        [SerializeField] private float positionLerp = 12f;
        [SerializeField] private float rotationLerp = 12f;

        [Header("Display")]
        [SerializeField] private float panelWidthMeters = 0.36f;
        [SerializeField] private float minScale = 0.65f;
        [SerializeField] private float maxScale = 1.8f;
        [SerializeField] private float scaleStep = 0.12f;
        [SerializeField] private float refreshIntervalSeconds = 1.25f;
        [SerializeField] private float stickThreshold = 0.72f;
        [SerializeField] private float stickRepeatSeconds = 0.25f;

        private GameObject _visualRoot;
        private Transform _backplateTransform;
        private Transform _screenTransform;
        private MeshRenderer _screenRenderer;
        private Material _screenMaterial;
        private TextMeshPro _titleText;
        private TextMeshPro _statusText;
        private TextMeshPro _feedbackText;
        private Texture2D _texture;

        private PreviewImageRecord[] _images = Array.Empty<PreviewImageRecord>();
        private string _activeImageId = string.Empty;
        private string _activeSignature = string.Empty;
        private bool _requestInFlight;
        private bool _hasPose;
        private float _nextRefreshAt;
        private float _nextStickAt;
        private float _displayScale = 1f;
        private float _feedbackUntil = -1f;
        private int _imageLoadVersion;
        private Coroutine _hapticRoutine;

        private static RoomPreviewPanel _instance;
        private static readonly int BaseMapId = Shader.PropertyToID("_BaseMap");
        private static readonly int MainTexId = Shader.PropertyToID("_MainTex");
        private static readonly int BaseColorId = Shader.PropertyToID("_BaseColor");
        private static readonly int ColorId = Shader.PropertyToID("_Color");

        public static RoomPreviewPanel EnsureExists(Camera camera = null)
        {
            if (_instance != null)
            {
                _instance.SetCamera(camera);
                return _instance;
            }

            RoomPreviewPanel existing = FindFirstObjectByType<RoomPreviewPanel>();
            if (existing != null)
            {
                _instance = existing;
                existing.SetCamera(camera);
                return existing;
            }

            GameObject go = GameObject.Find(DefaultObjectName);
            if (go == null)
                go = new GameObject(DefaultObjectName);

            RoomPreviewPanel panel = go.GetComponent<RoomPreviewPanel>();
            if (panel == null)
                panel = go.AddComponent<RoomPreviewPanel>();

            panel.SetCamera(camera);
            return panel;
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
            BuildVisuals();
            SetVisible(false);
        }

        private void OnDestroy()
        {
            if (_instance == this)
                _instance = null;
            OVRInput.SetControllerVibration(0f, 0f, OVRInput.Controller.LTouch);
            if (_screenMaterial != null)
                Destroy(_screenMaterial);
            if (_texture != null)
                Destroy(_texture);
        }

        private void Update()
        {
            ResolveReferences();

            bool visible = RoomCoordinateSystemPanel.HasEnteredRoom
                           && !RoomCoordinateSystemPanel.IsPanelVisible
                           && !DeviceArchivePanel.IsPanelVisible
                           && !DeviceBindingPanel.IsPanelVisible;
            SetVisible(visible);
            if (!visible)
                return;

            UpdatePose();
            HandleStick();
            UpdateTransientFeedback();

            if (!_requestInFlight && Time.time >= _nextRefreshAt)
            {
                _nextRefreshAt = Time.time + Mathf.Max(0.25f, refreshIntervalSeconds);
                StartCoroutine(RefreshListAsync());
            }
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

        private void BuildVisuals()
        {
            if (_visualRoot != null)
                return;

            gameObject.name = DefaultObjectName;
            _visualRoot = new GameObject("VisualRoot");
            _visualRoot.transform.SetParent(transform, false);

            GameObject backplate = GameObject.CreatePrimitive(PrimitiveType.Cube);
            backplate.name = "Backplate";
            backplate.transform.SetParent(_visualRoot.transform, false);
            backplate.transform.localPosition = new Vector3(0f, 0f, 0.012f);
            backplate.transform.localScale = new Vector3(1.06f, 0.76f, 0.012f);
            DestroyCollider(backplate);
            backplate.GetComponent<MeshRenderer>().sharedMaterial = CreateFlatMaterial(new Color(0.015f, 0.017f, 0.02f, 1f));
            _backplateTransform = backplate.transform;

            GameObject screen = GameObject.CreatePrimitive(PrimitiveType.Quad);
            screen.name = "PreviewScreen";
            screen.transform.SetParent(_visualRoot.transform, false);
            screen.transform.localPosition = new Vector3(0f, 0.005f, 0f);
            screen.transform.localScale = new Vector3(0.96f, 0.96f / AspectRatio, 1f);
            DestroyCollider(screen);
            _screenTransform = screen.transform;
            _screenRenderer = screen.GetComponent<MeshRenderer>();
            _screenMaterial = CreateTextureMaterial();
            _screenRenderer.sharedMaterial = _screenMaterial;

            _titleText = CreateLabel(
                "Title",
                "0/0  Waiting for images",
                new Vector3(-0.48f, 0.355f, -0.002f),
                0.12f,
                TextAlignmentOptions.Left);
            _statusText = CreateLabel(
                "Status",
                "No preview images yet",
                new Vector3(-0.48f, -0.365f, -0.002f),
                0.10f,
                TextAlignmentOptions.Left);
            _feedbackText = CreateLabel(
                "InteractionFeedback",
                string.Empty,
                new Vector3(0f, 0f, -0.004f),
                0.16f,
                TextAlignmentOptions.Center);
            RectTransform feedbackRect = _feedbackText.GetComponent<RectTransform>();
            if (feedbackRect != null)
                feedbackRect.sizeDelta = new Vector2(0.82f, 0.18f);
            _feedbackText.fontStyle = FontStyles.Bold;
            _feedbackText.gameObject.SetActive(false);
            UpdateAspectLayout(AspectRatio);

            ApplyPanelScale();
        }

        private static void DestroyCollider(GameObject go)
        {
            Collider col = go.GetComponent<Collider>();
            if (col != null)
                Destroy(col);
        }

        private TextMeshPro CreateLabel(string name, string text, Vector3 localPosition, float fontSize, TextAlignmentOptions alignment)
        {
            GameObject labelObject = new GameObject(name, typeof(TextMeshPro));
            labelObject.transform.SetParent(_visualRoot.transform, false);
            labelObject.transform.localPosition = localPosition;
            labelObject.transform.localScale = Vector3.one;

            TextMeshPro textMesh = labelObject.GetComponent<TextMeshPro>();
            textMesh.text = text;
            textMesh.fontSize = fontSize;
            textMesh.color = Color.white;
            textMesh.alignment = alignment;
            textMesh.textWrappingMode = TextWrappingModes.NoWrap;
            textMesh.overflowMode = TextOverflowModes.Ellipsis;
            textMesh.outlineColor = Color.black;
            textMesh.outlineWidth = 0.18f;

            RectTransform rect = labelObject.GetComponent<RectTransform>();
            if (rect != null)
                rect.sizeDelta = new Vector2(0.96f, 0.09f);
            return textMesh;
        }

        private Material CreateTextureMaterial()
        {
            Shader shader = Shader.Find("Universal Render Pipeline/Unlit");
            if (shader == null)
                shader = Shader.Find("Unlit/Texture");
            if (shader == null)
                shader = Shader.Find("Unlit/Color");
            Material material = new Material(shader);
            material.SetColor(BaseColorId, Color.white);
            material.SetColor(ColorId, Color.white);
            material.SetFloat("_Cull", 0f);
            material.color = Color.white;
            return material;
        }

        private static Material CreateFlatMaterial(Color color)
        {
            Shader shader = Shader.Find("Universal Render Pipeline/Unlit");
            if (shader == null)
                shader = Shader.Find("Unlit/Color");
            Material material = new Material(shader);
            material.SetColor(BaseColorId, color);
            material.SetColor(ColorId, color);
            material.SetFloat("_Cull", 0f);
            material.color = color;
            return material;
        }

        private void SetVisible(bool visible)
        {
            if (_visualRoot != null && _visualRoot.activeSelf != visible)
                _visualRoot.SetActive(visible);
        }

        private void UpdatePose()
        {
            if (xrCamera == null)
                return;

            Vector3 targetPosition;
            if (leftHandAnchor != null)
                targetPosition = leftHandAnchor.TransformPoint(leftHandLocalOffset);
            else
                targetPosition = xrCamera.transform.TransformPoint(headFallbackOffset);

            Vector3 toHead = xrCamera.transform.position - targetPosition;
            Quaternion targetRotation = toHead.sqrMagnitude > 0.0001f
                ? Quaternion.LookRotation(-toHead.normalized, Vector3.up)
                : xrCamera.transform.rotation;

            if (!_hasPose)
            {
                transform.SetPositionAndRotation(targetPosition, targetRotation);
                _hasPose = true;
                return;
            }

            float posT = 1f - Mathf.Exp(-positionLerp * Time.deltaTime);
            float rotT = 1f - Mathf.Exp(-rotationLerp * Time.deltaTime);
            transform.position = Vector3.Lerp(transform.position, targetPosition, posT);
            transform.rotation = Quaternion.Slerp(transform.rotation, targetRotation, rotT);
        }

        private void HandleStick()
        {
            Vector2 stick = OVRInput.Get(OVRInput.Axis2D.PrimaryThumbstick, OVRInput.Controller.LTouch);
            if (Time.time < _nextStickAt)
                return;

            if (stick.x >= stickThreshold)
            {
                SelectRelativeImage(1);
                _nextStickAt = Time.time + stickRepeatSeconds;
            }
            else if (stick.x <= -stickThreshold)
            {
                SelectRelativeImage(-1);
                _nextStickAt = Time.time + stickRepeatSeconds;
            }
            else if (stick.y >= stickThreshold)
            {
                SetDisplayScale(_displayScale + scaleStep);
                _nextStickAt = Time.time + stickRepeatSeconds;
            }
            else if (stick.y <= -stickThreshold)
            {
                SetDisplayScale(_displayScale - scaleStep);
                _nextStickAt = Time.time + stickRepeatSeconds;
            }
        }

        private void SetDisplayScale(float requestedScale)
        {
            float next = Mathf.Clamp(requestedScale, minScale, maxScale);
            if (Mathf.Approximately(next, _displayScale))
            {
                string boundary = next >= maxScale ? "Maximum zoom" : "Minimum zoom";
                ShowFeedback($"{boundary}  {Mathf.RoundToInt(next * 100f)}%", new Color(1f, 0.78f, 0.24f), 0.12f);
                return;
            }

            _displayScale = next;
            ApplyPanelScale();
            ShowFeedback($"Zoom  {Mathf.RoundToInt(_displayScale * 100f)}%", Color.white, 0.2f);
        }

        private void ApplyPanelScale()
        {
            if (_visualRoot == null)
                return;
            float width = panelWidthMeters * _displayScale;
            _visualRoot.transform.localScale = Vector3.one * width;
        }

        private void SelectRelativeImage(int delta)
        {
            if (_images == null || _images.Length == 0)
            {
                SetTitle("0/0  No images");
                SetStatus("Place a point to create the first image");
                ShowFeedback("No images yet", new Color(1f, 0.78f, 0.24f), 0.12f);
                return;
            }

            int current = Array.FindIndex(_images, image => image.image_id == _activeImageId);
            if (current < 0)
                current = Mathf.Clamp(_images.Length - 1, 0, _images.Length - 1);

            if (_images.Length == 1)
            {
                SetTitle($"1/1  {_images[0].image_id}");
                ShowFeedback("Only one image", new Color(1f, 0.78f, 0.24f), 0.12f);
                return;
            }

            int next = current + delta;
            if (next < 0 || next >= _images.Length)
            {
                string boundary = next < 0 ? "Oldest image" : "Newest image";
                ShowFeedback(
                    $"{boundary}  {current + 1}/{_images.Length}",
                    new Color(1f, 0.78f, 0.24f),
                    0.12f);
                return;
            }

            SetTitle($"{next + 1}/{_images.Length}  {_images[next].image_id}");
            SetStatus("Loading selected image...");
            ShowFeedback($"Image  {next + 1} / {_images.Length}", Color.white, 0.28f);
            SetActiveImage(_images[next], force: true);
        }

        private IEnumerator RefreshListAsync()
        {
            if (trackingManager == null)
                yield break;

            int previousCount = _images != null ? _images.Length : 0;
            _requestInFlight = true;
            string url = trackingManager.BuildViewerUrl(
                "/api/room/preview/list?room_id=" + UnityWebRequest.EscapeURL(RoomCoordinateSystemPanel.CurrentRoomId) +
                "&room_name=" + UnityWebRequest.EscapeURL(RoomCoordinateSystemPanel.CurrentRoomName) +
                "&device_id=" + UnityWebRequest.EscapeURL(SystemInfo.deviceUniqueIdentifier) +
                "&device_name=" + UnityWebRequest.EscapeURL(SystemInfo.deviceName) +
                "&device_model=" + UnityWebRequest.EscapeURL(SystemInfo.deviceModel) +
                "&object_id=" + UnityWebRequest.EscapeURL(RoomObjectSession.CurrentObjectId));

            using (UnityWebRequest request = UnityWebRequest.Get(url))
            {
                yield return request.SendWebRequest();
                if (request.result != UnityWebRequest.Result.Success)
                {
                    SetStatus("Preview list error: " + request.error);
                    ShowFeedback("Image list unavailable", new Color(1f, 0.3f, 0.25f), 0.4f);
                    _requestInFlight = false;
                    yield break;
                }

                PreviewListResponse response = null;
                try
                {
                    response = JsonUtility.FromJson<PreviewListResponse>(request.downloadHandler.text);
                }
                catch (Exception ex)
                {
                    SetStatus("Preview JSON error: " + ex.Message);
                    ShowFeedback("Invalid image list", new Color(1f, 0.3f, 0.25f), 0.4f);
                    _requestInFlight = false;
                    yield break;
                }

                _images = response != null && response.images != null ? response.images : Array.Empty<PreviewImageRecord>();
            }

            _requestInFlight = false;

            if (_images.Length == 0)
            {
                _activeImageId = string.Empty;
                _activeSignature = string.Empty;
                SetStatus("Place a point to create the first image");
                SetTitle("0/0  No images");
                yield break;
            }

            bool hasNewImage = _images.Length > previousCount;
            if (hasNewImage)
            {
                string message = previousCount == 0
                    ? "First image ready"
                    : $"New image ready  {_images.Length}/{_images.Length}";
                ShowFeedback(message, new Color(0.3f, 1f, 0.55f), 0.35f);
            }

            PreviewImageRecord selected = hasNewImage
                ? _images[_images.Length - 1]
                : FindCurrentOrLatestImage();
            SetActiveImage(selected, force: false);
        }

        private PreviewImageRecord FindCurrentOrLatestImage()
        {
            if (!string.IsNullOrWhiteSpace(_activeImageId))
            {
                for (int i = 0; i < _images.Length; i++)
                {
                    if (_images[i].image_id == _activeImageId)
                        return _images[i];
                }
            }
            return _images[Mathf.Clamp(_images.Length - 1, 0, _images.Length - 1)];
        }

        private void SetActiveImage(PreviewImageRecord image, bool force)
        {
            if (image == null || string.IsNullOrWhiteSpace(image.image_id) || trackingManager == null)
                return;

            string signature = image.image_id + ":" + image.updated_at_ms + ":" + image.last_segmented_at_ms + ":" + image.point_count;
            if (!force && _activeImageId == image.image_id && _activeSignature == signature)
                return;

            _activeImageId = image.image_id;
            _activeSignature = signature;
            int loadVersion = ++_imageLoadVersion;
            StartCoroutine(LoadImageAsync(image, loadVersion));
        }

        private IEnumerator LoadImageAsync(PreviewImageRecord image, int loadVersion)
        {
            if (trackingManager == null)
                yield break;

            int index = Array.FindIndex(_images, candidate => candidate.image_id == image.image_id);
            SetTitle($"{Mathf.Max(index + 1, 1)}/{Mathf.Max(_images.Length, 1)}  {image.image_id}");
            SetStatus($"Loading +{image.positive_point_count}/-{image.negative_point_count}");

            string url = trackingManager.BuildViewerUrl(
                "/api/room/preview/image?room_id=" + UnityWebRequest.EscapeURL(RoomCoordinateSystemPanel.CurrentRoomId) +
                "&device_id=" + UnityWebRequest.EscapeURL(SystemInfo.deviceUniqueIdentifier) +
                "&object_id=" + UnityWebRequest.EscapeURL(RoomObjectSession.CurrentObjectId) +
                "&image_id=" + UnityWebRequest.EscapeURL(image.image_id) +
                "&t=" + image.updated_at_ms);

            using (UnityWebRequest request = UnityWebRequestTexture.GetTexture(url, nonReadable: false))
            {
                yield return request.SendWebRequest();
                if (request.result != UnityWebRequest.Result.Success)
                {
                    if (loadVersion == _imageLoadVersion)
                    {
                        SetStatus("Preview image error: " + request.error);
                        ShowFeedback("Image load failed", new Color(1f, 0.3f, 0.25f), 0.5f);
                    }
                    yield break;
                }

                if (loadVersion != _imageLoadVersion || _activeImageId != image.image_id)
                    yield break;

                Texture2D texture = DownloadHandlerTexture.GetContent(request);
                ApplyTexture(texture);
                SetStatus($"+{image.positive_point_count}/-{image.negative_point_count}  {(image.segmented ? "mask" : "no mask")}");
                ShowFeedback(
                    $"Image  {Mathf.Max(index + 1, 1)} / {Mathf.Max(_images.Length, 1)}  ready",
                    new Color(0.3f, 1f, 0.55f),
                    0.18f);
            }
        }

        private void ApplyTexture(Texture2D texture)
        {
            if (texture == null || _screenMaterial == null)
                return;

            if (_texture != null)
                Destroy(_texture);
            _texture = texture;
            _screenMaterial.SetTexture(BaseMapId, _texture);
            _screenMaterial.SetTexture(MainTexId, _texture);
            if (_texture.width > 0 && _texture.height > 0)
                UpdateAspectLayout((float)_texture.width / _texture.height);
        }

        private void UpdateAspectLayout(float aspect)
        {
            if (_screenTransform == null)
                return;

            float safeAspect = Mathf.Clamp(aspect > 0f ? aspect : AspectRatio, 0.75f, 2.4f);
            float screenWidth = 0.96f;
            float screenHeight = screenWidth / safeAspect;
            _screenTransform.localScale = new Vector3(screenWidth, screenHeight, 1f);

            if (_backplateTransform != null)
                _backplateTransform.localScale = new Vector3(1.06f, screenHeight + 0.20f, 0.012f);

            float titleY = screenHeight * 0.5f + 0.075f;
            float statusY = -screenHeight * 0.5f - 0.075f;
            if (_titleText != null)
                _titleText.transform.localPosition = new Vector3(-0.48f, titleY, -0.002f);
            if (_statusText != null)
                _statusText.transform.localPosition = new Vector3(-0.48f, statusY, -0.002f);
            if (_feedbackText != null)
                _feedbackText.transform.localPosition = new Vector3(0f, 0f, -0.004f);
        }

        private void SetTitle(string text)
        {
            if (_titleText != null)
                _titleText.text = text;
        }

        private void SetStatus(string text)
        {
            if (_statusText != null)
                _statusText.text = ShortText(text, 54);
        }

        private void ShowFeedback(string text, Color color, float hapticAmplitude)
        {
            if (_feedbackText != null)
            {
                _feedbackText.text = ShortText(text, 38);
                _feedbackText.color = color;
                _feedbackText.gameObject.SetActive(true);
                _feedbackUntil = Time.unscaledTime + 0.9f;
            }

            if (hapticAmplitude > 0f)
            {
                if (_hapticRoutine != null)
                    StopCoroutine(_hapticRoutine);
                _hapticRoutine = StartCoroutine(PulseLeftController(hapticAmplitude));
            }
        }

        private void UpdateTransientFeedback()
        {
            if (_feedbackText != null &&
                _feedbackText.gameObject.activeSelf &&
                Time.unscaledTime >= _feedbackUntil)
            {
                _feedbackText.gameObject.SetActive(false);
            }
        }

        private IEnumerator PulseLeftController(float amplitude)
        {
            OVRInput.SetControllerVibration(0.12f, Mathf.Clamp01(amplitude), OVRInput.Controller.LTouch);
            yield return new WaitForSecondsRealtime(0.045f);
            OVRInput.SetControllerVibration(0f, 0f, OVRInput.Controller.LTouch);
            _hapticRoutine = null;
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

        [Serializable]
        private sealed class PreviewListResponse
        {
            public bool ok = false;
            public string room_id = string.Empty;
            public string device_id = string.Empty;
            public string object_id = string.Empty;
            public int selected_index = -1;
            public PreviewImageRecord[] images = Array.Empty<PreviewImageRecord>();
        }

        [Serializable]
        private sealed class PreviewImageRecord
        {
            public string image_id = string.Empty;
            public long created_at_ms = 0;
            public long updated_at_ms = 0;
            public long last_segmented_at_ms = 0;
            public int point_count = 0;
            public int positive_point_count = 0;
            public int negative_point_count = 0;
            public int preseeded_point_count = 0;
            public bool segmented = false;
        }
    }
}
