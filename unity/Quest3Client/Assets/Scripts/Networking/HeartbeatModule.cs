using System;
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

    }
}
