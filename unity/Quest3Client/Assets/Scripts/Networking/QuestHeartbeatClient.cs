using System;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using TMPro;
using UnityEngine;

namespace SmartRoom.Networking
{
    [Serializable]
    public class HeartbeatPayload
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

    public class QuestHeartbeatClient : MonoBehaviour
    {
        [Header("Transport")]
        [SerializeField] private StreamTransportSwitcher transportSwitcher;
        [SerializeField] private string websocketPath = "/ws/heartbeat";
        [SerializeField] private float heartbeatIntervalSeconds = 1f;
        [SerializeField] private float reconnectDelaySeconds = 2f;

        [Header("Optional HUD Text")]
        [SerializeField] private TMP_Text titleText;
        [SerializeField] private TMP_Text counterText;
        [SerializeField] private TMP_Text statusText;

        private ClientWebSocket _socket;
        private CancellationTokenSource _cts;
        private bool _isConnecting;
        private bool _isSending;
        private bool _isQuitting;
        private float _nextHeartbeatAt;
        private int _tick;

        private void Awake()
        {
            if (transportSwitcher == null)
            {
                transportSwitcher = FindFirstObjectByType<StreamTransportSwitcher>();
            }

            if (titleText != null)
            {
                titleText.text = "Hello World";
            }
        }

        private void Start()
        {
            _cts = new CancellationTokenSource();
            _ = EnsureConnectedAsync();
            _nextHeartbeatAt = Time.time + heartbeatIntervalSeconds;
        }

        private void Update()
        {
            if (counterText != null)
            {
                counterText.text = $"Hello World {_tick}";
            }

            if (Time.time < _nextHeartbeatAt)
            {
                return;
            }

            _nextHeartbeatAt = Time.time + heartbeatIntervalSeconds;

            if (_socket == null || _socket.State != WebSocketState.Open)
            {
                _ = EnsureConnectedAsync();
                UpdateStatusText("Connecting...");
                return;
            }

            if (!_isSending)
            {
                _ = SendHeartbeatAsync();
            }
        }

        private async Task EnsureConnectedAsync()
        {
            if (_isConnecting || _isQuitting)
            {
                return;
            }

            _isConnecting = true;

            try
            {
                while (!_isQuitting)
                {
                    if (_socket != null && _socket.State == WebSocketState.Open)
                    {
                        return;
                    }

                    try
                    {
                        transportSwitcher.RefreshActiveTransport();
                        string url = transportSwitcher.BuildWebSocketUrl(websocketPath);

                        _socket?.Dispose();
                        _socket = new ClientWebSocket();
                        await _socket.ConnectAsync(new Uri(url), _cts.Token);

                        UpdateStatusText($"Connected: {url}");
                        _ = ReceiveLoopAsync();
                        return;
                    }
                    catch (Exception ex)
                    {
                        UpdateStatusText($"Retrying... {ex.Message}");
                        await Task.Delay(TimeSpan.FromSeconds(reconnectDelaySeconds), _cts.Token);
                    }
                }
            }
            finally
            {
                _isConnecting = false;
            }
        }

        private async Task SendHeartbeatAsync()
        {
            if (_socket == null || _socket.State != WebSocketState.Open)
            {
                return;
            }

            _isSending = true;
            _tick++;

            try
            {
                var payload = new HeartbeatPayload
                {
                    type = "heartbeat",
                    device_id = SystemInfo.deviceUniqueIdentifier,
                    tick = _tick,
                    timestamp_ms = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                    app_version = Application.version,
                    unity_version = Application.unityVersion,
                    device_model = SystemInfo.deviceModel,
                    os = SystemInfo.operatingSystem,
                    connection_mode = transportSwitcher.ResolvedMode.ToString()
                };

                string json = JsonUtility.ToJson(payload);
                byte[] bytes = Encoding.UTF8.GetBytes(json);

                await _socket.SendAsync(
                    new ArraySegment<byte>(bytes),
                    WebSocketMessageType.Text,
                    true,
                    _cts.Token
                );

                UpdateStatusText($"Connected, sent #{_tick}");
            }
            catch (Exception ex)
            {
                UpdateStatusText($"Send failed: {ex.Message}");
                _ = EnsureConnectedAsync();
            }
            finally
            {
                _isSending = false;
            }
        }

        private async Task ReceiveLoopAsync()
        {
            if (_socket == null)
            {
                return;
            }

            var buffer = new byte[1024];

            try
            {
                while (!_isQuitting && _socket.State == WebSocketState.Open)
                {
                    var result = await _socket.ReceiveAsync(new ArraySegment<byte>(buffer), _cts.Token);

                    if (result.MessageType == WebSocketMessageType.Close)
                    {
                        UpdateStatusText("Server closed connection");
                        break;
                    }
                }
            }
            catch
            {
                if (!_isQuitting)
                {
                    UpdateStatusText("Connection lost");
                }
            }

            if (!_isQuitting)
            {
                _ = EnsureConnectedAsync();
            }
        }

        private void UpdateStatusText(string value)
        {
            if (statusText != null)
            {
                statusText.text = value;
            }

            Debug.Log($"[Heartbeat] {value}", this);
        }

        private async void OnApplicationQuit()
        {
            _isQuitting = true;

            if (_socket != null)
            {
                try
                {
                    await _socket.CloseAsync(WebSocketCloseStatus.NormalClosure, "Quit", CancellationToken.None);
                }
                catch
                {
                }

                _socket.Dispose();
            }

            _cts?.Cancel();
            _cts?.Dispose();
        }
    }
}
