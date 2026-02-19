using System;
using System.Text.RegularExpressions;
using TMPro;
using UnityEngine;

namespace SmartRoom.Networking
{
    [Serializable]
    public class HeartbeatModulePayload
    {
        public string type;
        public string device_id;
        public int tick;
        public long timestamp_ms;
        public string app_version;
        public string unity_version;
        public string device_model;
        public string os;
        public string connection_mode;
    }

    public class HeartbeatModule : MonoBehaviour
    {
        [SerializeField] private BackendCommunicationManager manager;
        [SerializeField] private float intervalSeconds = 1f;

        [Header("Optional HUD Text")]
        [SerializeField] private TMP_Text titleText;
        [SerializeField] private TMP_Text counterText;
        [SerializeField] private TMP_Text statusText;

        private float _nextAt;
        private int _tick;
        private static readonly Regex StackLineRegex =
            new Regex(@"\(at\s+(.*):(\d+)\)", RegexOptions.Compiled);

        private void Awake()
        {
            if (manager == null)
            {
                manager = FindFirstObjectByType<BackendCommunicationManager>();
            }

            if (titleText != null)
            {
                titleText.text = "Hello World";
            }

            Application.logMessageReceived += HandleUnityLog;
        }

        private void Start()
        {
            _nextAt = Time.time + intervalSeconds;
            manager?.QueueUnityLog("INFO", "HeartbeatModule started.");
        }

        private void Update()
        {
            if (counterText != null)
            {
                counterText.text = $"Hello World {_tick}";
            }

            if (manager == null)
            {
                if (statusText != null)
                {
                    statusText.text = "Backend manager missing";
                }
                return;
            }

            if (statusText != null)
            {
                statusText.text = manager.IsControlConnected ? "Control connected" : "Control connecting...";
            }

            if (Time.time < _nextAt)
            {
                return;
            }

            _nextAt = Time.time + intervalSeconds;
            _tick++;

            var payload = new HeartbeatModulePayload
            {
                type = "heartbeat",
                device_id = SystemInfo.deviceUniqueIdentifier,
                tick = _tick,
                timestamp_ms = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                app_version = Application.version,
                unity_version = Application.unityVersion,
                device_model = SystemInfo.deviceModel,
                os = SystemInfo.operatingSystem,
                connection_mode = "ManagerMux",
            };

            manager.QueueControlJson(JsonUtility.ToJson(payload));
        }

        private void HandleUnityLog(string condition, string stackTrace, LogType type)
        {
            if (manager == null)
            {
                return;
            }

            string level = type switch
            {
                LogType.Error => "ERROR",
                LogType.Exception => "ERROR",
                LogType.Assert => "ERROR",
                LogType.Warning => "WARNING",
                _ => "INFO",
            };

            if (condition != null && condition.Contains("HeartbeatModule"))
            {
                return;
            }

            string script = null;
            int line = -1;
            if (!string.IsNullOrEmpty(stackTrace))
            {
                var match = StackLineRegex.Match(stackTrace);
                if (match.Success)
                {
                    script = match.Groups[1].Value;
                    int.TryParse(match.Groups[2].Value, out line);
                }
            }

            manager.QueueUnityLog(level, condition, script, line, stackTrace);
        }

        private void OnDestroy()
        {
            Application.logMessageReceived -= HandleUnityLog;
        }
    }
}
