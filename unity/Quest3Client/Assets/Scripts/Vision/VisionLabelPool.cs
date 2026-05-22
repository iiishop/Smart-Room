using System;
using TMPro;
using UnityEngine;
using UnityEngine.UI;

namespace SmartRoom.Vision
{
    public sealed class VisionLabelPool : MonoBehaviour
    {
        private const float DefaultExtraCapacityRatio = 0.2f;
        private const int DefaultFallbackMaxObjects = 60;
        private const int MaxPoolCapacity = 128;

        [SerializeField] private VisionRenderConfig renderConfig;
        [SerializeField] private Canvas worldSpaceLabelCanvas;
        [SerializeField] private TMP_FontAsset labelFont;
        [SerializeField] private Camera labelCamera;
        [SerializeField] private Vector2 labelSize = new Vector2(320f, 72f);
        [SerializeField] private float labelFontSize = 36f;
        [SerializeField] private float canvasScale = 0.001f;
        [SerializeField] private Color labelTextColor = Color.white;
        [SerializeField] private Color labelOutlineColor = new Color(0f, 0f, 0f, 0.85f);
        [SerializeField, Range(0f, 1f)] private float labelOutlineWidth = 0.2f;

        private PooledLabel[] _pool = Array.Empty<PooledLabel>();
        private int _activeCount;

        public int Capacity => _pool.Length;

        private void Awake()
        {
            EnsurePool();
        }

        public void SetLabelCamera(Camera camera)
        {
            labelCamera = camera;
            if (worldSpaceLabelCanvas != null)
            {
                worldSpaceLabelCanvas.worldCamera = labelCamera;
            }
        }

        public void SyncObjects(VisionObjectProcessedData[] objects)
        {
            EnsurePool();
            ReleaseAll();

            int count = objects != null ? objects.Length : 0;
            for (int index = 0; index < count; index++)
            {
                if (!TryAcquire(objects[index]))
                {
                    break;
                }
            }
        }

        public void Clear()
        {
            ReleaseAll();
        }

        public void UpdateBillboards()
        {
            BillboardActiveLabels();
        }

        private bool TryAcquire(VisionObjectProcessedData processedObject)
        {
            if (_activeCount >= _pool.Length || processedObject == null || !processedObject.CenterValid)
            {
                return false;
            }

            ref PooledLabel pooledLabel = ref _pool[_activeCount++];
            pooledLabel.Transform.position = processedObject.Center3D + ResolveLabelOffset();
            pooledLabel.Text.text = SmartRoom.Networking.VisionLabelFormatting.FormatLabel(processedObject.Label, processedObject.Score);
            pooledLabel.GameObject.SetActive(true);
            return true;
        }

        private void ReleaseAll()
        {
            for (int index = 0; index < _activeCount; index++)
            {
                _pool[index].GameObject.SetActive(false);
            }

            _activeCount = 0;
        }

        private void EnsurePool()
        {
            if (_pool.Length > 0)
            {
                return;
            }

            EnsureWorldSpaceCanvas();

            int targetCapacity = ResolvePoolCapacity();
            _pool = new PooledLabel[targetCapacity];
            _activeCount = 0;

            for (int index = 0; index < targetCapacity; index++)
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
                _pool[index] = new PooledLabel(rectTransform, text);
            }
        }

        private void EnsureWorldSpaceCanvas()
        {
            if (worldSpaceLabelCanvas != null)
            {
                worldSpaceLabelCanvas.renderMode = RenderMode.WorldSpace;
                worldSpaceLabelCanvas.worldCamera = labelCamera;
                return;
            }

            var canvasObject = new GameObject("VisionLabelCanvas", typeof(RectTransform), typeof(Canvas), typeof(CanvasScaler), typeof(GraphicRaycaster));
            canvasObject.transform.SetParent(transform, false);

            worldSpaceLabelCanvas = canvasObject.GetComponent<Canvas>();
            worldSpaceLabelCanvas.renderMode = RenderMode.WorldSpace;
            worldSpaceLabelCanvas.worldCamera = labelCamera;

            RectTransform rectTransform = canvasObject.GetComponent<RectTransform>();
            rectTransform.sizeDelta = new Vector2(1024f, 1024f);
            rectTransform.localScale = Vector3.one * canvasScale;

            CanvasScaler scaler = canvasObject.GetComponent<CanvasScaler>();
            scaler.dynamicPixelsPerUnit = 1000f;
            scaler.referencePixelsPerUnit = 100f;
        }

        private void BillboardActiveLabels()
        {
            if (labelCamera == null)
            {
                return;
            }

            if (worldSpaceLabelCanvas != null)
            {
                worldSpaceLabelCanvas.worldCamera = labelCamera;
            }

            Transform cameraTransform = labelCamera.transform;
            for (int index = 0; index < _activeCount; index++)
            {
                PooledLabel pooledLabel = _pool[index];
                pooledLabel.Transform.LookAt(
                    pooledLabel.Transform.position + cameraTransform.rotation * Vector3.forward,
                    cameraTransform.rotation * Vector3.up);
            }
        }

        private Vector3 ResolveLabelOffset()
        {
            if (renderConfig != null)
            {
                return renderConfig.labelOffset;
            }

            return new Vector3(0f, 0.05f, 0f);
        }

        private int ResolvePoolCapacity()
        {
            int maxObjects = renderConfig != null ? renderConfig.maxObjects : DefaultFallbackMaxObjects;
            maxObjects = Mathf.Max(1, maxObjects);
            int capacity = Mathf.CeilToInt(maxObjects * (1f + DefaultExtraCapacityRatio));
            return Mathf.Clamp(capacity, 1, MaxPoolCapacity);
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
