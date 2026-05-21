using System;
using System.Collections.Concurrent;
using System.IO;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using UnityEngine;

namespace SmartRoom.Networking
{
    public class VisionReceiverModule : MonoBehaviour
    {
        public event Action<VisionFrameResultData> VisionFrameReceived;

        [Header("Dependencies")]
        [SerializeField] private BackendCommunicationManager manager;
        [SerializeField] private StreamTransportSwitcher transportSwitcher;

        [Header("Connection")]
        [SerializeField] private string visionPath = "/ws/vision";
        [SerializeField] private float reconnectDelaySeconds = 2f;

        private readonly ConcurrentQueue<VisionFrameResultData> _receivedFrames = new ConcurrentQueue<VisionFrameResultData>();
        private readonly ConcurrentQueue<PendingLog> _pendingLogs = new ConcurrentQueue<PendingLog>();

        private ClientWebSocket _visionSocket;
        private CancellationTokenSource _cts;
        private bool _connecting;
        private bool _isQuitting;
        private float _nextReconnectAt;

        public bool IsVisionConnected => _visionSocket != null && _visionSocket.State == WebSocketState.Open;
        public VisionFrameResultData LatestFrame { get; private set; }

        private void Awake()
        {
            if (manager == null)
            {
                manager = FindFirstObjectByType<BackendCommunicationManager>();
            }

            if (transportSwitcher == null)
            {
                transportSwitcher = FindFirstObjectByType<StreamTransportSwitcher>();
            }
        }

        private void Start()
        {
            _cts = new CancellationTokenSource();
            _nextReconnectAt = Time.time;
        }

        private void Update()
        {
            FlushPendingLogs();
            FlushReceivedFrames();

            if (_isQuitting || IsVisionConnected || Time.time < _nextReconnectAt)
            {
                return;
            }

            _nextReconnectAt = Time.time + reconnectDelaySeconds;
            _ = EnsureVisionConnectedAsync();
        }

        private async Task EnsureVisionConnectedAsync()
        {
            if (_connecting || _isQuitting)
            {
                return;
            }

            if (transportSwitcher == null)
            {
                EnqueueLog("WARNING", "VisionReceiverModule missing StreamTransportSwitcher.");
                return;
            }

            _connecting = true;
            try
            {
                transportSwitcher.RefreshActiveTransport();
                string url = transportSwitcher.BuildWebSocketUrl(visionPath);

                DisposeSocket(_visionSocket);
                _visionSocket = new ClientWebSocket();
                await _visionSocket.ConnectAsync(new Uri(url), _cts.Token);

                EnqueueLog("INFO", $"Vision websocket connected: {url}");
                _ = ReceiveVisionLoopAsync(_visionSocket);
            }
            catch (Exception ex)
            {
                EnqueueLog("WARNING", $"Vision websocket connect retry: {ex.Message}", ex.ToString());
            }
            finally
            {
                _connecting = false;
            }
        }

        private async Task ReceiveVisionLoopAsync(ClientWebSocket socket)
        {
            byte[] buffer = new byte[4096];
            try
            {
                while (!_isQuitting && socket != null && socket.State == WebSocketState.Open)
                {
                    using var stream = new MemoryStream();
                    WebSocketReceiveResult result;
                    do
                    {
                        result = await socket.ReceiveAsync(new ArraySegment<byte>(buffer), _cts.Token);
                        if (result.MessageType == WebSocketMessageType.Close)
                        {
                            EnqueueLog("WARNING", "Vision websocket closed by server.");
                            TryCloseSocket(socket);
                            return;
                        }

                        if (result.Count > 0)
                        {
                            stream.Write(buffer, 0, result.Count);
                        }
                    }
                    while (!result.EndOfMessage);

                    if (result.MessageType != WebSocketMessageType.Text || stream.Length == 0)
                    {
                        continue;
                    }

                    string json = Encoding.UTF8.GetString(stream.GetBuffer(), 0, (int)stream.Length);
                    VisionFrameResultData frame = VisionMessageParser.ParseFrameResult(json);
                    _receivedFrames.Enqueue(frame);
                }
            }
            catch (OperationCanceledException)
            {
            }
            catch (Exception ex)
            {
                if (!_isQuitting)
                {
                    EnqueueLog("WARNING", $"Vision websocket receive failed: {ex.Message}", ex.ToString());
                }
            }
            finally
            {
                if (!_isQuitting)
                {
                    DisposeSocket(socket);
                }
            }
        }

        private void FlushReceivedFrames()
        {
            while (_receivedFrames.TryDequeue(out VisionFrameResultData frame))
            {
                LatestFrame = frame;
                LogFrame(frame);

                try
                {
                    VisionFrameReceived?.Invoke(frame);
                }
                catch (Exception ex)
                {
                    EnqueueLog("WARNING", $"VisionFrameReceived handler failed: {ex.Message}", ex.ToString());
                }
            }
        }

        private void LogFrame(VisionFrameResultData frame)
        {
            foreach (VisionObjectData item in frame.Objects)
            {
                EnqueueLog(
                    "INFO",
                    $"Vision object_id={item.ObjectId} label={item.Label} mask={item.DecodedMask.Width}x{item.DecodedMask.Height} area={item.Area}");
            }
        }

        private void FlushPendingLogs()
        {
            while (_pendingLogs.TryDequeue(out PendingLog entry))
            {
                if (manager != null)
                {
                    manager.QueueUnityLog(entry.Level, entry.Message, stackTrace: entry.StackTrace);
                    continue;
                }

                if (entry.Level == "WARNING")
                {
                    Debug.LogWarning(entry.Message, this);
                    continue;
                }

                if (entry.Level == "ERROR")
                {
                    Debug.LogError(entry.Message, this);
                    continue;
                }

                Debug.Log(entry.Message, this);
            }
        }

        private void EnqueueLog(string level, string message, string stackTrace = null)
        {
            _pendingLogs.Enqueue(new PendingLog(level, message, stackTrace));
        }

        private void DisposeSocket(ClientWebSocket socket)
        {
            if (socket == null)
            {
                return;
            }

            TryCloseSocket(socket);
            if (VisionSocketOwnership.ShouldClearCurrentSocket(_visionSocket, socket))
            {
                _visionSocket = null;
            }
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

        private async void OnApplicationQuit()
        {
            await ShutdownAsync();
        }

        private async void OnDestroy()
        {
            await ShutdownAsync();
        }

        private async Task ShutdownAsync()
        {
            if (_isQuitting)
            {
                return;
            }

            _isQuitting = true;
            _cts?.Cancel();

            if (_visionSocket != null)
            {
                try
                {
                    await _visionSocket.CloseAsync(WebSocketCloseStatus.NormalClosure, "Quit", CancellationToken.None);
                }
                catch
                {
                }

                _visionSocket.Dispose();
                _visionSocket = null;
            }

            _cts?.Dispose();
            _cts = null;
        }

        private readonly struct PendingLog
        {
            public string Level { get; }
            public string Message { get; }
            public string StackTrace { get; }

            public PendingLog(string level, string message, string stackTrace)
            {
                Level = level ?? "INFO";
                Message = message ?? string.Empty;
                StackTrace = stackTrace;
            }
        }
    }
}
