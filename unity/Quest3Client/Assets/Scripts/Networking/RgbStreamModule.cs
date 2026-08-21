using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using Meta.XR;
using UnityEngine;

namespace SmartRoom.Networking
{
    public class RgbStreamModule : MonoBehaviour
    {
        [Serializable]
        private sealed class CameraIntrinsicsPayload
        {
            public string type = "camera_intrinsics";
            public float fx;
            public float fy;
            public float cx;
            public float cy;
            public int sensor_width;
            public int sensor_height;
            public int requested_width;
            public int requested_height;
            public int current_width;
            public int current_height;
            public int stream_width;
            public int stream_height;
            public int preferred_width;
            public int preferred_height;
            public string[] supported_resolutions;
            public long timestamp_ms;
            // RGB camera world pose
            public float pose_position_x, pose_position_y, pose_position_z;
            public float pose_rotation_x, pose_rotation_y, pose_rotation_z, pose_rotation_w;
        }

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
        private bool _loggedFirstFrameMeta;
        private bool _cameraMetadataSent;
        private Vector2Int _preferredResolution;
        private string[] _supportedResolutionStrings = Array.Empty<string>();
        private Vector2Int _lastReportedCurrentResolution;

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
            ProbeAndApplyResolutionStrategy();
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

                if (!_loggedFirstFrameMeta)
                {
                    _loggedFirstFrameMeta = true;
                    var intrinsics = passthroughCameraAccess.Intrinsics;
                    manager?.QueueUnityLog(
                        "INFO",
                        $"RGB capture ready: requested={passthroughCameraAccess.RequestedResolution.x}x{passthroughCameraAccess.RequestedResolution.y}, " +
                        $"current={passthroughCameraAccess.CurrentResolution.x}x{passthroughCameraAccess.CurrentResolution.y}, " +
                        $"stream={width}x{height}, sensor={intrinsics.SensorResolution.x}x{intrinsics.SensorResolution.y}, " +
                        $"focal=({intrinsics.FocalLength.x:F2},{intrinsics.FocalLength.y:F2}), " +
                        $"principal=({intrinsics.PrincipalPoint.x:F2},{intrinsics.PrincipalPoint.y:F2}), ts_ms={timestampMs}"
                    );
                }

                MaybeSendCameraMetadata(timestampMs, width, height);

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

        private void ProbeAndApplyResolutionStrategy()
        {
            if (passthroughCameraAccess == null)
            {
                return;
            }

            var supported = ProbeSupportedResolutions(passthroughCameraAccess);
            if (supported.Count == 0)
            {
                manager?.QueueUnityLog("WARNING", "RGB supported resolution probe returned no results; keeping current PCA request");
                return;
            }

            supported = supported
                .Distinct()
                .OrderByDescending(v => v.x * v.y)
                .ThenByDescending(v => v.y)
                .ThenByDescending(v => v.x)
                .ToList();

            _supportedResolutionStrings = supported.Select(v => $"{v.x}x{v.y}").ToArray();
            _preferredResolution = supported[0];
            bool applied = TrySetRequestedResolution(_preferredResolution);

            manager?.QueueUnityLog(
                "INFO",
                $"RGB supported resolutions (v85 probe): {string.Join(", ", _supportedResolutionStrings)} | preferred={_preferredResolution.x}x{_preferredResolution.y} | apply={(applied ? "ok" : "skipped")}"
            );
        }

        private void MaybeSendCameraMetadata(long timestampMs, int streamWidth, int streamHeight)
        {
            if (manager == null || passthroughCameraAccess == null)
            {
                return;
            }

            Vector2Int current = passthroughCameraAccess.CurrentResolution;
            if (_cameraMetadataSent && current == _lastReportedCurrentResolution)
            {
                return;
            }

            var intrinsics = passthroughCameraAccess.Intrinsics;
            Pose camPose = passthroughCameraAccess.GetCameraPose();
            var payload = new CameraIntrinsicsPayload
            {
                fx = intrinsics.FocalLength.x,
                fy = intrinsics.FocalLength.y,
                cx = intrinsics.PrincipalPoint.x,
                cy = intrinsics.PrincipalPoint.y,
                sensor_width = intrinsics.SensorResolution.x,
                sensor_height = intrinsics.SensorResolution.y,
                requested_width = passthroughCameraAccess.RequestedResolution.x,
                requested_height = passthroughCameraAccess.RequestedResolution.y,
                current_width = current.x,
                current_height = current.y,
                stream_width = streamWidth,
                stream_height = streamHeight,
                preferred_width = _preferredResolution.x,
                preferred_height = _preferredResolution.y,
                supported_resolutions = _supportedResolutionStrings,
                timestamp_ms = timestampMs,
                pose_position_x = camPose.position.x,
                pose_position_y = camPose.position.y,
                pose_position_z = camPose.position.z,
                pose_rotation_x = camPose.rotation.x,
                pose_rotation_y = camPose.rotation.y,
                pose_rotation_z = camPose.rotation.z,
                pose_rotation_w = camPose.rotation.w,
            };

            manager.QueueControlJson(JsonUtility.ToJson(payload));
            _cameraMetadataSent = true;
            _lastReportedCurrentResolution = current;
        }

        private static List<Vector2Int> ProbeSupportedResolutions(PassthroughCameraAccess pca)
        {
            var results = new List<Vector2Int>();
            if (pca == null)
            {
                return results;
            }

            Type type = pca.GetType();
            MethodInfo[] methods = type
                .GetMethods(BindingFlags.Public | BindingFlags.Instance | BindingFlags.Static)
                .Where(m => m.Name == "GetSupportedResolutions")
                .ToArray();

            foreach (var method in methods)
            {
                object target = method.IsStatic ? null : pca;
                ParameterInfo[] parameters = method.GetParameters();

                try
                {
                    if (parameters.Length == 0)
                    {
                        AppendResolutionResults(results, method.Invoke(target, null));
                        continue;
                    }

                    if (parameters.Length == 1 && parameters[0].ParameterType.IsEnum)
                    {
                        foreach (var enumValue in Enum.GetValues(parameters[0].ParameterType))
                        {
                            AppendResolutionResults(results, method.Invoke(target, new[] { enumValue }));
                        }
                    }
                }
                catch
                {
                    // Reflection probe is best-effort only; keep trying other overloads.
                }
            }

            return results;
        }

        private static void AppendResolutionResults(List<Vector2Int> output, object result)
        {
            if (result is not IEnumerable enumerable)
            {
                return;
            }

            foreach (var item in enumerable)
            {
                if (item == null)
                {
                    continue;
                }

                if (item is Vector2Int v2)
                {
                    if (v2.x > 0 && v2.y > 0)
                    {
                        output.Add(v2);
                    }
                    continue;
                }

                Type itemType = item.GetType();
                int x = TryReadInt(itemType.GetProperty("x"), item, itemType.GetField("x"));
                int y = TryReadInt(itemType.GetProperty("y"), item, itemType.GetField("y"));
                if (x <= 0 || y <= 0)
                {
                    x = TryReadInt(itemType.GetProperty("width"), item, itemType.GetField("width"));
                    y = TryReadInt(itemType.GetProperty("height"), item, itemType.GetField("height"));
                }

                if (x > 0 && y > 0)
                {
                    output.Add(new Vector2Int(x, y));
                }
            }
        }

        private static int TryReadInt(PropertyInfo property, object instance, FieldInfo field = null)
        {
            try
            {
                object value = property != null ? property.GetValue(instance) : field?.GetValue(instance);
                return value switch
                {
                    int i => i,
                    _ => 0,
                };
            }
            catch
            {
                return 0;
            }
        }

        private bool TrySetRequestedResolution(Vector2Int resolution)
        {
            if (passthroughCameraAccess == null || resolution.x <= 0 || resolution.y <= 0)
            {
                return false;
            }

            try
            {
                PropertyInfo property = passthroughCameraAccess.GetType().GetProperty("RequestedResolution");
                if (property != null && property.CanWrite && property.PropertyType == typeof(Vector2Int))
                {
                    property.SetValue(passthroughCameraAccess, resolution);
                    return true;
                }
            }
            catch
            {
                // Best-effort only.
            }

            return false;
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
