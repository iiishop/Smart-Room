using System;
using System.IO;
using Meta.XR;
using UnityEngine;

namespace SmartRoom.Networking
{
    public class RgbStreamModule : MonoBehaviour
    {
        [SerializeField] private BackendCommunicationManager manager;
        [SerializeField] private PassthroughCameraAccess passthroughCameraAccess;
        [SerializeField] private int outputWidth = 640;
        [SerializeField] private int outputHeight = 360;
        public int LatestFrameWidth => outputWidth;
        public int LatestFrameHeight => outputHeight;
        [SerializeField] private int jpegQuality = 65;
        [SerializeField] private int targetFrameRateHz = 60;
        [SerializeField] private bool clampToPcaMaxFramerate = true;

        private float _nextAt;
        private float _nextWarnAt;
        private int _frameId;
        private int _sentCount;
        private RenderTexture _rt;
        private Texture2D _readbackTexture;
        private string _lastStatusMessage = "Not started";

        private void Awake()
        {
            if (manager == null)
            {
                manager = FindFirstObjectByType<BackendCommunicationManager>();
            }

            if (passthroughCameraAccess == null)
            {
                passthroughCameraAccess = FindFirstObjectByType<PassthroughCameraAccess>();
            }

            jpegQuality = Mathf.Clamp(jpegQuality, 1, 100);
            outputWidth = Mathf.Max(16, outputWidth);
            outputHeight = Mathf.Max(16, outputHeight);
            targetFrameRateHz = Mathf.Clamp(targetFrameRateHz, 1, 60);
        }

        private void Start()
        {
            _nextAt = Time.time + ResolveInterval();
            _nextWarnAt = Time.time + 2f;
            int pcaMax = passthroughCameraAccess != null ? Mathf.Max(1, passthroughCameraAccess.MaxFramerate) : -1;
            manager?.QueueUnityLog("INFO", $"RgbStreamModule started. requested_fps={Mathf.Clamp(targetFrameRateHz,1,60)}, effective_fps={ResolveTargetFps()}, pca_max={(pcaMax > 0 ? pcaMax.ToString() : "unknown")}, interval={ResolveInterval():F3}s source={(passthroughCameraAccess != null ? "found" : "null")}");
        }

        private void Update()
        {
            if (manager == null || passthroughCameraAccess == null)
            {
                return;
            }

            if (!manager.IsRgbConnected)
            {
                return;
            }

            if (Time.time < _nextAt)
            {
                return;
            }

            _nextAt = Time.time + ResolveInterval();

            if (!TryGetJpegFrame(out var jpegBytes, out int width, out int height, out long timestampMs))
            {
                if (Time.time >= _nextWarnAt)
                {
                    _nextWarnAt = Time.time + 2f;
                    manager.QueueUnityLog("WARNING", $"RGB skipped: {_lastStatusMessage}");
                }
                return;
            }

            _frameId++;
            manager.PublishLatestRgbTimestamp(timestampMs);
            manager.QueueRgbPacket(BuildPacket(_frameId, timestampMs, width, height, jpegBytes));
            _sentCount++;

            if (_sentCount % 20 == 0)
            {
                manager.QueueUnityLog("INFO", $"RGB sent count={_sentCount}, frame_id={_frameId}, size={width}x{height}");
            }
        }

        private float ResolveInterval()
        {
            int fps = ResolveTargetFps();
            return Mathf.Clamp(1f / fps, 1f / 120f, 1f);
        }

        private int ResolveTargetFps()
        {
            int fps = Mathf.Clamp(targetFrameRateHz, 1, 60);
            if (!clampToPcaMaxFramerate || passthroughCameraAccess == null)
            {
                return fps;
            }

            int pcaMax = Mathf.Clamp(passthroughCameraAccess.MaxFramerate, 1, 120);
            return Mathf.Min(fps, pcaMax);
        }

        private bool TryGetJpegFrame(out byte[] jpegBytes, out int width, out int height, out long timestampMs)
        {
            jpegBytes = null;
            width = 0;
            height = 0;
            timestampMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();

            if (passthroughCameraAccess == null || !passthroughCameraAccess.enabled || !passthroughCameraAccess.IsPlaying)
            {
                _lastStatusMessage = "PassthroughCameraAccess not playing or missing";
                return false;
            }

            Texture source = passthroughCameraAccess.GetTexture();
            if (source == null)
            {
                _lastStatusMessage = "PCA texture is null";
                return false;
            }

            EnsureBuffers();

            Graphics.Blit(source, _rt);
            RenderTexture previous = RenderTexture.active;
            RenderTexture.active = _rt;
            try
            {
                _readbackTexture.ReadPixels(new Rect(0, 0, outputWidth, outputHeight), 0, 0, false);
                _readbackTexture.Apply(false, false);
                jpegBytes = _readbackTexture.EncodeToJPG(jpegQuality);
                width = outputWidth;
                height = outputHeight;

                if (passthroughCameraAccess.Timestamp == default)
                {
                    timestampMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
                }
                else
                {
                    DateTime tsUtc = DateTime.SpecifyKind(passthroughCameraAccess.Timestamp, DateTimeKind.Utc);
                    timestampMs = new DateTimeOffset(tsUtc).ToUnixTimeMilliseconds();
                }

                bool ok = jpegBytes != null && jpegBytes.Length > 0;
                _lastStatusMessage = ok ? "OK" : "EncodeToJPG returned empty data";
                return ok;
            }
            finally
            {
                RenderTexture.active = previous;
            }
        }

        private void EnsureBuffers()
        {
            if (_rt == null || _rt.width != outputWidth || _rt.height != outputHeight)
            {
                if (_rt != null)
                {
                    _rt.Release();
                    Destroy(_rt);
                }

                _rt = new RenderTexture(outputWidth, outputHeight, 0, RenderTextureFormat.ARGB32)
                {
                    useMipMap = false,
                    autoGenerateMips = false,
                };
                _rt.Create();
            }

            if (_readbackTexture == null || _readbackTexture.width != outputWidth || _readbackTexture.height != outputHeight)
            {
                _readbackTexture = new Texture2D(outputWidth, outputHeight, TextureFormat.RGB24, false);
            }
        }

        private static byte[] BuildPacket(int frameId, long timestampMs, int width, int height, byte[] jpegBytes)
        {
            using var ms = new MemoryStream(28 + jpegBytes.Length);
            using var bw = new BinaryWriter(ms);
            bw.Write((byte)'R');
            bw.Write((byte)'G');
            bw.Write((byte)'B');
            bw.Write((byte)'1');
            bw.Write(frameId);
            bw.Write(timestampMs);
            bw.Write(width);
            bw.Write(height);
            bw.Write(jpegBytes.Length);
            bw.Write(jpegBytes);
            bw.Flush();
            return ms.ToArray();
        }

        private void OnDestroy()
        {
            if (_rt != null)
            {
                _rt.Release();
                Destroy(_rt);
            }

            if (_readbackTexture != null)
            {
                Destroy(_readbackTexture);
            }
        }
    }
}
