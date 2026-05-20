using System;
using System.IO;
using Meta.XR.EnvironmentDepth;
using Unity.Collections;
using UnityEngine;
using UnityEngine.Rendering;

namespace SmartRoom.Networking
{
    public class DepthStreamModule : MonoBehaviour
    {
        [SerializeField] private BackendCommunicationManager manager;
        [SerializeField] private EnvironmentDepthManager environmentDepthManager;
        [SerializeField] private Shader depthArraySliceShader;
        [SerializeField] private int targetFrameRateHz = 10;
        [SerializeField] private int maxPixelsPerFrame = 0;
        [SerializeField] private bool usePreprocessedDepthTexture = false;
        [SerializeField] private bool flipVertical = true;
        [SerializeField] private bool syncTimestampWithLatestRgb = true;

        private float _nextAt;
        private float _nextWarnAt;
        private int _frameId;
        private int _sentCount;
        private int _lastSentWidth;
        private int _lastSentHeight;
        private int _lastStride = -1;
        private long _lastSyncedRgbTimestampMs;
        private Material _depthSliceMaterial;
        private RenderTexture _depthRt;
        private Texture2D _depthReadbackTexture;
        private string _lastStatusMessage = "Not started";
        private float[] _latestDepthMeters;
        private int _latestDepthWidth;
        private int _latestDepthHeight;

        private const string RawDepthGlobal = "_EnvironmentDepthTexture";
        private const string PreprocessedDepthGlobal = "_PreprocessedEnvironmentDepthTexture";
        private const string ScenePermission = "com.oculus.permission.USE_SCENE";
        private const string RuntimeShaderName = "Hidden/SmartRoom/DepthArraySliceToFloat";
        private const string ResourceShaderName = "Hidden/SmartRoom/DepthArraySliceToFloat_Resource";
        private const string ResourceShaderAssetPath = "SmartRoomDepthArraySliceToFloat";

        private void Awake()
        {
            if (manager == null)
            {
                manager = FindFirstObjectByType<BackendCommunicationManager>();
            }

            if (environmentDepthManager == null)
            {
                environmentDepthManager = FindFirstObjectByType<EnvironmentDepthManager>();
            }

            if (depthArraySliceShader == null)
            {
                depthArraySliceShader = Shader.Find(RuntimeShaderName);
            }

            if (depthArraySliceShader == null)
            {
                depthArraySliceShader = Shader.Find(ResourceShaderName);
            }

            if (depthArraySliceShader == null)
            {
                depthArraySliceShader = Resources.Load<Shader>(ResourceShaderAssetPath);
            }

            if (depthArraySliceShader != null)
            {
                _depthSliceMaterial = new Material(depthArraySliceShader);
            }
            else
            {
                _lastStatusMessage = "Depth shader not found. Assign depthArraySliceShader in inspector or keep Resources/SmartRoomDepthArraySliceToFloat.shader in build.";
            }
        }

        private void Start()
        {
            _nextAt = Time.time + ResolveInterval();
            _nextWarnAt = Time.time + 2f;
            manager?.QueueUnityLog(
                "INFO",
                $"DepthStreamModule started. manager={(manager != null ? "ok" : "null")}, envDepthManager={(environmentDepthManager != null ? "ok" : "null")}, shader={(depthArraySliceShader != null ? "ok" : "null")}, target_fps={Mathf.Clamp(targetFrameRateHz, 1, 60)}, interval={ResolveInterval():F3}s"
            );
        }

        public bool TryRaycastViewport(
            float u,
            float v,
            Ray ray,
            Transform referenceTransform,
            out float depthMeters,
            out Vector3 worldPoint,
            out Vector3 cameraPoint)
        {
            depthMeters = -1f;
            worldPoint = Vector3.zero;
            cameraPoint = Vector3.zero;

            if (_latestDepthMeters == null || _latestDepthWidth <= 0 || _latestDepthHeight <= 0)
            {
                return false;
            }

            u = Mathf.Clamp01(u);
            v = Mathf.Clamp01(v);

            int x = Mathf.Clamp((int)(u * _latestDepthWidth), 0, _latestDepthWidth - 1);
            int y = Mathf.Clamp((int)(v * _latestDepthHeight), 0, _latestDepthHeight - 1);

            int idx = y * _latestDepthWidth + x;
            if (idx < 0 || idx >= _latestDepthMeters.Length)
            {
                return false;
            }

            float z = _latestDepthMeters[idx];
            if (!float.IsFinite(z) || z <= 0f)
            {
                return false;
            }

            Vector3 world = ray.origin + ray.direction.normalized * z;
            Vector3 cam = referenceTransform != null
                ? referenceTransform.InverseTransformPoint(world)
                : ray.direction.normalized * z;

            depthMeters = z;
            worldPoint = world;
            cameraPoint = cam;
            return true;
        }

        private void Update()
        {
            if (manager == null)
            {
                return;
            }

            if (!manager.IsDepthConnected)
            {
                return;
            }

            if (Time.time < _nextAt)
            {
                return;
            }

            _nextAt = Time.time + ResolveInterval();

            if (!TryBuildDepthPacket(out byte[] packet, out int outWidth, out int outHeight))
            {
                if (Time.time >= _nextWarnAt)
                {
                    _nextWarnAt = Time.time + 2f;
                    manager.QueueUnityLog("WARNING", $"Depth skipped: {_lastStatusMessage}");
                }
                return;
            }

            manager.QueueDepthPacket(packet);
            _sentCount++;
            if (_sentCount % 20 == 0)
            {
                manager.QueueUnityLog(
                    "INFO",
                    $"Depth sent count={_sentCount}, frame_id={_frameId}, size={outWidth}x{outHeight}"
                );
            }
        }

        private bool TryBuildDepthPacket(out byte[] packet, out int outWidth, out int outHeight)
        {
            packet = null;
            outWidth = 0;
            outHeight = 0;

            if (environmentDepthManager == null)
            {
                _lastStatusMessage = "EnvironmentDepthManager missing in scene";
                return false;
            }

            if (!EnvironmentDepthManager.IsSupported)
            {
                _lastStatusMessage = "EnvironmentDepthManager.IsSupported == false (device/plugin unsupported)";
                return false;
            }

            if (!HasScenePermission())
            {
                _lastStatusMessage = "Missing USE_SCENE permission. Enable OVRManager scene permission or request at runtime.";
                return false;
            }

            if (!environmentDepthManager.enabled)
            {
                environmentDepthManager.enabled = true;
                _lastStatusMessage = "EnvironmentDepthManager was disabled and has been enabled";
                return false;
            }

            if (!environmentDepthManager.IsDepthAvailable)
            {
                _lastStatusMessage = "Depth not available yet (check passthrough, PST fixes, and scene permission)";
                return false;
            }

            if (_depthSliceMaterial == null)
            {
                _lastStatusMessage = "Depth slice material unavailable (shader missing)";
                return false;
            }

            string globalTextureName = usePreprocessedDepthTexture ? PreprocessedDepthGlobal : RawDepthGlobal;
            Texture sourceDepth = Shader.GetGlobalTexture(globalTextureName);
            if (sourceDepth == null)
            {
                _lastStatusMessage = $"Global depth texture not found: {globalTextureName}";
                return false;
            }

            if (sourceDepth.width <= 0 || sourceDepth.height <= 0)
            {
                _lastStatusMessage = $"Depth texture invalid size: {sourceDepth.width}x{sourceDepth.height}";
                return false;
            }

            if (sourceDepth.dimension != TextureDimension.Tex2DArray)
            {
                _lastStatusMessage = $"Depth texture dimension unsupported: {sourceDepth.dimension} (expected Tex2DArray)";
                return false;
            }

            if (!SystemInfo.SupportsRenderTextureFormat(RenderTextureFormat.RFloat))
            {
                _lastStatusMessage = "RenderTextureFormat.RFloat unsupported on this device";
                return false;
            }

            EnsureDepthBuffers(sourceDepth.width, sourceDepth.height);

            _depthSliceMaterial.SetTexture("_SourceDepthArray", sourceDepth);
            _depthSliceMaterial.SetFloat("_ArraySlice", 0f);
            Graphics.Blit(null, _depthRt, _depthSliceMaterial);

            RenderTexture previous = RenderTexture.active;
            RenderTexture.active = _depthRt;
            try
            {
                _depthReadbackTexture.ReadPixels(new Rect(0, 0, _depthRt.width, _depthRt.height), 0, 0, false);
                _depthReadbackTexture.Apply(false, false);
            }
            finally
            {
                RenderTexture.active = previous;
            }

            NativeArray<float> floatData = _depthReadbackTexture.GetRawTextureData<float>();
            if (!floatData.IsCreated || floatData.Length == 0)
            {
                _lastStatusMessage = "Depth readback returned empty buffer";
                return false;
            }

            int sourceWidth = _depthRt.width;
            int sourceHeight = _depthRt.height;
            int totalPixels = sourceWidth * sourceHeight;
            int stride = 1;
            if (maxPixelsPerFrame > 0 && totalPixels > maxPixelsPerFrame)
            {
                stride = Mathf.CeilToInt(Mathf.Sqrt((float)totalPixels / maxPixelsPerFrame));
            }

            int sampleWidth = Mathf.Max(1, sourceWidth / stride);
            int sampleHeight = Mathf.Max(1, sourceHeight / stride);
            float[] meters = new float[sampleWidth * sampleHeight];

            int idx = 0;
            for (int y = 0; y < sampleHeight; y++)
            {
                int srcY = Mathf.Min(sourceHeight - 1, y * stride);
                if (flipVertical)
                {
                    srcY = (sourceHeight - 1) - srcY;
                }

                int rowBase = srcY * sourceWidth;
                for (int x = 0; x < sampleWidth; x++)
                {
                    int srcX = Mathf.Min(sourceWidth - 1, x * stride);
                    int srcIndex = rowBase + srcX;
                    float depthM = floatData[srcIndex];

                    if (float.IsNaN(depthM) || float.IsInfinity(depthM) || depthM < 0f)
                    {
                        depthM = 0f;
                    }

                    meters[idx++] = depthM;
                }
            }

            _frameId++;
            outWidth = sampleWidth;
            outHeight = sampleHeight;

            _latestDepthWidth = sampleWidth;
            _latestDepthHeight = sampleHeight;
            _latestDepthMeters = meters;

            long timestampMs;
            if (syncTimestampWithLatestRgb)
            {
                long rgbTs = manager != null ? manager.LatestRgbTimestampMs : 0;
                if (rgbTs <= 0)
                {
                    _lastStatusMessage = "Waiting for RGB timestamp to sync depth frame";
                    return false;
                }

                if (rgbTs == _lastSyncedRgbTimestampMs)
                {
                    _lastStatusMessage = "Waiting for next RGB frame timestamp for 1:1 sync";
                    return false;
                }

                _lastSyncedRgbTimestampMs = rgbTs;
                timestampMs = rgbTs;
            }
            else
            {
                timestampMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            }

            if (_lastSentWidth != sampleWidth || _lastSentHeight != sampleHeight || _lastStride != stride)
            {
                _lastSentWidth = sampleWidth;
                _lastSentHeight = sampleHeight;
                _lastStride = stride;
                manager?.QueueUnityLog(
                    "INFO",
                    $"Depth sampling config: source={sourceWidth}x{sourceHeight}, stride={stride}, output={sampleWidth}x{sampleHeight}, maxPixelsPerFrame={(maxPixelsPerFrame > 0 ? maxPixelsPerFrame.ToString() : "native")}, interval={ResolveInterval():F3}s, ts_sync={(syncTimestampWithLatestRgb ? "rgb" : "local")}" 
                );
            }

            packet = BuildPacket(_frameId, timestampMs, sampleWidth, sampleHeight, meters);
            _lastStatusMessage = "OK";
            return true;
        }

        private float ResolveInterval()
        {
            int fps = Mathf.Clamp(targetFrameRateHz, 1, 60);
            return Mathf.Clamp(1f / fps, 1f / 120f, 1f);
        }

        private void EnsureDepthBuffers(int width, int height)
        {
            if (_depthRt == null || _depthRt.width != width || _depthRt.height != height)
            {
                if (_depthRt != null)
                {
                    _depthRt.Release();
                    Destroy(_depthRt);
                }

                _depthRt = new RenderTexture(width, height, 0, RenderTextureFormat.RFloat)
                {
                    useMipMap = false,
                    autoGenerateMips = false,
                };
                _depthRt.Create();
            }

            if (_depthReadbackTexture == null || _depthReadbackTexture.width != width || _depthReadbackTexture.height != height)
            {
                _depthReadbackTexture = new Texture2D(width, height, TextureFormat.RFloat, false);
            }
        }

        private static bool HasScenePermission()
        {
#if UNITY_ANDROID && !UNITY_EDITOR
            return UnityEngine.Android.Permission.HasUserAuthorizedPermission(ScenePermission);
#else
            return true;
#endif
        }

        private static byte[] BuildPacket(int frameId, long timestampMs, int width, int height, float[] depthMeters)
        {
            int payloadLen = depthMeters.Length * sizeof(float);
            using var ms = new MemoryStream(36 + payloadLen);
            using var bw = new BinaryWriter(ms);

            bw.Write((byte)'D');
            bw.Write((byte)'E');
            bw.Write((byte)'P');
            bw.Write((byte)'1');
            bw.Write(frameId);
            bw.Write(timestampMs);
            bw.Write(width);
            bw.Write(height);
            bw.Write(width * sizeof(float));
            bw.Write(sizeof(float));
            bw.Write(payloadLen);

            byte[] payload = new byte[payloadLen];
            Buffer.BlockCopy(depthMeters, 0, payload, 0, payloadLen);
            bw.Write(payload);
            bw.Flush();
            return ms.ToArray();
        }

        private void OnDestroy()
        {
            if (_depthRt != null)
            {
                _depthRt.Release();
                Destroy(_depthRt);
            }

            if (_depthReadbackTexture != null)
            {
                Destroy(_depthReadbackTexture);
            }

            if (_depthSliceMaterial != null)
            {
                Destroy(_depthSliceMaterial);
            }
        }
    }
}
