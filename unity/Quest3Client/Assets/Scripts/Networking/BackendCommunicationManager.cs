using System;
using System.Collections.Concurrent;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using UnityEngine;

namespace SmartRoom.Networking
{
    public class BackendCommunicationManager : MonoBehaviour
    {
        public event Action<string> ControlMessageReceived;
        public event Action<string> VisionMessageReceived;
        [Header("Transport")]
        [SerializeField] private StreamTransportSwitcher transportSwitcher;
        [SerializeField] private string controlPath = "/ws/heartbeat";
        [SerializeField] private string rgbPath = "/ws/rgb";
        [SerializeField] private string depthPath = "/ws/depth";
        [SerializeField] private string visionPath = "/ws/vision";
        [SerializeField] private float reconnectDelaySeconds = 2f;

        private ClientWebSocket _controlSocket;
        private ClientWebSocket _rgbSocket;
        private ClientWebSocket _depthSocket;
        private ClientWebSocket _visionSocket;
        private CancellationTokenSource _cts;

        private readonly ConcurrentQueue<string> _controlQueue = new ConcurrentQueue<string>();
        private byte[] _latestRgbPacket;
        private byte[] _latestDepthPacket;
        private long _latestRgbTimestampMs;

        private bool _controlConnecting;
        private bool _rgbConnecting;
        private bool _depthConnecting;
        private bool _visionConnecting;
        private bool _controlSending;
        private bool _rgbSending;
        private bool _depthSending;
        private bool _isQuitting;
        private float _nextControlReconnectAt;
        private float _nextRgbReconnectAt;
        private float _nextDepthReconnectAt;
        private float _nextVisionReconnectAt;

        public bool IsControlConnected => _controlSocket != null && _controlSocket.State == WebSocketState.Open;
        public bool IsRgbConnected => _rgbSocket != null && _rgbSocket.State == WebSocketState.Open;
        public bool IsDepthConnected => _depthSocket != null && _depthSocket.State == WebSocketState.Open;
        public bool IsVisionConnected => _visionSocket != null && _visionSocket.State == WebSocketState.Open;
        public long LatestRgbTimestampMs => _latestRgbTimestampMs;

        private void Awake()
        {
            if (transportSwitcher == null)
            {
                transportSwitcher = FindFirstObjectByType<StreamTransportSwitcher>();
            }
        }

        private void Start()
        {
            _cts = new CancellationTokenSource();
            _nextControlReconnectAt = Time.time;
            _nextRgbReconnectAt = Time.time;
            _nextDepthReconnectAt = Time.time;
            _nextVisionReconnectAt = Time.time;
        }

        private void Update()
        {
            if (_isQuitting)
            {
                return;
            }

            if (!IsControlConnected && Time.time >= _nextControlReconnectAt)
            {
                _nextControlReconnectAt = Time.time + reconnectDelaySeconds;
                _ = EnsureControlConnectedAsync();
            }

            if (!IsRgbConnected && Time.time >= _nextRgbReconnectAt)
            {
                _nextRgbReconnectAt = Time.time + reconnectDelaySeconds;
                _ = EnsureRgbConnectedAsync();
            }

            if (!IsDepthConnected && Time.time >= _nextDepthReconnectAt)
            {
                _nextDepthReconnectAt = Time.time + reconnectDelaySeconds;
                _ = EnsureDepthConnectedAsync();
            }

            if (!IsVisionConnected && Time.time >= _nextVisionReconnectAt)
            {
                _nextVisionReconnectAt = Time.time + reconnectDelaySeconds;
                _ = EnsureVisionConnectedAsync();
            }

            if (IsControlConnected && !_controlSending)
            {
                _ = FlushControlQueueAsync();
            }

            if (IsRgbConnected && !_rgbSending && _latestRgbPacket != null)
            {
                _ = FlushLatestRgbAsync();
            }

            if (IsDepthConnected && !_depthSending && _latestDepthPacket != null)
            {
                _ = FlushLatestDepthAsync();
            }
        }

        public void QueueControlJson(string json)
        {
            if (string.IsNullOrWhiteSpace(json))
            {
                return;
            }

            _controlQueue.Enqueue(json);
        }

        public void QueueRgbPacket(byte[] packet)
        {
            if (packet == null || packet.Length == 0)
            {
                return;
            }

            _latestRgbPacket = packet;
        }

        public void PublishLatestRgbTimestamp(long timestampMs)
        {
            _latestRgbTimestampMs = timestampMs;
        }

        public void QueueDepthPacket(byte[] packet)
        {
            if (packet == null || packet.Length == 0)
            {
                return;
            }

            _latestDepthPacket = packet;
        }

        private async Task EnsureControlConnectedAsync()
        {
            if (_controlConnecting || _isQuitting)
            {
                return;
            }

            _controlConnecting = true;
            try
            {
                transportSwitcher.RefreshActiveTransport();
                string url = transportSwitcher.BuildWebSocketUrl(controlPath);

                _controlSocket?.Dispose();
                _controlSocket = new ClientWebSocket();
                await _controlSocket.ConnectAsync(new Uri(url), _cts.Token);

                QueueUnityLog("INFO", $"Control websocket connected: {url}");
                _ = ReceiveControlLoopAsync();
            }
            catch (Exception ex)
            {
                QueueUnityLog("WARNING", $"Control websocket connect retry: {ex.Message}", stackTrace: ex.ToString());
            }
            finally
            {
                _controlConnecting = false;
            }
        }

        private async Task EnsureRgbConnectedAsync()
        {
            if (_rgbConnecting || _isQuitting)
            {
                return;
            }

            _rgbConnecting = true;
            try
            {
                transportSwitcher.RefreshActiveTransport();
                string url = transportSwitcher.BuildWebSocketUrl(rgbPath);

                _rgbSocket?.Dispose();
                _rgbSocket = new ClientWebSocket();
                await _rgbSocket.ConnectAsync(new Uri(url), _cts.Token);

                QueueUnityLog("INFO", $"RGB websocket connected: {url}");
            }
            catch (Exception ex)
            {
                QueueUnityLog("WARNING", $"RGB websocket connect retry: {ex.Message}", stackTrace: ex.ToString());
            }
            finally
            {
                _rgbConnecting = false;
            }
        }

        private async Task EnsureDepthConnectedAsync()
        {
            if (_depthConnecting || _isQuitting)
            {
                return;
            }

            _depthConnecting = true;
            try
            {
                transportSwitcher.RefreshActiveTransport();
                string url = transportSwitcher.BuildWebSocketUrl(depthPath);

                _depthSocket?.Dispose();
                _depthSocket = new ClientWebSocket();
                await _depthSocket.ConnectAsync(new Uri(url), _cts.Token);

                QueueUnityLog("INFO", $"Depth websocket connected: {url}");
            }
            catch (Exception ex)
            {
                QueueUnityLog("WARNING", $"Depth websocket connect retry: {ex.Message}", stackTrace: ex.ToString());
            }
            finally
            {
                _depthConnecting = false;
            }
        }

        private async Task EnsureVisionConnectedAsync()
        {
            if (_visionConnecting || _isQuitting)
            {
                return;
            }

            _visionConnecting = true;
            try
            {
                transportSwitcher.RefreshActiveTransport();
                string url = transportSwitcher.BuildWebSocketUrl(visionPath);

                _visionSocket?.Dispose();
                _visionSocket = new ClientWebSocket();
                await _visionSocket.ConnectAsync(new Uri(url), _cts.Token);

                QueueUnityLog("INFO", $"Vision websocket connected: {url}");
                _ = ReceiveVisionLoopAsync();
            }
            catch (Exception ex)
            {
                QueueUnityLog("WARNING", $"Vision websocket connect retry: {ex.Message}", stackTrace: ex.ToString());
            }
            finally
            {
                _visionConnecting = false;
            }
        }

        private async Task FlushControlQueueAsync()
        {
            if (_controlSocket == null || _controlSocket.State != WebSocketState.Open)
            {
                return;
            }

            _controlSending = true;
            try
            {
                int sent = 0;
                while (sent < 20 && _controlQueue.TryDequeue(out var json))
                {
                    byte[] bytes = Encoding.UTF8.GetBytes(json);
                    await _controlSocket.SendAsync(new ArraySegment<byte>(bytes), WebSocketMessageType.Text, true, _cts.Token);
                    sent++;
                }
            }
            catch (Exception ex)
            {
                QueueUnityLog("WARNING", $"Control websocket send failed: {ex.Message}", stackTrace: ex.ToString());
                TryCloseSocket(_controlSocket);
            }
            finally
            {
                _controlSending = false;
            }
        }

        private async Task FlushLatestRgbAsync()
        {
            if (_rgbSocket == null || _rgbSocket.State != WebSocketState.Open)
            {
                return;
            }

            byte[] packet = _latestRgbPacket;
            if (packet == null)
            {
                return;
            }

            _latestRgbPacket = null;
            _rgbSending = true;
            try
            {
                await _rgbSocket.SendAsync(new ArraySegment<byte>(packet), WebSocketMessageType.Binary, true, _cts.Token);
            }
            catch (Exception ex)
            {
                QueueUnityLog("WARNING", $"RGB websocket send failed: {ex.Message}", stackTrace: ex.ToString());
                TryCloseSocket(_rgbSocket);
            }
            finally
            {
                _rgbSending = false;
            }
        }

        private async Task FlushLatestDepthAsync()
        {
            if (_depthSocket == null || _depthSocket.State != WebSocketState.Open)
            {
                return;
            }

            byte[] packet = _latestDepthPacket;
            if (packet == null)
            {
                return;
            }

            _latestDepthPacket = null;
            _depthSending = true;
            try
            {
                await _depthSocket.SendAsync(new ArraySegment<byte>(packet), WebSocketMessageType.Binary, true, _cts.Token);
            }
            catch (Exception ex)
            {
                QueueUnityLog("WARNING", $"Depth websocket send failed: {ex.Message}", stackTrace: ex.ToString());
                TryCloseSocket(_depthSocket);
            }
            finally
            {
                _depthSending = false;
            }
        }

        private async Task ReceiveControlLoopAsync()
        {
            if (_controlSocket == null)
            {
                return;
            }

            byte[] buffer = new byte[512];
            try
            {
                while (!_isQuitting && _controlSocket.State == WebSocketState.Open)
                {
                    var result = await _controlSocket.ReceiveAsync(new ArraySegment<byte>(buffer), _cts.Token);
                    if (result.MessageType == WebSocketMessageType.Close)
                    {
                        QueueUnityLog("WARNING", "Control websocket closed by server");
                        break;
                    }

                    if (result.MessageType == WebSocketMessageType.Text)
                    {
                        string message = Encoding.UTF8.GetString(buffer, 0, result.Count);
                        try
                        {
                            ControlMessageReceived?.Invoke(message);
                        }
                        catch (Exception ex)
                        {
                            QueueUnityLog("WARNING", $"Control message handler failed: {ex.Message}");
                        }
                    }
                }
            }
            catch (Exception ex)
            {
                if (!_isQuitting)
                {
                    QueueUnityLog("WARNING", $"Control websocket receive loop ended: {ex.Message}", stackTrace: ex.ToString());
                }
            }

            TryCloseSocket(_controlSocket);
        }

        private async Task ReceiveVisionLoopAsync()
        {
            if (_visionSocket == null)
            {
                return;
            }

            byte[] buffer = new byte[64 * 1024];
            try
            {
                while (!_isQuitting && _visionSocket.State == WebSocketState.Open)
                {
                    var result = await _visionSocket.ReceiveAsync(new ArraySegment<byte>(buffer), _cts.Token);
                    if (result.MessageType == WebSocketMessageType.Close)
                    {
                        QueueUnityLog("WARNING", "Vision websocket closed by server");
                        break;
                    }

                    if (result.MessageType == WebSocketMessageType.Text)
                    {
                        string message = Encoding.UTF8.GetString(buffer, 0, result.Count);
                        try
                        {
                            VisionMessageReceived?.Invoke(message);
                        }
                        catch (Exception ex)
                        {
                            QueueUnityLog("WARNING", $"Vision message handler failed: {ex.Message}");
                        }
                    }
                }
            }
            catch (Exception ex)
            {
                if (!_isQuitting)
                {
                    QueueUnityLog("WARNING", $"Vision websocket receive loop ended: {ex.Message}", stackTrace: ex.ToString());
                }
            }

            TryCloseSocket(_visionSocket);
        }

        private static void TryCloseSocket(ClientWebSocket socket)
        {
            if (socket == null)
            {
                return;
            }

            try
            {
                socket.Dispose();
            }
            catch (Exception ex)
            {
                Debug.LogWarning($"TryCloseSocket failed: {ex.Message}");
            }
        }

        public void QueueUnityLog(
            string level,
            string message,
            string script = null,
            int line = -1,
            string stackTrace = null)
        {
            var payload = new BackendClientLogPayload
            {
                type = "client_log",
                source = "unity",
                level = level,
                message = message,
                script = script,
                line = line,
                stack_trace = stackTrace,
                timestamp_ms = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                device_id = SystemInfo.deviceUniqueIdentifier,
            };

            QueueControlJson(JsonUtility.ToJson(payload));
        }

        private async void OnApplicationQuit()
        {
            _isQuitting = true;
            _cts?.Cancel();

            if (_controlSocket != null)
            {
                try
                {
                    await _controlSocket.CloseAsync(WebSocketCloseStatus.NormalClosure, "Quit", CancellationToken.None);
                }
                catch (Exception ex)
                {
                    Debug.LogWarning($"OnApplicationQuit close control socket failed: {ex.Message}");
                }

                _controlSocket.Dispose();
            }

            if (_rgbSocket != null)
            {
                try
                {
                    await _rgbSocket.CloseAsync(WebSocketCloseStatus.NormalClosure, "Quit", CancellationToken.None);
                }
                catch (Exception ex)
                {
                    Debug.LogWarning($"OnApplicationQuit close rgb socket failed: {ex.Message}");
                }

                _rgbSocket.Dispose();
            }

            if (_depthSocket != null)
            {
                try
                {
                    await _depthSocket.CloseAsync(WebSocketCloseStatus.NormalClosure, "Quit", CancellationToken.None);
                }
                catch (Exception ex)
                {
                    Debug.LogWarning($"OnApplicationQuit close depth socket failed: {ex.Message}");
                }

                _depthSocket.Dispose();
            }

            if (_visionSocket != null)
            {
                try
                {
                    await _visionSocket.CloseAsync(WebSocketCloseStatus.NormalClosure, "Quit", CancellationToken.None);
                }
                catch (Exception ex)
                {
                    Debug.LogWarning($"OnApplicationQuit close vision socket failed: {ex.Message}");
                }

                _visionSocket.Dispose();
            }

            _cts?.Dispose();
        }
    }

    [Serializable]
    public class BackendClientLogPayload
    {
        public string type;
        public string source;
        public string level;
        public string message;
        public string script;
        public int line;
        public string stack_trace;
        public long timestamp_ms;
        public string device_id;
    }
}
