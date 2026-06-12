using System;
using System.Diagnostics;
using System.IO;
using Meta.XR.EnvironmentDepth;
using Meta.XR;
using Unity.Collections;
using UnityEngine;
using UnityEngine.Rendering;

namespace SmartRoom.Networking
{
    public class DepthStreamModule : MonoBehaviour
    {
        [Serializable]
        public sealed class DebugProjectionMeta
        {
            public int depthWidth;
            public int depthHeight;
            public int rgbWidth;
            public int rgbHeight;
            public int sourceWidth;
            public int sourceHeight;
            public int attemptedPoints;
            public int validPoints;
            public int clippedPoints;
            public int pointsBehindCamera;
            public int collidedPixels;
            public float minPixelX;
            public float maxPixelX;
            public float minPixelY;
            public float maxPixelY;
            public float avgPixelX;
            public float avgPixelY;
            public float minRgbCameraZ;
            public float maxRgbCameraZ;
            public float avgRgbCameraZ;
            public bool usedFlipVertical;
            public bool usedPreprocessedDepthTexture;
            public long capturedAtUnixMs;
            public string depthValueSemantics = "rgb_camera_z_m";
        }

        [Serializable]
        public sealed class AlignedDepthProjectionResult
        {
            public float[] sparseAlignedDepth;
            public byte[] validMask;
            public DebugProjectionMeta debugProjectionMeta;
            public int rgbWidth;
            public int rgbHeight;
        }

        [Serializable]
        private sealed class DepthSourceMetaPayload
        {
            public string type = "depth_source_meta";
            public int source_width;
            public int source_height;
            public int sampled_width;
            public int sampled_height;
            public int stride;
            public bool flip_vertical;
            public bool preprocessed;
            public float[] zbuffer_params;
            public int unity_frame_count;
            public long timestamp_ms;
        }

        [Serializable]
        public sealed class DepthFrameSnapshot
        {
            public float[] depthMeters;
            public int sampledWidth;
            public int sampledHeight;
            public int sourceWidth;
            public int sourceHeight;
            public float[] zBufferParams;
            public float[] reprojectionMatrix;
            public bool usedPreprocessedDepthTexture;
            public bool usedFlipVertical;
            public int unityFrameCount;
            public long capturedAtUnixMs;
            // Per-eye depth frame metadata from Utils.GetEnvironmentalDepthFrameDesc(0)
            public double depthCreateTime;
            public double depthPredictedDisplayTime;
            public bool depthIsValid;
            public float[] depthCreatePose7;
            public float[] fovAngles;     // left, right, top, down (degrees)
            public float nearZ;
            public float farZ;
            public float minDepth;
            public float maxDepth;
        }

        [SerializeField] private BackendCommunicationManager manager;
        [SerializeField] private EnvironmentDepthManager environmentDepthManager;
        [SerializeField] private EnvironmentRaycastManager environmentRaycastManager;
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
        private bool _loggedEnvironmentRaycastFallback;
        private bool _loggedFirstLocalFrame;
        private bool _loggedDepthSourceMeta;
        private bool _loggedReprojectionMeta;
        private int _lastSourceDepthWidth;
        private int _lastSourceDepthHeight;
        private bool _depthSourceMetaSent;
        private int _lastSentSourceWidth = -1;
        private int _lastSentSourceHeight = -1;
        private int _lastSentSampledWidth = -1;
        private int _lastSentSampledHeight = -1;

        // Cached per-frame depth frame desc (populated each successful readback)
        private UnityEngine.XR.Oculus.Utils.EnvironmentalDepthFrameDesc _cachedDepthFrameDesc;
        private bool _cachedDepthFrameDescValid;

        // ReadPixels timing (one-shot per-frame diagnostic, summary every 100 reads)
        private int _readbackCount;
        private double _readbackSumMs;
        private double _readbackMinMs = double.MaxValue;
        private double _readbackMaxMs;

        // Public read-only access
        public float[] LatestDepthMeters => _latestDepthMeters;
        public int LatestDepthWidth => _latestDepthWidth;
        public int LatestDepthHeight => _latestDepthHeight;
        private EnvironmentRaycastManagerProvider _environmentRaycastProvider;

        private const string RawDepthGlobal = "_EnvironmentDepthTexture";
        private const string PreprocessedDepthGlobal = "_PreprocessedEnvironmentDepthTexture";
        private const string ScenePermission = "com.oculus.permission.USE_SCENE";
        private const string RuntimeShaderName = "Hidden/SmartRoom/DepthArraySliceToFloat";
        private const string ResourceShaderName = "Hidden/SmartRoom/DepthArraySliceToFloat_Resource";
        private const string ResourceShaderAssetPath = "SmartRoomDepthArraySliceToFloat";

        private void Awake()
        {
            if (manager == null)
                manager = FindFirstObjectByType<BackendCommunicationManager>();

            if (environmentDepthManager == null)
                environmentDepthManager = FindFirstObjectByType<EnvironmentDepthManager>();

            if (environmentRaycastManager == null)
                environmentRaycastManager = FindFirstObjectByType<EnvironmentRaycastManager>();

            _environmentRaycastProvider = new EnvironmentRaycastManagerProvider(environmentRaycastManager);

            if (depthArraySliceShader == null)
                depthArraySliceShader = Shader.Find(RuntimeShaderName);
            if (depthArraySliceShader == null)
                depthArraySliceShader = Shader.Find(ResourceShaderName);
            if (depthArraySliceShader == null)
                depthArraySliceShader = Resources.Load<Shader>(ResourceShaderAssetPath);

            if (depthArraySliceShader != null)
                _depthSliceMaterial = new Material(depthArraySliceShader);
            else
                _lastStatusMessage = "Depth shader not found. Assign depthArraySliceShader in inspector or keep Resources/SmartRoomDepthArraySliceToFloat.shader in build.";
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
            float u, float v, Ray ray,
            out float depthMeters, out Vector3 worldPoint, out Vector3 cameraPoint)
        {
            bool hit = DepthViewportRaycast.TryResolve(
                _latestDepthMeters, _latestDepthWidth, _latestDepthHeight,
                u, v, ray, _environmentRaycastProvider,
                out bool usedFallback, out depthMeters, out worldPoint, out cameraPoint);

            if (usedFallback && !_loggedEnvironmentRaycastFallback)
            {
                _loggedEnvironmentRaycastFallback = true;
                manager?.QueueUnityLog("WARNING",
                    "EnvironmentRaycastManager unavailable; falling back to manual depth projection.");
            }
            return hit;
        }

        private void Update()
        {
            if (Time.time < _nextAt) return;
            _nextAt = Time.time + ResolveInterval();

            // ── Phase 1: Local depth readback (always, no backend needed) ──
            bool readOk = TryReadbackDepthFrame();
            if (!readOk)
            {
                if (Time.time >= _nextWarnAt)
                {
                    _nextWarnAt = Time.time + 2f;
                    Debug.LogWarning($"[DepthStreamModule] Depth readback failed: {_lastStatusMessage}");
                }
                return;
            }

            // ── Phase 2: Build & send packet to backend (optional) ──
            if (manager == null || !manager.IsDepthConnected)
                return; // local-only mode — _latestDepthMeters already set above

            if (syncTimestampWithLatestRgb)
            {
                long rgbTs = manager.LatestRgbTimestampMs;
                if (rgbTs <= 0) return; // no RGB frame yet, skip this depth packet
                if (rgbTs == _lastSyncedRgbTimestampMs) return; // same RGB frame, skip
                _lastSyncedRgbTimestampMs = rgbTs;
            }

            byte[] packet = BuildPacket(_frameId, DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                _latestDepthWidth, _latestDepthHeight, _latestDepthMeters);
            manager.QueueDepthPacket(packet);
            _sentCount++;

            if (_sentCount % 20 == 0)
                manager.QueueUnityLog("INFO", $"Depth sent count={_sentCount}, frame_id={_frameId}, size={_latestDepthWidth}x{_latestDepthHeight}");

            if (_lastSentWidth != _latestDepthWidth || _lastSentHeight != _latestDepthHeight || _lastStride != _lastStride)
            {
                _lastSentWidth = _latestDepthWidth;
                _lastSentHeight = _latestDepthHeight;
                manager?.QueueUnityLog("INFO",
                    $"Depth sampling config: output={_latestDepthWidth}x{_latestDepthHeight}, interval={ResolveInterval():F3}s, ts_sync={(syncTimestampWithLatestRgb ? "rgb" : "local")}");
            }
        }

        /// <summary>
        /// Phase 1: GPU readback → populate _latestDepthMeters.
        /// Always runs locally — no backend dependency.
        /// </summary>
        private bool TryReadbackDepthFrame()
        {
            if (environmentDepthManager == null)
            {
                _lastStatusMessage = "EnvironmentDepthManager missing in scene";
                return false;
            }
            if (!EnvironmentDepthManager.IsSupported)
            {
                _lastStatusMessage = "EnvironmentDepthManager.IsSupported == false";
                return false;
            }
            if (!HasScenePermission())
            {
                _lastStatusMessage = "Missing USE_SCENE permission";
                return false;
            }
            if (!environmentDepthManager.enabled)
            {
                environmentDepthManager.enabled = true;
                _lastStatusMessage = "EnvironmentDepthManager was disabled, now enabled";
                return false;
            }
            if (!environmentDepthManager.IsDepthAvailable)
            {
                _lastStatusMessage = "Depth not available yet";
                return false;
            }
            if (_depthSliceMaterial == null)
            {
                _lastStatusMessage = "Depth slice material unavailable";
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
                _lastStatusMessage = $"Depth texture dimension unsupported: {sourceDepth.dimension}";
                return false;
            }
            if (!SystemInfo.SupportsRenderTextureFormat(RenderTextureFormat.RFloat))
            {
                _lastStatusMessage = "RenderTextureFormat.RFloat unsupported";
                return false;
            }

            EnsureDepthBuffers(sourceDepth.width, sourceDepth.height);

            _depthSliceMaterial.SetTexture("_SourceDepthArray", sourceDepth);
            _depthSliceMaterial.SetFloat("_ArraySlice", 0f);
            Graphics.Blit(null, _depthRt, _depthSliceMaterial);

            RenderTexture previous = RenderTexture.active;
            RenderTexture.active = _depthRt;
            var sw = Stopwatch.StartNew();
            try
            {
                _depthReadbackTexture.ReadPixels(new Rect(0, 0, _depthRt.width, _depthRt.height), 0, 0, false);
                _depthReadbackTexture.Apply(false, false);
            }
            finally
            {
                RenderTexture.active = previous;
            }
            sw.Stop();
            double ms = sw.Elapsed.TotalMilliseconds;
            _readbackCount++;
            _readbackSumMs += ms;
            if (ms < _readbackMinMs) _readbackMinMs = ms;
            if (ms > _readbackMaxMs) _readbackMaxMs = ms;
            if (_readbackCount % 100 == 0)
            {
                double avg = _readbackSumMs / _readbackCount;
                Debug.Log(
                    $"[DepthStreamModule] ReadPixels summary after {_readbackCount} reads: " +
                    $"avg={avg:F2}ms, min={_readbackMinMs:F2}ms, max={_readbackMaxMs:F2}ms"
                );
            }

            NativeArray<float> floatData = _depthReadbackTexture.GetRawTextureData<float>();
            if (!floatData.IsCreated || floatData.Length == 0)
            {
                _lastStatusMessage = "Depth readback returned empty buffer";
                return false;
            }

            int sourceWidth = _depthRt.width;
            int sourceHeight = _depthRt.height;
            _lastSourceDepthWidth = sourceWidth;
            _lastSourceDepthHeight = sourceHeight;

            // Decode parameters from Meta Depth API shader globals.
            // _EnvironmentDepthTexture stores depth as 0..1 NDC values
            // (not linear meters).  Reference: EnvironmentOcclusion.cginc
            //   linearDepth = (1 / (ndcEncoded*2-1 + zbp.y)) * zbp.x
            var zbp = Shader.GetGlobalVector("_EnvironmentDepthZBufferParams");

            int totalPixels = sourceWidth * sourceHeight;
            int stride = 1;
            if (maxPixelsPerFrame > 0 && totalPixels > maxPixelsPerFrame)
                stride = Mathf.CeilToInt(Mathf.Sqrt((float)totalPixels / maxPixelsPerFrame));

            int sampleWidth = Mathf.Max(1, sourceWidth / stride);
            int sampleHeight = Mathf.Max(1, sourceHeight / stride);
            float[] meters = new float[sampleWidth * sampleHeight];

            int idx = 0;
            for (int y = 0; y < sampleHeight; y++)
            {
                int srcY = Mathf.Min(sourceHeight - 1, y * stride);
                if (flipVertical)
                    srcY = (sourceHeight - 1) - srcY;

                int rowBase = srcY * sourceWidth;
                for (int x = 0; x < sampleWidth; x++)
                {
                    int srcX = Mathf.Min(sourceWidth - 1, x * stride);
                    float rawNdc = floatData[rowBase + srcX];

                    if (float.IsNaN(rawNdc) || float.IsInfinity(rawNdc) || rawNdc < 0f)
                        rawNdc = 0f;

                    // Decode 0..1 NDC → linear meters
                    float ndc = rawNdc * 2f - 1f;
                    float linearM = (1f / (ndc + zbp.y)) * zbp.x;
                    if (float.IsNaN(linearM) || float.IsInfinity(linearM) || linearM <= 0f)
                        linearM = 0f;

                    meters[idx++] = linearM;
                }
            }

            _frameId++;
            _latestDepthWidth = sampleWidth;
            _latestDepthHeight = sampleHeight;
            _latestDepthMeters = meters;
            _lastStatusMessage = "OK";

            // Cache per-frame depth metadata for CaptureSnapshot
            try
            {
                _cachedDepthFrameDesc = UnityEngine.XR.Oculus.Utils.GetEnvironmentalDepthFrameDesc(0);
                _cachedDepthFrameDescValid = true;
            }
            catch (System.Exception)
            {
                _cachedDepthFrameDescValid = false;
            }

            if (!_loggedFirstLocalFrame)
            {
                _loggedFirstLocalFrame = true;
                Debug.Log($"[DepthStreamModule] First local depth frame: {sampleWidth}x{sampleHeight}, {meters.Length} floats, range=[{Min(meters):F2}, {Max(meters):F2}]m");
            }

            if (!_loggedDepthSourceMeta)
            {
                _loggedDepthSourceMeta = true;
                manager?.QueueUnityLog(
                    "INFO",
                    $"Depth source ready: texture={sourceWidth}x{sourceHeight}, sampled={sampleWidth}x{sampleHeight}, stride={stride}, " +
                    $"flip_vertical={flipVertical}, preprocessed={usePreprocessedDepthTexture}, zbuffer=({zbp.x:F6},{zbp.y:F6},{zbp.z:F6},{zbp.w:F6})"
                );
            }

            MaybeSendDepthSourceMetadata(sourceWidth, sourceHeight, sampleWidth, sampleHeight, stride, zbp);

            if (!_loggedDualEyeComparison)
            {
                _loggedDualEyeComparison = true;
                LogDualEyeComparison(sourceDepth, sampleWidth, sampleHeight, stride, zbp);
            }

            return true;
        }

        private static float Min(float[] arr)
        {
            float m = float.MaxValue;
            for (int i = 0; i < arr.Length; i++)
                if (arr[i] > 0f && arr[i] < m) m = arr[i];
            return m == float.MaxValue ? 0f : m;
        }

        private static float Max(float[] arr)
        {
            float m = float.MinValue;
            for (int i = 0; i < arr.Length; i++)
                if (arr[i] > m) m = arr[i];
            return m == float.MinValue ? 0f : m;
        }

        /// <summary>
        /// One-shot diagnostic: read right-eye depth slice and compare against left-eye.
        /// Logs per-eye metadata (if available) and pixel-level difference statistics.
        /// </summary>
        private void LogDualEyeComparison(
            Texture sourceDepth, int sampleWidth, int sampleHeight, int stride, Vector4 zbp)
        {
            // ── Per-eye metadata ──
            // Left desc reused from _cachedDepthFrameDesc (already populated in TryReadbackDepthFrame)
            try
            {
                var leftDesc = _cachedDepthFrameDescValid
                    ? _cachedDepthFrameDesc
                    : UnityEngine.XR.Oculus.Utils.GetEnvironmentalDepthFrameDesc(0);
                var rightDesc = UnityEngine.XR.Oculus.Utils.GetEnvironmentalDepthFrameDesc(1);
                Debug.Log(
                    "[DepthDualEye] Metadata — " +
                    $"L: createTime={leftDesc.createTime}, " +
                    $"createPose=({leftDesc.createPoseLocation.x:F3},{leftDesc.createPoseLocation.y:F3},{leftDesc.createPoseLocation.z:F3}) " +
                    $"rot=({leftDesc.createPoseRotation.x:F4},{leftDesc.createPoseRotation.y:F4},{leftDesc.createPoseRotation.z:F4},{leftDesc.createPoseRotation.w:F4}), " +
                    $"fovL={leftDesc.fovLeft:F1} fovR={leftDesc.fovRight:F1} fovT={leftDesc.fovTop:F1} fovD={leftDesc.fovDown:F1}, " +
                    $"near={leftDesc.nearZ:F3} far={leftDesc.farZ:F3}, " +
                    $"depthRange=[{leftDesc.minDepth:F3},{leftDesc.maxDepth:F3}]"
                );
                Debug.Log(
                    "[DepthDualEye] Metadata — " +
                    $"R: createTime={rightDesc.createTime}, " +
                    $"createPose=({rightDesc.createPoseLocation.x:F3},{rightDesc.createPoseLocation.y:F3},{rightDesc.createPoseLocation.z:F3}) " +
                    $"rot=({rightDesc.createPoseRotation.x:F4},{rightDesc.createPoseRotation.y:F4},{rightDesc.createPoseRotation.z:F4},{rightDesc.createPoseRotation.w:F4}), " +
                    $"fovL={rightDesc.fovLeft:F1} fovR={rightDesc.fovRight:F1} fovT={rightDesc.fovTop:F1} fovD={rightDesc.fovDown:F1}, " +
                    $"near={rightDesc.nearZ:F3} far={rightDesc.farZ:F3}, " +
                    $"depthRange=[{rightDesc.minDepth:F3},{rightDesc.maxDepth:F3}]"
                );
                Debug.Log(
                    "[DepthDualEye] Pose delta: " +
                    $"pos=({(leftDesc.createPoseLocation - rightDesc.createPoseLocation).magnitude * 1000f:F1}mm), " +
                    $"time={(leftDesc.createTime - rightDesc.createTime):F3}s"
                );
            }
            catch (System.Exception ex)
            {
                Debug.Log($"[DepthDualEye] Metadata unavailable: {ex.Message}");
            }

            // ── Depth pixel comparison: read slice 1 ──
            try
            {
                _depthSliceMaterial.SetTexture("_SourceDepthArray", sourceDepth);
                _depthSliceMaterial.SetFloat("_ArraySlice", 1f);
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

                var rightFloatData = _depthReadbackTexture.GetRawTextureData<float>();
                if (!rightFloatData.IsCreated || rightFloatData.Length == 0)
                {
                    Debug.LogWarning("[DepthDualEye] Right-eye readback returned empty buffer");
                    return;
                }

                int sourceWidth = _depthRt.width;
                int sourceHeight = _depthRt.height;
                float[] rightMeters = new float[sampleWidth * sampleHeight];

                int idx = 0;
                for (int y = 0; y < sampleHeight; y++)
                {
                    int srcY = Mathf.Min(sourceHeight - 1, y * stride);
                    if (flipVertical)
                        srcY = (sourceHeight - 1) - srcY;
                    int rowBase = srcY * sourceWidth;
                    for (int x = 0; x < sampleWidth; x++)
                    {
                        int srcX = Mathf.Min(sourceWidth - 1, x * stride);
                        float rawNdc = rightFloatData[rowBase + srcX];
                        if (float.IsNaN(rawNdc) || float.IsInfinity(rawNdc) || rawNdc < 0f)
                            rawNdc = 0f;
                        float ndc = rawNdc * 2f - 1f;
                        float linearM = (1f / (ndc + zbp.y)) * zbp.x;
                        if (float.IsNaN(linearM) || float.IsInfinity(linearM) || linearM <= 0f)
                            linearM = 0f;
                        rightMeters[idx++] = linearM;
                    }
                }

                // Compare left (_latestDepthMeters) vs right
                int compared = 0;
                int bothValid = 0;
                double sumAbsDiff = 0;
                float maxAbsDiff = 0;
                int over5cm = 0;
                int over10cm = 0;

                for (int i = 0; i < _latestDepthMeters.Length && i < rightMeters.Length; i++)
                {
                    float l = _latestDepthMeters[i];
                    float r = rightMeters[i];
                    if (l <= 0 || r <= 0) continue;
                    compared++;
                    if (l > 0 && r > 0)
                    {
                        bothValid++;
                        float diff = Mathf.Abs(l - r);
                        sumAbsDiff += diff;
                        if (diff > maxAbsDiff) maxAbsDiff = diff;
                        if (diff > 0.05f) over5cm++;
                        if (diff > 0.10f) over10cm++;
                    }
                }

                float meanAbsDiff = bothValid > 0 ? (float)(sumAbsDiff / bothValid) : 0f;
                float pct5cm = bothValid > 0 ? (float)over5cm / bothValid * 100f : 0f;
                float pct10cm = bothValid > 0 ? (float)over10cm / bothValid * 100f : 0f;

                Debug.Log(
                    "[DepthDualEye] L vs R depth comparison: " +
                    $"samples={compared}, bothValid={bothValid}, " +
                    $"meanAbsDiff={meanAbsDiff * 1000f:F1}mm, maxAbsDiff={maxAbsDiff * 1000f:F1}mm, " +
                    $">5cm={over5cm} ({pct5cm:F1}%), >10cm={over10cm} ({pct10cm:F1}%)"
                );

                // Restore slice 0 for subsequent frames
                _depthSliceMaterial.SetFloat("_ArraySlice", 0f);
            }
            catch (System.Exception ex)
            {
                Debug.LogWarning($"[DepthDualEye] Comparison failed: {ex.Message}");
            }
        }

        public DepthFrameSnapshot CaptureSnapshot()
        {
            if (_latestDepthMeters == null || _latestDepthWidth <= 0 || _latestDepthHeight <= 0)
                return null;

            var reproj = Shader.GetGlobalMatrix("_EnvironmentDepthReprojectionMatrices");
            var zbp = Shader.GetGlobalVector("_EnvironmentDepthZBufferParams");
            float[] depthCopy = new float[_latestDepthMeters.Length];
            Array.Copy(_latestDepthMeters, depthCopy, _latestDepthMeters.Length);

            return new DepthFrameSnapshot
            {
                depthMeters = depthCopy,
                sampledWidth = _latestDepthWidth,
                sampledHeight = _latestDepthHeight,
                sourceWidth = _lastSourceDepthWidth,
                sourceHeight = _lastSourceDepthHeight,
                zBufferParams = new[] { zbp.x, zbp.y, zbp.z, zbp.w },
                reprojectionMatrix = new[]
                {
                    reproj.m00, reproj.m01, reproj.m02, reproj.m03,
                    reproj.m10, reproj.m11, reproj.m12, reproj.m13,
                    reproj.m20, reproj.m21, reproj.m22, reproj.m23,
                    reproj.m30, reproj.m31, reproj.m32, reproj.m33,
                },
                usedPreprocessedDepthTexture = usePreprocessedDepthTexture,
                usedFlipVertical = flipVertical,
                unityFrameCount = Time.frameCount,
                capturedAtUnixMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),

                // Per-frame depth metadata (from Utils.GetEnvironmentalDepthFrameDesc)
                depthCreateTime = _cachedDepthFrameDescValid ? _cachedDepthFrameDesc.createTime : 0.0,
                depthPredictedDisplayTime = _cachedDepthFrameDescValid ? _cachedDepthFrameDesc.predictedDisplayTime : 0.0,
                depthIsValid = _cachedDepthFrameDescValid,
                depthCreatePose7 = _cachedDepthFrameDescValid ? new[]
                {
                    _cachedDepthFrameDesc.createPoseLocation.x,
                    _cachedDepthFrameDesc.createPoseLocation.y,
                    _cachedDepthFrameDesc.createPoseLocation.z,
                    _cachedDepthFrameDesc.createPoseRotation.x,
                    _cachedDepthFrameDesc.createPoseRotation.y,
                    _cachedDepthFrameDesc.createPoseRotation.z,
                    _cachedDepthFrameDesc.createPoseRotation.w,
                } : null,
                fovAngles = _cachedDepthFrameDescValid ? new[]
                {
                    _cachedDepthFrameDesc.fovLeft, _cachedDepthFrameDesc.fovRight,
                    _cachedDepthFrameDesc.fovTop, _cachedDepthFrameDesc.fovDown,
                } : null,
                nearZ = _cachedDepthFrameDescValid ? _cachedDepthFrameDesc.nearZ : 0f,
                farZ = _cachedDepthFrameDescValid ? _cachedDepthFrameDesc.farZ : 0f,
                minDepth = _cachedDepthFrameDescValid ? _cachedDepthFrameDesc.minDepth : 0f,
                maxDepth = _cachedDepthFrameDescValid ? _cachedDepthFrameDesc.maxDepth : 0f,
            };
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
                if (_depthRt != null) { _depthRt.Release(); Destroy(_depthRt); }
                _depthRt = new RenderTexture(width, height, 0, RenderTextureFormat.RFloat)
                {
                    useMipMap = false,
                    autoGenerateMips = false,
                };
                _depthRt.Create();
            }

            if (_depthReadbackTexture == null || _depthReadbackTexture.width != width || _depthReadbackTexture.height != height)
                _depthReadbackTexture = new Texture2D(width, height, TextureFormat.RFloat, false);
        }

        private void MaybeSendDepthSourceMetadata(int sourceWidth, int sourceHeight, int sampleWidth, int sampleHeight, int stride, Vector4 zbp)
        {
            if (manager == null)
            {
                return;
            }

            if (_depthSourceMetaSent
                && _lastSentSourceWidth == sourceWidth
                && _lastSentSourceHeight == sourceHeight
                && _lastSentSampledWidth == sampleWidth
                && _lastSentSampledHeight == sampleHeight)
            {
                return;
            }

            var payload = new DepthSourceMetaPayload
            {
                source_width = sourceWidth,
                source_height = sourceHeight,
                sampled_width = sampleWidth,
                sampled_height = sampleHeight,
                stride = stride,
                flip_vertical = flipVertical,
                preprocessed = usePreprocessedDepthTexture,
                zbuffer_params = new[] { zbp.x, zbp.y, zbp.z, zbp.w },
                unity_frame_count = Time.frameCount,
                timestamp_ms = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
            };

            manager.QueueControlJson(JsonUtility.ToJson(payload));
            _depthSourceMetaSent = true;
            _lastSentSourceWidth = sourceWidth;
            _lastSentSourceHeight = sourceHeight;
            _lastSentSampledWidth = sampleWidth;
            _lastSentSampledHeight = sampleHeight;
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
            if (_depthRt != null) { _depthRt.Release(); Destroy(_depthRt); }
            if (_depthReadbackTexture != null) Destroy(_depthReadbackTexture);
            if (_depthSliceMaterial != null) Destroy(_depthSliceMaterial);
        }

        private sealed class EnvironmentRaycastManagerProvider : IEnvironmentRaycastProvider
        {
            private readonly EnvironmentRaycastManager _environmentRaycastManager;

            public EnvironmentRaycastManagerProvider(EnvironmentRaycastManager environmentRaycastManager)
            {
                _environmentRaycastManager = environmentRaycastManager;
            }

            public bool IsAvailable => _environmentRaycastManager != null && _environmentRaycastManager.isActiveAndEnabled;

            public bool TryRaycast(Ray ray, out Vector3 worldPoint)
            {
                worldPoint = Vector3.zero;
                if (!IsAvailable) return false;
                if (!_environmentRaycastManager.Raycast(ray, out var hitInfo)) return false;
                worldPoint = hitInfo.point;
                return true;
            }
        }

        /// <summary>
        /// Build a depth map aligned to the RGB camera frame.
        /// Uses Meta's _EnvironmentDepthReprojectionMatrices + ZBufferParams
        /// for depth→world, then PCA.WorldToViewportPoint for world→RGB pixel.
        /// Returns null if no depth data or pca is unavailable.
        /// </summary>
        public AlignedDepthProjectionResult BuildAlignedDepth(
            DepthFrameSnapshot snapshot,
            int rgbW, int rgbH,
            float[] rgbPose7,        // px,py,pz, qx,qy,qz,qw
            PassthroughCameraAccess pca)
        {
            if (snapshot == null || snapshot.depthMeters == null || snapshot.sampledWidth < 2 || snapshot.sampledHeight < 2)
                return null;
            if (pca == null || !pca.isActiveAndEnabled)
                return null;

            var reproj = MatrixFromRowMajor(snapshot.reprojectionMatrix);
            var depthToWorld = reproj.inverse;

            if (!_loggedReprojectionMeta)
            {
                _loggedReprojectionMeta = true;
                Debug.Log(
                    $"[DepthAlign] Reprojection matrix acquired: " +
                    $"m00={reproj.m00:F6}, m11={reproj.m11:F6}, m22={reproj.m22:F6}, m33={reproj.m33:F6}, " +
                    $"det={reproj.determinant:F6}"
                );
            }

            int dw = snapshot.sampledWidth;
            int dh = snapshot.sampledHeight;
            int totalRgbPixels = rgbW * rgbH;
            float[] aligned = new float[totalRgbPixels];
            byte[] validMask = new byte[totalRgbPixels];
            for (int i = 0; i < aligned.Length; i++) aligned[i] = float.NaN;

            // RGB camera pose for WorldToViewportPoint
            var rgbPos = new Vector3(rgbPose7[0], rgbPose7[1], rgbPose7[2]);
            var rgbRot = new Quaternion(rgbPose7[3], rgbPose7[4], rgbPose7[5], rgbPose7[6]);
            var rgbPose = new Pose(rgbPos, rgbRot);
            var worldToRgb = Matrix4x4.TRS(rgbPos, rgbRot, Vector3.one).inverse;

            float invDw = 1f / Mathf.Max(dw - 1, 1);
            float invDh = 1f / Mathf.Max(dh - 1, 1);
            float zbpX = snapshot.zBufferParams[0], zbpY = snapshot.zBufferParams[1];

            // ── debug: log sample points ──
            int validCount = 0;
            int attemptedCount = 0;
            int clippedCount = 0;
            int behindCameraCount = 0;
            int collidedPixels = 0;
            float sumPx = 0, sumPy = 0, sumWorldX = 0, sumWorldY = 0, sumWorldZ = 0;
            float minPx = float.MaxValue, maxPx = float.MinValue;
            float minPy = float.MaxValue, maxPy = float.MinValue;
            float minCamZ = float.MaxValue, maxCamZ = float.MinValue, sumCamZ = 0f;

            for (int y = 0; y < dh; y++)
            {
                for (int x = 0; x < dw; x++)
                {
                    float d = snapshot.depthMeters[y * dw + x];
                    if (d <= 0f || float.IsNaN(d) || float.IsInfinity(d)) continue;
                    attemptedCount++;

                    // NDC: pixel → [-1, 1]
                    float ndcX = x * invDw * 2f - 1f;
                    float ndcY = y * invDh * 2f - 1f;
                    float ndcZ = zbpX / d - zbpY;

                    var clip = new Vector4(ndcX, ndcY, ndcZ, 1f);
                    var worldH = depthToWorld * clip;
                    if (Mathf.Abs(worldH.w) < 1e-10f) continue;
                    var world = new Vector3(worldH.x, worldH.y, worldH.z) / worldH.w;
                    var rgbLocalH = worldToRgb * new Vector4(world.x, world.y, world.z, 1f);
                    float rgbCameraZ = rgbLocalH.z;
                    if (rgbCameraZ <= 0f || float.IsNaN(rgbCameraZ) || float.IsInfinity(rgbCameraZ))
                    {
                        behindCameraCount++;
                        continue;
                    }

                    // Project to RGB viewport
                    var vp = pca.WorldToViewportPoint(world, rgbPose);
                    int px = Mathf.RoundToInt(vp.x * (rgbW - 1));
                    int py = Mathf.RoundToInt((1f - vp.y) * (rgbH - 1)); // Unity viewport Y=0 at bottom

                    if (px >= 0 && px < rgbW && py >= 0 && py < rgbH)
                    {
                        int idx = py * rgbW + px;
                        if (!float.IsNaN(aligned[idx]) && validMask[idx] != 0)
                            collidedPixels++;
                        if (float.IsNaN(aligned[idx]) || rgbCameraZ < aligned[idx])
                        {
                            aligned[idx] = rgbCameraZ;
                            validMask[idx] = 1;
                        }

                        validCount++;
                        sumPx += px; sumPy += py;
                        sumWorldX += world.x; sumWorldY += world.y; sumWorldZ += world.z;
                        sumCamZ += rgbCameraZ;
                        if (px < minPx) minPx = px; if (px > maxPx) maxPx = px;
                        if (py < minPy) minPy = py; if (py > maxPy) maxPy = py;
                        if (rgbCameraZ < minCamZ) minCamZ = rgbCameraZ;
                        if (rgbCameraZ > maxCamZ) maxCamZ = rgbCameraZ;
                    }
                    else
                    {
                        clippedCount++;
                    }
                }
            }

            if (validCount > 0)
            {
                float avgPx = sumPx / validCount, avgPy = sumPy / validCount;
                float avgWx = sumWorldX / validCount, avgWy = sumWorldY / validCount, avgWz = sumWorldZ / validCount;
                float avgCamZ = sumCamZ / validCount;
                Debug.Log($"[DepthAlign] {validCount} valid px, world=({avgWx:F2},{avgWy:F2},{avgWz:F2}), "
                    + $"rgb_px=({minPx:F0}~{maxPx:F0}, {minPy:F0}~{maxPy:F0}), avg=({avgPx:F0},{avgPy:F0}), "
                    + $"rgb_z=[{minCamZ:F3},{maxCamZ:F3}] avg={avgCamZ:F3}, depth={dw}x{dh}, zbp=({zbpX:F4},{zbpY:F4}), snapshot_ms={snapshot.capturedAtUnixMs}");
            }

            return new AlignedDepthProjectionResult
            {
                sparseAlignedDepth = aligned,
                validMask = validMask,
                rgbWidth = rgbW,
                rgbHeight = rgbH,
                debugProjectionMeta = new DebugProjectionMeta
                {
                    depthWidth = dw,
                    depthHeight = dh,
                    rgbWidth = rgbW,
                    rgbHeight = rgbH,
                    sourceWidth = snapshot.sourceWidth,
                    sourceHeight = snapshot.sourceHeight,
                    attemptedPoints = attemptedCount,
                    validPoints = validCount,
                    clippedPoints = clippedCount,
                    pointsBehindCamera = behindCameraCount,
                    collidedPixels = collidedPixels,
                    minPixelX = validCount > 0 ? minPx : -1f,
                    maxPixelX = validCount > 0 ? maxPx : -1f,
                    minPixelY = validCount > 0 ? minPy : -1f,
                    maxPixelY = validCount > 0 ? maxPy : -1f,
                    avgPixelX = validCount > 0 ? sumPx / validCount : -1f,
                    avgPixelY = validCount > 0 ? sumPy / validCount : -1f,
                    minRgbCameraZ = validCount > 0 ? minCamZ : 0f,
                    maxRgbCameraZ = validCount > 0 ? maxCamZ : 0f,
                    avgRgbCameraZ = validCount > 0 ? sumCamZ / validCount : 0f,
                    usedFlipVertical = snapshot.usedFlipVertical,
                    usedPreprocessedDepthTexture = snapshot.usedPreprocessedDepthTexture,
                    capturedAtUnixMs = snapshot.capturedAtUnixMs,
                },
            };
        }

        private static Matrix4x4 MatrixFromRowMajor(float[] values)
        {
            if (values == null || values.Length != 16)
                return Matrix4x4.identity;

            var mat = new Matrix4x4();
            mat.m00 = values[0]; mat.m01 = values[1]; mat.m02 = values[2]; mat.m03 = values[3];
            mat.m10 = values[4]; mat.m11 = values[5]; mat.m12 = values[6]; mat.m13 = values[7];
            mat.m20 = values[8]; mat.m21 = values[9]; mat.m22 = values[10]; mat.m23 = values[11];
            mat.m30 = values[12]; mat.m31 = values[13]; mat.m32 = values[14]; mat.m33 = values[15];
            return mat;
        }
    }
}
