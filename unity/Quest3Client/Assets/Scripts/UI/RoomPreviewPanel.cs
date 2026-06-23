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
        [SerializeField] private bool showCaptionText = false;
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
        private Texture2D _texture;

        private PreviewImageRecord[] _images = Array.Empty<PreviewImageRecord>();
        private string _activeImageId = string.Empty;
        private string _activeSignature = string.Empty;
        private bool _requestInFlight;
        private bool _hasPose;
        private float _nextRefreshAt;
        private float _nextStickAt;
        private float _displayScale = 1f;

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
                           && !DeviceArchivePanel.IsPanelVisible;
            SetVisible(visible);
            if (!visible)
                return;

            UpdatePose();
            HandleStick();

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

            if (showCaptionText)
            {
                _titleText = CreateLabel("Title", "Preview", new Vector3(-0.48f, 0.355f, -0.002f), 0.09f, TextAlignmentOptions.Left);
                _statusText = CreateLabel("Status", "Waiting", new Vector3(-0.48f, -0.365f, -0.002f), 0.08f, TextAlignmentOptions.Left);
            }
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
                _displayScale = Mathf.Min(maxScale, _displayScale + scaleStep);
                ApplyPanelScale();
                _nextStickAt = Time.time + stickRepeatSeconds;
            }
            else if (stick.y <= -stickThreshold)
            {
                _displayScale = Mathf.Max(minScale, _displayScale - scaleStep);
                ApplyPanelScale();
                _nextStickAt = Time.time + stickRepeatSeconds;
            }
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
                return;

            int current = Array.FindIndex(_images, image => image.image_id == _activeImageId);
            if (current < 0)
                current = Mathf.Clamp(_images.Length - 1, 0, _images.Length - 1);
            int next = (current + delta + _images.Length) % _images.Length;
            SetActiveImage(_images[next], force: true);
        }

        private IEnumerator RefreshListAsync()
        {
            if (trackingManager == null)
                yield break;

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
                SetStatus("No preview images");
                SetTitle("Room preview");
                yield break;
            }

            PreviewImageRecord selected = FindCurrentOrLatestImage();
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
            StartCoroutine(LoadImageAsync(image));
        }

        private IEnumerator LoadImageAsync(PreviewImageRecord image)
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
                    SetStatus("Preview image error: " + request.error);
                    yield break;
                }

                Texture2D texture = DownloadHandlerTexture.GetContent(request);
                ApplyTexture(texture);
                SetStatus($"+{image.positive_point_count}/-{image.negative_point_count}  {(image.segmented ? "mask" : "no mask")}");
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
        }

        private void SetTitle(string text)
        {
            if (_titleText != null)
                _titleText.text = text;
        }

        private void SetStatus(string text)
        {
            if (_statusText != null)
                _statusText.text = ShortText(text, 42);
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
