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
        [Header("Transport")]
        [SerializeField] private StreamTransportSwitcher transportSwitcher;
        [SerializeField] private string controlPath = "/ws/heartbeat";
        [SerializeField] private string rgbPath = "/ws/rgb";
        [SerializeField] private float reconnectDelaySeconds = 2f;

        private ClientWebSocket _controlSocket;
        private ClientWebSocket _rgbSocket;
        private CancellationTokenSource _cts;

        private readonly ConcurrentQueue<string> _controlQueue = new ConcurrentQueue<string>();
        private byte[] _latestRgbPacket;

        private bool _controlConnecting;
        private bool _rgbConnecting;
        private bool _controlSending;
        private bool _rgbSending;
        private bool _isQuitting;
        private float _nextControlReconnectAt;
        private float _nextRgbReconnectAt;

        public bool IsControlConnected => _controlSocket != null && _controlSocket.State == WebSocketState.Open;
        public bool IsRgbConnected => _rgbSocket != null && _rgbSocket.State == WebSocketState.Open;

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

            if (IsControlConnected && !_controlSending)
            {
                _ = FlushControlQueueAsync();
            }

            if (IsRgbConnected && !_rgbSending && _latestRgbPacket != null)
            {
                _ = FlushLatestRgbAsync();
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
                }
            }
            catch
            {
                if (!_isQuitting)
                {
                    QueueUnityLog("WARNING", "Control websocket receive loop ended");
                }
            }

            TryCloseSocket(_controlSocket);
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
            catch
            {
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
                catch
                {
                }

                _controlSocket.Dispose();
            }

            if (_rgbSocket != null)
            {
                try
                {
                    await _rgbSocket.CloseAsync(WebSocketCloseStatus.NormalClosure, "Quit", CancellationToken.None);
                }
                catch
                {
                }

                _rgbSocket.Dispose();
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
