using System;
using TMPro;
using UnityEngine;
using UnityEngine.EventSystems;

namespace SmartRoom.UI
{
    public sealed class QuestSystemKeyboard : MonoBehaviour
    {
        private const string ObjectName = "QuestSystemKeyboard";
        private const float StartupGraceSeconds = 0.35f;

        private static QuestSystemKeyboard _instance;

        private TMP_InputField _target;
        private TouchScreenKeyboard _keyboard;
        private Action _closed;
        private float _openedAt;
        private bool _inputFocusWasLost;

        public static QuestSystemKeyboard EnsureExists()
        {
            if (_instance != null)
                return _instance;

            GameObject go = GameObject.Find(ObjectName) ?? new GameObject(ObjectName);
            _instance = go.GetComponent<QuestSystemKeyboard>() ?? go.AddComponent<QuestSystemKeyboard>();
            DontDestroyOnLoad(go);
            return _instance;
        }

        public static void OpenFor(
            TMP_InputField target,
            string placeholder,
            bool multiline,
            int characterLimit,
            Action closed = null)
        {
            if (target == null)
                return;
            EnsureExists().Open(target, placeholder, multiline, characterLimit, closed);
        }

        public static void CloseFor(TMP_InputField target)
        {
            if (_instance != null && _instance._target == target)
                _instance.Close();
        }

        private void Awake()
        {
            if (_instance != null && _instance != this)
            {
                Destroy(gameObject);
                return;
            }

            _instance = this;
            DontDestroyOnLoad(gameObject);
            OVRManager.InputFocusLost += HandleInputFocusLost;
            OVRManager.InputFocusAcquired += HandleInputFocusAcquired;
        }

        private void Update()
        {
            SynchronizeKeyboard(forceClose: false);
        }

        private void OnDestroy()
        {
            OVRManager.InputFocusLost -= HandleInputFocusLost;
            OVRManager.InputFocusAcquired -= HandleInputFocusAcquired;
            if (_instance == this)
                _instance = null;
        }

        private void Open(
            TMP_InputField target,
            string placeholder,
            bool multiline,
            int characterLimit,
            Action closed)
        {
            if (_target == target && _keyboard != null &&
                _keyboard.status == TouchScreenKeyboard.Status.Visible)
            {
                return;
            }

            Close();

            _target = target;
            _closed = closed;
            _openedAt = Time.unscaledTime;
            _inputFocusWasLost = false;

            // TMP's automatic mobile keyboard path is unreliable for custom world-space
            // pointer events. This component owns the single explicit overlay instance.
            target.shouldHideSoftKeyboard = true;
            target.ActivateInputField();

            _keyboard = TouchScreenKeyboard.Open(
                target.text ?? string.Empty,
                TouchScreenKeyboardType.Default,
                autocorrection: true,
                multiline: multiline,
                secure: false,
                alert: false,
                textPlaceholder: placeholder ?? string.Empty,
                characterLimit: Mathf.Max(0, characterLimit));

            if (_keyboard == null)
            {
                Debug.LogError(
                    "[QuestSystemKeyboard] System keyboard could not be opened. " +
                    "Verify OVR Project Config > Require System Keyboard and focus awareness.");
                Finish();
            }
        }

        private void Close()
        {
            if (_keyboard != null)
            {
                SynchronizeText();
                _keyboard.active = false;
            }
            Finish();
        }

        private void HandleInputFocusLost()
        {
            if (_keyboard != null)
                _inputFocusWasLost = true;
        }

        private void HandleInputFocusAcquired()
        {
            if (_keyboard == null)
                return;

            SynchronizeText();
            if (_inputFocusWasLost &&
                _keyboard.status != TouchScreenKeyboard.Status.Visible)
            {
                Finish();
            }
        }

        private void SynchronizeKeyboard(bool forceClose)
        {
            if (_keyboard == null)
                return;

            SynchronizeText();
            if (forceClose)
            {
                Finish();
                return;
            }

            if (_keyboard.status == TouchScreenKeyboard.Status.Visible)
                return;

            // On Quest the overlay can take input focus before Unity reports Visible.
            if (Time.unscaledTime - _openedAt < StartupGraceSeconds)
            {
                return;
            }

            Finish();
        }

        private void SynchronizeText()
        {
            if (_keyboard == null || _target == null)
                return;

            string value = _keyboard.text ?? string.Empty;
            if (_target.characterLimit > 0 && value.Length > _target.characterLimit)
                value = value.Substring(0, _target.characterLimit);
            if (!string.Equals(_target.text, value, StringComparison.Ordinal))
                _target.text = value;
        }

        private void Finish()
        {
            TMP_InputField target = _target;
            Action closed = _closed;
            _keyboard = null;
            _target = null;
            _closed = null;
            _inputFocusWasLost = false;

            if (target != null)
                target.DeactivateInputField();
            closed?.Invoke();
        }
    }

    public sealed class QuestSystemKeyboardInputBridge : MonoBehaviour, IPointerClickHandler
    {
        private TMP_InputField _input;
        private string _placeholder = string.Empty;
        private bool _multiline;
        private int _characterLimit;
        private Action _closed;

        public void Configure(
            TMP_InputField input,
            string placeholder,
            bool multiline,
            int characterLimit,
            Action closed = null)
        {
            _input = input;
            _placeholder = placeholder ?? string.Empty;
            _multiline = multiline;
            _characterLimit = characterLimit;
            _closed = closed;
            if (_input != null)
                _input.shouldHideSoftKeyboard = true;
        }

        public void OnPointerClick(PointerEventData eventData)
        {
            QuestSystemKeyboard.OpenFor(
                _input,
                _placeholder,
                _multiline,
                _characterLimit,
                _closed);
        }
    }
}
