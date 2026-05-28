using UnityEngine;

namespace SmartRoom.Networking
{
    public enum TransportMode
    {
        Auto = 0,
        WiredUsbDebug = 1,
        WirelessLan = 2
    }

    [System.Serializable]
    public struct TransportEndpoint
    {
        public string host;
        public int port;
        public string protocol;

        public override string ToString()
        {
            return $"{protocol}://{host}:{port}";
        }
    }

    public class StreamTransportSwitcher : MonoBehaviour
    {
        [Header("Mode")]
        [SerializeField] private TransportMode mode = TransportMode.Auto;

        [Header("Editor Wired (USB Debug)")]
        [SerializeField] private TransportEndpoint wiredEndpoint = new TransportEndpoint
        {
            host = "127.0.0.1",
            port = 8500,
            protocol = "ws"
        };

        [Header("Device Wireless (LAN)")]
        [SerializeField] private TransportEndpoint wirelessEndpoint = new TransportEndpoint
        {
            host = "192.168.1.100",
            port = 8500,
            protocol = "ws"
        };

        [Header("Debug")]
        [SerializeField] private bool logResolvedEndpoint = true;

        public TransportMode ResolvedMode { get; private set; }
        public TransportEndpoint ActiveEndpoint { get; private set; }

        private void Awake()
        {
            RefreshActiveTransport();
        }

        [ContextMenu("Refresh Active Transport")]
        public void RefreshActiveTransport()
        {
            ResolvedMode = ResolveMode(mode);
            ActiveEndpoint = ResolvedMode == TransportMode.WiredUsbDebug
                ? wiredEndpoint
                : wirelessEndpoint;

            if (logResolvedEndpoint)
            {
                Debug.Log($"[Transport] Mode={ResolvedMode}, Endpoint={ActiveEndpoint}", this);
            }
        }

        private static TransportMode ResolveMode(TransportMode configuredMode)
        {
            if (configuredMode != TransportMode.Auto)
            {
                return configuredMode;
            }

#if UNITY_EDITOR
            return TransportMode.WiredUsbDebug;
#else
            return TransportMode.WirelessLan;
#endif
        }

        public string BuildWebSocketUrl(string path = "/ws/heartbeat")
        {
            if (path.Length == 0 || path[0] != '/')
            {
                path = "/" + path;
            }

            return $"{ActiveEndpoint.protocol}://{ActiveEndpoint.host}:{ActiveEndpoint.port}{path}";
        }
    }
}
