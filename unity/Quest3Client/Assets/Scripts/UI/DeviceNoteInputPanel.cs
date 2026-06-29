using System;
using System.Collections.Generic;
using SmartRoom.Interaction;
using TMPro;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.InputSystem.UI;
using UnityEngine.UI;

namespace SmartRoom.UI
{
    public sealed class DeviceNoteInputPanel : MonoBehaviour
    {
        private const string DefaultObjectName = "DeviceNoteInputPanel";

        private readonly List<RaycastResult> _raycastResults = new List<RaycastResult>();
        private Camera _camera;
        private ControllerRaycaster _controllerRaycaster;
        private RectTransform _canvasRoot;
        private GraphicRaycaster _graphicRaycaster;
        private TMP_InputField _input;
        private PointerEventData _pointerData;
        private EventSystem _eventSystem;
        private GameObject _hoveredObject;
        private GameObject _pressedObject;
        private Action<bool, string> _completed;
        private bool _visible;

        private static DeviceNoteInputPanel _instance;
        public static bool IsPanelVisible => _instance != null && _instance._visible;

        public static DeviceNoteInputPanel EnsureExists(Camera camera = null)
        {
            if (_instance == null)
            {
                GameObject go = GameObject.Find(DefaultObjectName) ?? new GameObject(DefaultObjectName);
                _instance = go.GetComponent<DeviceNoteInputPanel>() ?? go.AddComponent<DeviceNoteInputPanel>();
            }
            if (camera != null)
                _instance._camera = camera;
            return _instance;
        }

        public static void Open(Action<bool, string> completed, Camera camera = null)
        {
            EnsureExists(camera).Show(completed);
        }

        private void Awake()
        {
            _instance = this;
            ResolveReferences();
            BuildUi();
            HideVisual();
        }

        private void Update()
        {
            if (!_visible)
                return;
            ResolveReferences();
            UpdateControllerInput();
        }

        private void OnDestroy()
        {
            if (_instance == this)
                _instance = null;
        }

        private void ResolveReferences()
        {
            if (_camera == null)
                _camera = Camera.main;
            if (_controllerRaycaster == null)
                _controllerRaycaster = FindFirstObjectByType<ControllerRaycaster>();
        }

        private void Show(Action<bool, string> completed)
        {
            ResolveReferences();
            _completed = completed;
            _visible = true;
            _input.text = string.Empty;
            if (_canvasRoot != null)
            {
                _canvasRoot.gameObject.SetActive(true);
                Transform head = _camera != null ? _camera.transform : null;
                if (head != null)
                {
                    transform.position = head.position + head.forward * 0.82f + Vector3.down * 0.04f;
                    transform.rotation = Quaternion.LookRotation(transform.position - head.position, Vector3.up);
                }
            }
            OpenKeyboard();
        }

        private void Finish(bool confirmed, bool includeText)
        {
            string note = includeText ? (_input.text ?? string.Empty).Trim() : string.Empty;
            Action<bool, string> callback = _completed;
            _completed = null;
            HideVisual();
            callback?.Invoke(confirmed, note);
        }

        private void HideVisual()
        {
            _visible = false;
            QuestSystemKeyboard.CloseFor(_input);
            ClearHover();
            _pressedObject = null;
            if (_canvasRoot != null)
                _canvasRoot.gameObject.SetActive(false);
        }

        private void OpenKeyboard()
        {
            QuestSystemKeyboard.OpenFor(
                _input,
                "Optional note about this device",
                multiline: true,
                characterLimit: 280);
        }

        private void BuildUi()
        {
            EnsureEventSystem();
            GameObject canvasObject = new GameObject(
                "DeviceNoteCanvas",
                typeof(RectTransform),
                typeof(Canvas),
                typeof(CanvasScaler),
                typeof(GraphicRaycaster),
                typeof(TrackedDeviceRaycaster),
                typeof(Image));
            canvasObject.transform.SetParent(transform, false);
            _canvasRoot = canvasObject.GetComponent<RectTransform>();
            _canvasRoot.sizeDelta = new Vector2(720f, 430f);
            _canvasRoot.localScale = Vector3.one * 0.0011f;
            canvasObject.GetComponent<Image>().color = new Color(0.035f, 0.04f, 0.05f, 0.97f);

            Canvas canvas = canvasObject.GetComponent<Canvas>();
            canvas.renderMode = RenderMode.WorldSpace;
            canvas.worldCamera = _camera;
            canvas.sortingOrder = 35;
            _graphicRaycaster = canvasObject.GetComponent<GraphicRaycaster>();

            CanvasScaler scaler = canvasObject.GetComponent<CanvasScaler>();
            scaler.dynamicPixelsPerUnit = 1000f;
            scaler.referencePixelsPerUnit = 100f;

            TextMeshProUGUI title = CreateText(
                _canvasRoot,
                "Title",
                "Optional device note",
                32f,
                FontStyles.Bold,
                TextAlignmentOptions.Left);
            SetTopLeft(title.rectTransform, 28f, 24f, 560f, 42f);

            TextMeshProUGUI subtitle = CreateText(
                _canvasRoot,
                "Subtitle",
                "This note will be supplied to the visual analysis as user-provided context.",
                18f,
                FontStyles.Normal,
                TextAlignmentOptions.Left);
            subtitle.color = new Color(0.75f, 0.81f, 0.87f, 1f);
            SetTopLeft(subtitle.rectTransform, 28f, 72f, 664f, 50f);

            _input = CreateInput(_canvasRoot, "NoteInput");
            SetTopLeft((RectTransform)_input.transform, 28f, 132f, 664f, 170f);

            Button keyboardButton = CreateButton(_canvasRoot, "Keyboard", "Keyboard", new Color(0.18f, 0.34f, 0.54f, 0.98f));
            SetBottomLeft((RectTransform)keyboardButton.transform, 28f, 24f, 132f, 48f);
            keyboardButton.onClick.AddListener(OpenKeyboard);

            Button cancelButton = CreateButton(_canvasRoot, "Cancel", "Cancel", new Color(0.28f, 0.29f, 0.32f, 0.98f));
            SetBottomRight((RectTransform)cancelButton.transform, 288f, 24f, 112f, 48f);
            cancelButton.onClick.AddListener(() => Finish(false, false));

            Button skipButton = CreateButton(_canvasRoot, "Skip", "Skip", new Color(0.34f, 0.30f, 0.18f, 0.98f));
            SetBottomRight((RectTransform)skipButton.transform, 160f, 24f, 112f, 48f);
            skipButton.onClick.AddListener(() => Finish(true, false));

            Button continueButton = CreateButton(_canvasRoot, "Continue", "Continue", new Color(0.12f, 0.48f, 0.34f, 0.98f));
            SetBottomRight((RectTransform)continueButton.transform, 28f, 24f, 116f, 48f);
            continueButton.onClick.AddListener(() => Finish(true, true));
        }

        private void UpdateControllerInput()
        {
            if (!TryRaycast(out GameObject target, out PointerEventData pointerData))
            {
                ClearHover();
                if (OVRInput.GetUp(OVRInput.RawButton.RIndexTrigger))
                    ReleasePressed(pointerData, null);
                return;
            }

            if (_hoveredObject != target)
            {
                ClearHover();
                _hoveredObject = target;
                ExecuteEvents.ExecuteHierarchy(target, pointerData, ExecuteEvents.pointerEnterHandler);
            }
            if (OVRInput.GetDown(OVRInput.RawButton.RIndexTrigger))
            {
                _pressedObject = ExecuteEvents.ExecuteHierarchy(target, pointerData, ExecuteEvents.pointerDownHandler);
                if (_pressedObject == null)
                    _pressedObject = ExecuteEvents.GetEventHandler<IPointerClickHandler>(target);
            }
            if (OVRInput.GetUp(OVRInput.RawButton.RIndexTrigger))
                ReleasePressed(pointerData, target);
        }

        private bool TryRaycast(out GameObject target, out PointerEventData pointerData)
        {
            target = null;
            pointerData = null;
            if (_canvasRoot == null || _graphicRaycaster == null || _camera == null || _controllerRaycaster == null)
                return false;
            Ray ray = _controllerRaycaster.GetRay();
            Plane plane = new Plane(_canvasRoot.forward, _canvasRoot.position);
            if (!plane.Raycast(ray, out float distance) || distance < 0f)
                return false;

            Vector3 world = ray.GetPoint(distance);
            _eventSystem = EventSystem.current;
            if (_eventSystem == null)
                return false;
            _pointerData ??= new PointerEventData(_eventSystem);
            _pointerData.Reset();
            _pointerData.position = _camera.WorldToScreenPoint(world);
            _raycastResults.Clear();
            _graphicRaycaster.Raycast(_pointerData, _raycastResults);
            if (_raycastResults.Count == 0)
                return false;
            target = _raycastResults[0].gameObject;
            pointerData = _pointerData;
            return target != null;
        }

        private void ReleasePressed(PointerEventData pointerData, GameObject currentTarget)
        {
            if (_pressedObject == null || pointerData == null)
            {
                _pressedObject = null;
                return;
            }
            ExecuteEvents.Execute(_pressedObject, pointerData, ExecuteEvents.pointerUpHandler);
            GameObject pressedHandler = ExecuteEvents.GetEventHandler<IPointerClickHandler>(_pressedObject);
            GameObject currentHandler = currentTarget != null
                ? ExecuteEvents.GetEventHandler<IPointerClickHandler>(currentTarget)
                : null;
            if (pressedHandler != null && pressedHandler == currentHandler)
                ExecuteEvents.Execute(pressedHandler, pointerData, ExecuteEvents.pointerClickHandler);
            _pressedObject = null;
        }

        private void ClearHover()
        {
            if (_hoveredObject != null && _pointerData != null)
                ExecuteEvents.ExecuteHierarchy(_hoveredObject, _pointerData, ExecuteEvents.pointerExitHandler);
            _hoveredObject = null;
        }

        private static TMP_InputField CreateInput(Transform parent, string name)
        {
            GameObject go = new GameObject(name, typeof(RectTransform), typeof(Image), typeof(TMP_InputField));
            go.transform.SetParent(parent, false);
            go.GetComponent<Image>().color = new Color(0.93f, 0.95f, 0.97f, 1f);
            RectTransform root = (RectTransform)go.transform;
            RectTransform viewport = AddRect(root, "Viewport");
            Stretch(viewport, 12f, 12f, 10f, 10f);
            TextMeshProUGUI text = CreateText(viewport, "Text", string.Empty, 21f, FontStyles.Normal, TextAlignmentOptions.TopLeft);
            text.color = new Color(0.04f, 0.05f, 0.06f, 1f);
            text.textWrappingMode = TextWrappingModes.Normal;
            Stretch(text.rectTransform, 4f, 4f, 4f, 4f);
            TextMeshProUGUI placeholder = CreateText(
                viewport,
                "Placeholder",
                "Example: This is a desk lamp with temperature sensing.",
                20f,
                FontStyles.Italic,
                TextAlignmentOptions.TopLeft);
            placeholder.color = new Color(0.43f, 0.47f, 0.51f, 1f);
            placeholder.textWrappingMode = TextWrappingModes.Normal;
            Stretch(placeholder.rectTransform, 4f, 4f, 4f, 4f);
            TMP_InputField input = go.GetComponent<TMP_InputField>();
            input.textViewport = viewport;
            input.textComponent = text;
            input.placeholder = placeholder;
            input.lineType = TMP_InputField.LineType.MultiLineNewline;
            input.characterLimit = 280;
            QuestSystemKeyboardInputBridge bridge =
                go.AddComponent<QuestSystemKeyboardInputBridge>();
            bridge.Configure(
                input,
                "Optional note about this device",
                multiline: true,
                characterLimit: 280);
            return input;
        }

        private static Button CreateButton(Transform parent, string name, string label, Color color)
        {
            GameObject go = new GameObject(name, typeof(RectTransform), typeof(Image), typeof(Button));
            go.transform.SetParent(parent, false);
            go.GetComponent<Image>().color = color;
            Button button = go.GetComponent<Button>();
            TextMeshProUGUI text = CreateText(go.transform, "Label", label, 19f, FontStyles.Bold, TextAlignmentOptions.Center);
            Stretch(text.rectTransform, 4f, 4f, 4f, 4f);
            return button;
        }

        private static TextMeshProUGUI CreateText(
            Transform parent,
            string name,
            string value,
            float size,
            FontStyles style,
            TextAlignmentOptions alignment)
        {
            GameObject go = new GameObject(name, typeof(RectTransform), typeof(CanvasRenderer), typeof(TextMeshProUGUI));
            go.transform.SetParent(parent, false);
            TextMeshProUGUI text = go.GetComponent<TextMeshProUGUI>();
            text.text = value;
            text.fontSize = size;
            text.fontStyle = style;
            text.alignment = alignment;
            text.color = Color.white;
            return text;
        }

        private static RectTransform AddRect(Transform parent, string name)
        {
            GameObject go = new GameObject(name, typeof(RectTransform));
            go.transform.SetParent(parent, false);
            return (RectTransform)go.transform;
        }

        private static void EnsureEventSystem()
        {
            if (FindFirstObjectByType<EventSystem>() != null)
                return;
            GameObject go = new GameObject("EventSystem", typeof(EventSystem), typeof(InputSystemUIInputModule));
            go.GetComponent<InputSystemUIInputModule>().AssignDefaultActions();
        }

        private static void Stretch(RectTransform rect, float left, float right, float top, float bottom)
        {
            rect.anchorMin = Vector2.zero;
            rect.anchorMax = Vector2.one;
            rect.offsetMin = new Vector2(left, bottom);
            rect.offsetMax = new Vector2(-right, -top);
        }

        private static void SetTopLeft(RectTransform rect, float x, float y, float width, float height)
        {
            rect.anchorMin = rect.anchorMax = rect.pivot = new Vector2(0f, 1f);
            rect.anchoredPosition = new Vector2(x, -y);
            rect.sizeDelta = new Vector2(width, height);
        }

        private static void SetBottomLeft(RectTransform rect, float x, float y, float width, float height)
        {
            rect.anchorMin = rect.anchorMax = rect.pivot = new Vector2(0f, 0f);
            rect.anchoredPosition = new Vector2(x, y);
            rect.sizeDelta = new Vector2(width, height);
        }

        private static void SetBottomRight(RectTransform rect, float x, float y, float width, float height)
        {
            rect.anchorMin = rect.anchorMax = rect.pivot = new Vector2(1f, 0f);
            rect.anchoredPosition = new Vector2(-x, y);
            rect.sizeDelta = new Vector2(width, height);
        }
    }
}
