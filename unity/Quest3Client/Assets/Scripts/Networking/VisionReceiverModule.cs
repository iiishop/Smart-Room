using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Reflection;
using System.Text;
using Meta.XR;
using TMPro;
using UnityEngine;
using UnityEngine.UI;

namespace SmartRoom.Networking
{
    public readonly struct VisionWorldObject
    {
        public VisionWorldObject(int objectId, string label, float score, Vector3 worldPosition, int hitCount)
        {
            ObjectId = objectId;
            Label = label ?? string.Empty;
            Score = score;
            WorldPosition = worldPosition;
            HitCount = hitCount;
        }

        public int ObjectId { get; }
        public string Label { get; }
        public float Score { get; }
        public Vector3 WorldPosition { get; }
        public int HitCount { get; }
    }

    public class VisionReceiverModule : MonoBehaviour
    {
        public event Action<WorldPosition[]> ObjectsProcessed;

        [SerializeField] private BackendCommunicationManager manager;
        [SerializeField] private DepthStreamModule depthStreamModule;
        [SerializeField] private Camera rayCamera;
        [SerializeField] private PassthroughCameraAccess passthroughCameraAccess;
        [SerializeField] private int samplesPerObject = 3;
        [SerializeField] private int maxObjectsPerFrame = 8;
        [Header("World Labels")]
        [SerializeField] private Canvas worldSpaceLabelCanvas;
        [SerializeField] private TMP_FontAsset labelFont;
        [SerializeField] private int pooledLabelCount = 16;
        [SerializeField] private Vector3 labelWorldOffset = new Vector3(0f, 0.08f, 0f);
        [SerializeField] private Vector2 labelSize = new Vector2(320f, 72f);
        [SerializeField] private float labelFontSize = 36f;
        [SerializeField] private float canvasScale = 0.001f;
        [SerializeField] private Color labelTextColor = Color.white;
        [SerializeField] private Color labelOutlineColor = new Color(0f, 0f, 0f, 0.85f);
        [SerializeField] [Range(0f, 1f)] private float labelOutlineWidth = 0.2f;

        private MethodInfo _passthroughViewportRayMethod;
        private bool _passthroughViewportRayMethodResolved;
        private bool _loggedRaySource;
        private bool _loggedPassthroughFallback;
        private readonly ConcurrentQueue<string> _pendingVisionMessages = new ConcurrentQueue<string>();
        private readonly List<VisionWorldObject> _worldObjectsBuffer = new List<VisionWorldObject>(16);
        private VisionWorldObject[] _latestWorldObjects = Array.Empty<VisionWorldObject>();
        private PooledLabel[] _labelPool = Array.Empty<PooledLabel>();
        private Camera _labelCamera;

        public event Action<VisionWorldObject[]> WorldObjectsUpdated;
        public VisionWorldObject[] LatestWorldObjects => _latestWorldObjects;

        private void Awake()
        {
            if (manager == null)
            {
                manager = FindFirstObjectByType<BackendCommunicationManager>();
            }

            if (depthStreamModule == null)
            {
                depthStreamModule = FindFirstObjectByType<DepthStreamModule>();
            }

            if (rayCamera == null)
            {
                rayCamera = Camera.main;
            }

            if (passthroughCameraAccess == null)
            {
                passthroughCameraAccess = FindFirstObjectByType<PassthroughCameraAccess>();
            }

            samplesPerObject = Mathf.Clamp(samplesPerObject, 1, 16);
            maxObjectsPerFrame = Mathf.Clamp(maxObjectsPerFrame, 1, 64);
            pooledLabelCount = Mathf.Clamp(pooledLabelCount, 1, 64);
            EnsureLabelPool();
        }

        private void OnEnable()
        {
            if (manager != null)
            {
                manager.VisionMessageReceived += OnVisionMessage;
            }
        }

        private void OnDisable()
        {
            if (manager != null)
            {
                manager.VisionMessageReceived -= OnVisionMessage;
            }

            while (_pendingVisionMessages.TryDequeue(out _))
            {
            }

            PublishWorldObjects(Array.Empty<VisionWorldObject>());
        }

        private void Update()
        {
            ProcessLatestVisionMessage();
            BillboardActiveLabels();
        }

        private void OnVisionMessage(string json)
        {
            if (string.IsNullOrWhiteSpace(json))
            {
                return;
            }

            _pendingVisionMessages.Enqueue(json);
        }

        private void ProcessLatestVisionMessage()
        {
            if (manager == null || depthStreamModule == null)
            {
                if (_latestWorldObjects.Length > 0)
                {
                    PublishWorldObjects(Array.Empty<VisionWorldObject>());
                }

                return;
            }

            string latestJson = null;
            while (_pendingVisionMessages.TryDequeue(out string json))
            {
                latestJson = json;
            }

            if (latestJson == null)
            {
                return;
            }

            VisionFramePayload frame;
            try
            {
                frame = JsonUtility.FromJson<VisionFramePayload>(latestJson);
            }
            catch (Exception ex)
            {
                manager.QueueUnityLog("WARNING", $"Vision payload parse failed: {ex.Message}");
                return;
            }

            if (frame?.objects == null || frame.objects.Length == 0)
            {
                PublishWorldObjects(Array.Empty<VisionWorldObject>());
                return;
            }

            _worldObjectsBuffer.Clear();
            int objectCount = Mathf.Min(frame.objects.Length, Mathf.Min(maxObjectsPerFrame, pooledLabelCount));
            var processedObjects = new WorldPosition[objectCount * samplesPerObject];
            int processedCount = 0;
            for (int objectIndex = 0; objectIndex < objectCount; objectIndex++)
            {
                VisionTrackedMaskPayload trackedMask = frame.objects[objectIndex];
                if (trackedMask?.mask_rle == null)
                {
                    continue;
                }

                VisionMaskSamplePoint[] samples = VisionMaskSampling.SampleMaskPixels(frame, trackedMask, samplesPerObject);
                if (samples.Length == 0)
                {
                    manager.QueueUnityLog("WARNING", $"Vision object_id={trackedMask.object_id} has no foreground pixels to sample.");
                    continue;
                }

                var successful = new StringBuilder();
                int hitCount = 0;
                Vector3 accumulatedWorldPoint = Vector3.zero;
                for (int sampleIndex = 0; sampleIndex < samples.Length; sampleIndex++)
                {
                    VisionMaskSamplePoint sample = samples[sampleIndex];
                    if (!TryGetViewportRay(sample.ViewportU, sample.ViewportV, out Ray ray, out Transform rayTransform))
                    {
                        continue;
                    }

                    if (!depthStreamModule.TryRaycastViewport(sample.ViewportU, sample.ViewportV, ray, rayTransform, out float depthM, out Vector3 worldPoint, out _))
                    {
                        continue;
                    }

                    if (successful.Length > 0)
                    {
                        successful.Append("; ");
                    }

                    successful.AppendFormat(
                        "pixel=({0},{1}) viewport=({2:F3},{3:F3}) world=({4:F3},{5:F3},{6:F3}) depth={7:F3}",
                        sample.PixelX,
                        sample.PixelY,
                        sample.ViewportU,
                        sample.ViewportV,
                        worldPoint.x,
                        worldPoint.y,
                        worldPoint.z,
                        depthM
                    );
                    accumulatedWorldPoint += worldPoint;
                    processedObjects[processedCount++] = VisionWorldPositionFactory.Create(
                        trackedMask.object_id,
                        trackedMask.label,
                        trackedMask.score,
                        worldPoint.x,
                        worldPoint.y,
                        worldPoint.z,
                        depthM);
                    hitCount++;
                }

                if (hitCount > 0)
                {
                    string label = string.IsNullOrWhiteSpace(trackedMask.label) ? "unknown" : trackedMask.label;
                    Vector3 averagedWorldPoint = accumulatedWorldPoint / hitCount;
                    _worldObjectsBuffer.Add(
                        new VisionWorldObject(
                            trackedMask.object_id,
                            label,
                            trackedMask.score,
                            averagedWorldPoint,
                            hitCount));
                    manager.QueueUnityLog(
                        "INFO",
                        $"Vision object_id={trackedMask.object_id} label={label} hits={hitCount}/{samples.Length} samples=[{successful}]"
                    );
                }
                else
                {
                    manager.QueueUnityLog(
                        "WARNING",
                        $"Vision object_id={trackedMask.object_id} produced no 3D hits from {samples.Length} sampled mask pixels."
                    );
                }
            }

            var publishedObjects = new WorldPosition[processedCount];
            Array.Copy(processedObjects, publishedObjects, processedCount);
            ObjectsProcessed?.Invoke(publishedObjects);

            PublishWorldObjects(_worldObjectsBuffer.ToArray());
        }

        private bool TryGetViewportRay(float u, float v, out Ray ray, out Transform rayTransform)
        {
            if (TryGetPassthroughViewportRay(u, v, out ray))
            {
                rayTransform = passthroughCameraAccess != null ? passthroughCameraAccess.transform : null;
                LogRaySourceOnce("PassthroughCameraAccess.ViewportPointToRay");
                return true;
            }

            if (rayCamera != null)
            {
                ray = rayCamera.ViewportPointToRay(new Vector3(u, v, 0f));
                rayTransform = rayCamera.transform;
                if (!_loggedPassthroughFallback && passthroughCameraAccess != null)
                {
                    _loggedPassthroughFallback = true;
                    manager?.QueueUnityLog("WARNING", "PassthroughCameraAccess.ViewportPointToRay unavailable; falling back to Camera.ViewportPointToRay for vision receiver.");
                }

                LogRaySourceOnce($"Camera.ViewportPointToRay({rayCamera.name})");
                return true;
            }

            ray = default;
            rayTransform = null;
            return false;
        }

        private bool TryGetPassthroughViewportRay(float u, float v, out Ray ray)
        {
            ray = default;

            if (passthroughCameraAccess == null || !passthroughCameraAccess.enabled || !passthroughCameraAccess.IsPlaying)
            {
                return false;
            }

            MethodInfo method = ResolvePassthroughViewportRayMethod();
            if (method == null)
            {
                return false;
            }

            object arg = method.GetParameters()[0].ParameterType == typeof(Vector2)
                ? new Vector2(u, v)
                : new Vector3(u, v, 0f);

            object target = method.IsStatic ? null : passthroughCameraAccess;
            try
            {
                object result = method.Invoke(target, new[] { arg });
                if (result is Ray castRay)
                {
                    ray = castRay;
                    return true;
                }
            }
            catch (TargetInvocationException ex)
            {
                if (!_loggedPassthroughFallback)
                {
                    _loggedPassthroughFallback = true;
                    manager?.QueueUnityLog("WARNING", $"PassthroughCameraAccess.ViewportPointToRay failed: {ex.InnerException?.Message ?? ex.Message}");
                }
            }
            catch (Exception ex)
            {
                if (!_loggedPassthroughFallback)
                {
                    _loggedPassthroughFallback = true;
                    manager?.QueueUnityLog("WARNING", $"PassthroughCameraAccess.ViewportPointToRay failed: {ex.Message}");
                }
            }

            return false;
        }

        private MethodInfo ResolvePassthroughViewportRayMethod()
        {
            if (_passthroughViewportRayMethodResolved)
            {
                return _passthroughViewportRayMethod;
            }

            _passthroughViewportRayMethodResolved = true;
            foreach (MethodInfo method in typeof(PassthroughCameraAccess).GetMethods(BindingFlags.Public | BindingFlags.Instance | BindingFlags.Static))
            {
                if (method.Name != "ViewportPointToRay" || method.ReturnType != typeof(Ray))
                {
                    continue;
                }

                ParameterInfo[] parameters = method.GetParameters();
                if (parameters.Length != 1)
                {
                    continue;
                }

                Type parameterType = parameters[0].ParameterType;
                if (parameterType == typeof(Vector2) || parameterType == typeof(Vector3))
                {
                    _passthroughViewportRayMethod = method;
                    break;
                }
            }

            return _passthroughViewportRayMethod;
        }

        private void LogRaySourceOnce(string source)
        {
            if (_loggedRaySource)
            {
                return;
            }

            _loggedRaySource = true;
            manager?.QueueUnityLog("INFO", $"VisionReceiverModule ray source: {source}");
        }

        private void EnsureLabelPool()
        {
            EnsureWorldSpaceCanvas();

            if (_labelPool.Length == pooledLabelCount)
            {
                return;
            }

            _labelPool = new PooledLabel[pooledLabelCount];
            for (int index = 0; index < pooledLabelCount; index++)
            {
                var labelObject = new GameObject($"VisionLabel_{index}", typeof(RectTransform), typeof(CanvasRenderer), typeof(TextMeshProUGUI));
                labelObject.transform.SetParent(worldSpaceLabelCanvas.transform, false);
                var rectTransform = (RectTransform)labelObject.transform;
                rectTransform.sizeDelta = labelSize;
                rectTransform.localScale = Vector3.one;

                TextMeshProUGUI text = labelObject.GetComponent<TextMeshProUGUI>();
                text.font = labelFont != null ? labelFont : text.font;
                text.fontSize = labelFontSize;
                text.color = labelTextColor;
                text.alignment = TextAlignmentOptions.Center;
                text.enableWordWrapping = false;
                text.overflowMode = TextOverflowModes.Overflow;
                text.outlineColor = labelOutlineColor;
                text.outlineWidth = labelOutlineWidth;
                text.text = string.Empty;
                text.raycastTarget = false;

                labelObject.SetActive(false);
                _labelPool[index] = new PooledLabel(rectTransform, text);
            }
        }

        private void EnsureWorldSpaceCanvas()
        {
            if (worldSpaceLabelCanvas != null)
            {
                return;
            }

            var canvasObject = new GameObject("VisionLabelCanvas", typeof(RectTransform), typeof(Canvas), typeof(CanvasScaler), typeof(GraphicRaycaster));
            canvasObject.transform.SetParent(transform, false);

            worldSpaceLabelCanvas = canvasObject.GetComponent<Canvas>();
            worldSpaceLabelCanvas.renderMode = RenderMode.WorldSpace;
            worldSpaceLabelCanvas.worldCamera = ResolveLabelCamera();

            RectTransform rectTransform = canvasObject.GetComponent<RectTransform>();
            rectTransform.sizeDelta = new Vector2(1024f, 1024f);
            rectTransform.localScale = Vector3.one * canvasScale;

            CanvasScaler scaler = canvasObject.GetComponent<CanvasScaler>();
            scaler.dynamicPixelsPerUnit = 1000f;
            scaler.referencePixelsPerUnit = 100f;
        }

        private Camera ResolveLabelCamera()
        {
            if (_labelCamera != null)
            {
                return _labelCamera;
            }

            _labelCamera = rayCamera != null ? rayCamera : Camera.main;
            return _labelCamera;
        }

        private void PublishWorldObjects(VisionWorldObject[] worldObjects)
        {
            _latestWorldObjects = worldObjects ?? Array.Empty<VisionWorldObject>();
            UpdateLabels(_latestWorldObjects);
            WorldObjectsUpdated?.Invoke(_latestWorldObjects);
        }

        private void UpdateLabels(VisionWorldObject[] worldObjects)
        {
            EnsureLabelPool();

            int visibleCount = worldObjects != null ? Mathf.Min(worldObjects.Length, _labelPool.Length) : 0;
            for (int index = 0; index < visibleCount; index++)
            {
                VisionWorldObject worldObject = worldObjects[index];
                PooledLabel pooledLabel = _labelPool[index];
                pooledLabel.Transform.position = worldObject.WorldPosition + labelWorldOffset;
                pooledLabel.Text.text = VisionLabelFormatting.FormatLabel(worldObject.Label, worldObject.Score);
                pooledLabel.GameObject.SetActive(true);
            }

            for (int index = visibleCount; index < _labelPool.Length; index++)
            {
                _labelPool[index].GameObject.SetActive(false);
            }
        }

        private void BillboardActiveLabels()
        {
            Camera camera = ResolveLabelCamera();
            if (camera == null)
            {
                return;
            }

            if (worldSpaceLabelCanvas != null)
            {
                worldSpaceLabelCanvas.worldCamera = camera;
            }

            Transform cameraTransform = camera.transform;
            for (int index = 0; index < _labelPool.Length; index++)
            {
                PooledLabel pooledLabel = _labelPool[index];
                if (!pooledLabel.GameObject.activeSelf)
                {
                    continue;
                }

                pooledLabel.Transform.LookAt(
                    pooledLabel.Transform.position + cameraTransform.rotation * Vector3.forward,
                    cameraTransform.rotation * Vector3.up);
            }
        }

        private readonly struct PooledLabel
        {
            public PooledLabel(RectTransform transform, TextMeshProUGUI text)
            {
                Transform = transform;
                Text = text;
            }

            public RectTransform Transform { get; }
            public TextMeshProUGUI Text { get; }
            public GameObject GameObject => Transform.gameObject;
        }
    }
}
