using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using Meta.XR;
using Meta.XR.EnvironmentDepth;
using Unity.Collections;
using UnityEngine;

namespace SmartRoom.Testing
{
    /// <summary>
    /// Self-contained RGB-D capture test for Meta Quest 3 (v85 SDK).
    /// Captures N frames of RGB + depth data to Application.persistentDataPath/rgbd_test/.
    /// No backend/network dependencies. Displays status via Debug.Log + head-locked HUD.
    ///
    /// Output per frame:
    ///   capture_XXXX/
    ///     meta.json    – structured metadata
    ///     rgb.jpg      – JPEG image (quality=95)
    ///     depth.raw    – NDC float32 raw (w×h×4 bytes, little-endian)
    /// </summary>
    public class RGBDCaptureTest : MonoBehaviour
    {
        // ── Serializable types for meta.json ──

        [Serializable]
        private sealed class CaptureMetaJson
        {
            public int capture_index;
            public int unity_frame;
            public long timestamp_unix_ms;
            public RgbSection rgb;
            public DepthSection depth;
        }

        [Serializable]
        private sealed class RgbSection
        {
            public long timestamp_ticks;
            public ResolutionInfo resolution;
            public ResolutionInfo requested_resolution;
            public ResolutionInfo current_resolution;
            public ResolutionInfo sensor_resolution;
            public Vec2 focal_length;
            public Vec2 principal_point;
            public PoseInfo pose;
        }

        [Serializable]
        private sealed class DepthSection
        {
            public double timestamp_create;
            public double timestamp_predicted;
            public int swapchain_index;
            public bool is_valid;
            public string fov_source;
            public ResolutionInfo resolution;
            public float fov_left;
            public float fov_right;
            public float fov_top;
            public float fov_bottom;
            public float near_z;
            public float far_z;
            public float min_depth;
            public float max_depth;
            public PoseInfo pose;
            public Vec4 zbuffer_params;
        }

        [Serializable]
        private sealed class ResolutionInfo { public int w; public int h; }

        [Serializable]
        private sealed class Vec2 { public float x; public float y; }

        [Serializable]
        private sealed class Vec3 { public float x; public float y; public float z; }

        [Serializable]
        private sealed class Vec4 { public float x; public float y; public float z; public float w; }

        [Serializable]
        private sealed class PoseInfo
        {
            public Vec3 position;
            public Vec4 rotation;
        }

        // ── Capture mode enum ──

        private enum CaptureMode
        {
            Automatic,  // auto-capture N frames on start
            Manual      // press right-hand A button per frame
        }

        // ── Inspector fields ──

        [Header("Capture Config")]
        [SerializeField] private CaptureMode captureMode = CaptureMode.Automatic;
        [SerializeField] private int frameCount = 5;
        [SerializeField] private float frameInterval = 0.5f;
        [SerializeField] private int jpegQuality = 95;

        [Header("RGB Output")]
        [SerializeField] private int rgbOutputWidth = 640;
        [SerializeField] private int rgbOutputHeight = 360;

        [Header("HUD")]
        [SerializeField] private float hudDistance = 1.5f;
        [SerializeField] private int hudFontSize = 48;

        // ── Runtime references ──

        private PassthroughCameraAccess _pca;
        private EnvironmentDepthManager _depthManager;
        private Camera _xrCamera;
        private Shader _depthShader;
        private Material _depthMaterial;

        // ── GPU buffers ──

        private RenderTexture _rgbRt;
        private Texture2D _rgbReadback;
        private RenderTexture _depthRt;
        private Texture2D _depthReadback;

        // ── Capture state ──

        private int _captureIndex;
        private bool _done;
        private bool _prevAButton;

        // ── Log collection ──

        private readonly List<string> _logEntries = new List<string>();
        private string _outputRoot;

        // ── HUD ──

        private GameObject _hudObject;
        private TextMesh _hudText;

        // ── Permission constants ──

        private const string CameraPermission = "horizonos.permission.HEADSET_CAMERA";
        private const string ScenePermission = "com.oculus.permission.USE_SCENE";
        private const string DepthShaderRuntime = "Hidden/SmartRoom/DepthArraySliceToFloat";
        private const string DepthShaderResource = "Hidden/SmartRoom/DepthArraySliceToFloat_Resource";
        private const string DepthShaderAssetPath = "SmartRoomDepthArraySliceToFloat";
        private const string RawDepthGlobal = "_EnvironmentDepthTexture";

        // ═══════════════════════════════════════════════════════════════
        //  Unity Lifecycle
        // ═══════════════════════════════════════════════════════════════

        private void Awake()
        {
            // Hook into Unity's log system to collect all log entries
            Application.logMessageReceived += OnLogMessageReceived;

            // Find references
            _pca = FindFirstObjectByType<PassthroughCameraAccess>();
            _depthManager = FindFirstObjectByType<EnvironmentDepthManager>();
            _xrCamera = Camera.main;

            // Find or load depth shader (same pattern as DepthStreamModule)
            _depthShader = Shader.Find(DepthShaderRuntime);
            if (_depthShader == null)
                _depthShader = Shader.Find(DepthShaderResource);
            if (_depthShader == null)
                _depthShader = Resources.Load<Shader>(DepthShaderAssetPath);

            if (_depthShader != null)
                _depthMaterial = new Material(_depthShader);

            // Clamp config values
            jpegQuality = Mathf.Clamp(jpegQuality, 1, 100);
            frameCount = Mathf.Max(1, frameCount);
            frameInterval = Mathf.Max(0.1f, frameInterval);
            rgbOutputWidth = Mathf.Max(16, rgbOutputWidth);
            rgbOutputHeight = Mathf.Max(16, rgbOutputHeight);

            // Set output root
            _outputRoot = Path.Combine(Application.persistentDataPath, "rgbd_test");
            Directory.CreateDirectory(_outputRoot);

            CreateHUD();

            Debug.Log($"[RGBDCaptureTest] Awake: mode={captureMode}, frames={frameCount}, " +
                      $"interval={frameInterval}s, output={_outputRoot}, " +
                      $"pca={(_pca != null ? "found" : "null")}, " +
                      $"depthMgr={(_depthManager != null ? "found" : "null")}, " +
                      $"shader={(_depthShader != null ? "found" : "null")}");
        }

        private void Start()
        {
            StartCoroutine(CaptureRoutine());
        }

        private void Update()
        {
            UpdateHudPosition();

            if (_done)
                return;

            // Manual mode: check right-hand A button (OVRInput.Button.One)
            if (captureMode == CaptureMode.Manual)
            {
                bool aPressed = OVRInput.Get(OVRInput.Button.One, OVRInput.Controller.RTouch);
                if (aPressed && !_prevAButton)
                {
                    Debug.Log($"[RGBDCaptureTest] Manual capture triggered (frame {_captureIndex + 1}/{frameCount})");
                    CaptureSingleFrame();
                }
                _prevAButton = aPressed;
            }
        }

        private void OnDestroy()
        {
            Application.logMessageReceived -= OnLogMessageReceived;

            // Save log file
            if (_logEntries.Count > 0)
            {
                SaveLogFile();
            }

            // Cleanup GPU resources
            if (_rgbRt != null) { _rgbRt.Release(); Destroy(_rgbRt); }
            if (_rgbReadback != null) Destroy(_rgbReadback);
            if (_depthRt != null) { _depthRt.Release(); Destroy(_depthRt); }
            if (_depthReadback != null) Destroy(_depthReadback);
            if (_depthMaterial != null) Destroy(_depthMaterial);

            if (_hudObject != null) Destroy(_hudObject);
        }

        // ═══════════════════════════════════════════════════════════════
        //  Capture Routine (Coroutine for automatic mode)
        // ═══════════════════════════════════════════════════════════════

        private IEnumerator CaptureRoutine()
        {
            UpdateHUD("Waiting for permissions...");
            Debug.Log("[RGBDCaptureTest] Requesting permissions...");

            // Wait for permissions
            yield return StartCoroutine(RequestPermissions());

            if (!HasCameraPermission() || !HasScenePermission())
            {
                UpdateHUD("ERROR: Permissions denied");
                Debug.LogError("[RGBDCaptureTest] Required permissions not granted");
                yield break;
            }

            // If PCA reference is missing, try to find again
            if (_pca == null)
            {
                _pca = FindFirstObjectByType<PassthroughCameraAccess>();
            }

            if (_pca == null || !_pca.isActiveAndEnabled)
            {
                UpdateHUD("ERROR: PCA not available");
                Debug.LogError("[RGBDCaptureTest] PassthroughCameraAccess not found or not enabled");
                yield break;
            }

            // Wait for PCA to start playing
            UpdateHUD("Waiting for PCA...");
            Debug.Log("[RGBDCaptureTest] Waiting for PassthroughCameraAccess to start playing...");
            float pcaWaitStart = Time.time;
            while (!_pca.IsPlaying && Time.time - pcaWaitStart < 10f)
            {
                yield return null;
            }

            if (!_pca.IsPlaying)
            {
                UpdateHUD("ERROR: PCA not playing");
                Debug.LogError("[RGBDCaptureTest] PCA did not start playing within timeout");
                yield break;
            }

            // Wait for depth availability
            if (_depthManager != null)
            {
                UpdateHUD("Waiting for depth...");
                Debug.Log("[RGBDCaptureTest] Waiting for depth availability...");
                float depthWaitStart = Time.time;
                while (!_depthManager.IsDepthAvailable && Time.time - depthWaitStart < 15f)
                {
                    yield return null;
                }
            }

            // Start capturing
            _captureIndex = 0;

            if (captureMode == CaptureMode.Automatic)
            {
                Debug.Log($"[RGBDCaptureTest] Starting automatic capture: {frameCount} frames, {frameInterval}s interval");
                UpdateHUD($"Ready. Capturing {frameCount} frames...");

                // Brief initial delay before first capture to let systems settle
                yield return new WaitForSeconds(1.0f);

                while (_captureIndex < frameCount)
                {
                    CaptureSingleFrame();

                    if (_captureIndex < frameCount)
                        yield return new WaitForSeconds(frameInterval);
                }
            }
            else
            {
                Debug.Log($"[RGBDCaptureTest] Manual mode ready. Press A button to capture (up to {frameCount} frames).");
                UpdateHUD("Ready. Press A to capture.");
            }
        }

        // ═══════════════════════════════════════════════════════════════
        //  Single Frame Capture
        // ═══════════════════════════════════════════════════════════════

        private void CaptureSingleFrame()
        {
            if (_captureIndex >= frameCount)
            {
                Debug.LogWarning("[RGBDCaptureTest] Already captured requested number of frames");
                return;
            }

            int idx = _captureIndex;
            int unityFrame = Time.frameCount;
            long captureUnixMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            string frameDir = Path.Combine(_outputRoot, $"capture_{idx:D4}");
            Directory.CreateDirectory(frameDir);

            Debug.Log($"[RGBDCaptureTest] ── Capturing frame {idx + 1}/{frameCount} (unity_frame={unityFrame}) ──");

            // ── Capture RGB ──
            bool rgbOk = TryCaptureRGB(out byte[] jpegBytes, out RgbSection rgbMeta);

            // ── Capture Depth (NDC) ──
            bool depthOk = TryCaptureDepthNDC(out byte[] ndcRaw, out DepthSection depthMeta);

            // ── Check integrity: same-frame guarantee ──
            if (!rgbOk && !depthOk)
            {
                Debug.LogError($"[RGBDCaptureTest] Frame {idx}: Both RGB and depth capture failed. Skipping.");
                UpdateHUD($"ERROR: Frame {idx} failed");
                return;
            }

            // ── Build meta.json ──
            var meta = new CaptureMetaJson
            {
                capture_index = idx,
                unity_frame = unityFrame,
                timestamp_unix_ms = captureUnixMs,
                rgb = rgbMeta,
                depth = depthMeta
            };

            // ── Save files ──
            string metaPath = Path.Combine(frameDir, "meta.json");
            try
            {
                File.WriteAllText(metaPath, JsonUtility.ToJson(meta, true));
                Debug.Log($"[RGBDCaptureTest]   meta.json → {metaPath}");
            }
            catch (Exception ex)
            {
                Debug.LogError($"[RGBDCaptureTest] Failed to write meta.json: {ex.Message}");
            }

            if (rgbOk && jpegBytes != null)
            {
                string rgbPath = Path.Combine(frameDir, "rgb.jpg");
                try
                {
                    File.WriteAllBytes(rgbPath, jpegBytes);
                    Debug.Log($"[RGBDCaptureTest]   rgb.jpg → {rgbPath} ({jpegBytes.Length} bytes, {rgbMeta.resolution?.w ?? 0}x{rgbMeta.resolution?.h ?? 0})");
                }
                catch (Exception ex)
                {
                    Debug.LogError($"[RGBDCaptureTest] Failed to write rgb.jpg: {ex.Message}");
                }
            }

            if (depthOk && ndcRaw != null)
            {
                string depthPath = Path.Combine(frameDir, "depth.raw");
                try
                {
                    File.WriteAllBytes(depthPath, ndcRaw);
                    Debug.Log($"[RGBDCaptureTest]   depth.raw → {depthPath} ({ndcRaw.Length} bytes, {depthMeta.resolution?.w ?? 0}x{depthMeta.resolution?.h ?? 0})");
                }
                catch (Exception ex)
                {
                    Debug.LogError($"[RGBDCaptureTest] Failed to write depth.raw: {ex.Message}");
                }
            }

            // ── Log summary ──
            Debug.Log($"[RGBDCaptureTest] Frame {idx} saved: rgb={(rgbOk ? "OK" : "FAIL")}, depth={(depthOk ? "OK" : "FAIL")}, " +
                      $"rgb_size={rgbMeta.resolution?.w ?? 0}x{rgbMeta.resolution?.h ?? 0}, " +
                      $"depth_size={depthMeta.resolution?.w ?? 0}x{depthMeta.resolution?.h ?? 0}, " +
                      $"fov=({depthMeta.fov_left:F3},{depthMeta.fov_right:F3},{depthMeta.fov_top:F3},{depthMeta.fov_bottom:F3})");

            _captureIndex++;

            // Check if done
            if (_captureIndex >= frameCount)
            {
                _done = true;
                SaveLogFile();
                Debug.Log($"[RGBDCaptureTest] DONE! {frameCount} frames saved to {_outputRoot}");
                UpdateHUD($"Done! {frameCount} frames saved");
            }
            else
            {
                if (captureMode == CaptureMode.Automatic)
                {
                    UpdateHUD($"Capturing {_captureIndex}/{frameCount}...");
                }
                else
                {
                    UpdateHUD($"Captured {_captureIndex}/{frameCount}. Press A for next.");
                }
            }
        }

        // ═══════════════════════════════════════════════════════════════
        //  RGB Capture
        // ═══════════════════════════════════════════════════════════════

        private bool TryCaptureRGB(out byte[] jpegBytes, out RgbSection meta)
        {
            jpegBytes = null;
            meta = new RgbSection();

            if (_pca == null || !_pca.isActiveAndEnabled || !_pca.IsPlaying)
            {
                Debug.LogWarning("[RGBDCaptureTest] PCA not available for RGB capture");
                return false;
            }

            Texture source = _pca.GetTexture();
            if (source == null)
            {
                Debug.LogWarning("[RGBDCaptureTest] PCA texture is null");
                return false;
            }

            // ── GPU readback: Blit → ReadPixels → EncodeToJPG ──
            // (same pattern as RgbStreamModule.TryGetJpegFrame)
            EnsureRgbBuffers();

            RenderTexture previous = RenderTexture.active;
            try
            {
                Graphics.Blit(source, _rgbRt);
                RenderTexture.active = _rgbRt;
                _rgbReadback.ReadPixels(new Rect(0, 0, rgbOutputWidth, rgbOutputHeight), 0, 0, false);
                _rgbReadback.Apply(false, false);
                jpegBytes = _rgbReadback.EncodeToJPG(jpegQuality);
            }
            catch (Exception ex)
            {
                Debug.LogError($"[RGBDCaptureTest] RGB readback failed: {ex.Message}");
                return false;
            }
            finally
            {
                RenderTexture.active = previous;
            }

            if (jpegBytes == null || jpegBytes.Length == 0)
            {
                Debug.LogWarning("[RGBDCaptureTest] EncodeToJPG returned empty data");
                return false;
            }

            // ── Build RGB metadata ──
            var intrinsics = _pca.Intrinsics;
            long timestampTicks = _pca.Timestamp.Ticks;
            Pose cameraPose = _pca.GetCameraPose();

            meta = new RgbSection
            {
                timestamp_ticks = timestampTicks,
                resolution = new ResolutionInfo { w = rgbOutputWidth, h = rgbOutputHeight },
                requested_resolution = new ResolutionInfo
                {
                    w = _pca.RequestedResolution.x,
                    h = _pca.RequestedResolution.y
                },
                current_resolution = new ResolutionInfo
                {
                    w = _pca.CurrentResolution.x,
                    h = _pca.CurrentResolution.y
                },
                sensor_resolution = new ResolutionInfo
                {
                    w = intrinsics.SensorResolution.x,
                    h = intrinsics.SensorResolution.y
                },
                focal_length = new Vec2
                {
                    x = intrinsics.FocalLength.x,
                    y = intrinsics.FocalLength.y
                },
                principal_point = new Vec2
                {
                    x = intrinsics.PrincipalPoint.x,
                    y = intrinsics.PrincipalPoint.y
                },
                pose = new PoseInfo
                {
                    position = new Vec3
                    {
                        x = cameraPose.position.x,
                        y = cameraPose.position.y,
                        z = cameraPose.position.z
                    },
                    rotation = new Vec4
                    {
                        x = cameraPose.rotation.x,
                        y = cameraPose.rotation.y,
                        z = cameraPose.rotation.z,
                        w = cameraPose.rotation.w
                    }
                }
            };

            return true;
        }

        // ═══════════════════════════════════════════════════════════════
        //  Depth Capture (NDC, not linearized)
        // ═══════════════════════════════════════════════════════════════

        private bool TryCaptureDepthNDC(out byte[] ndcRaw, out DepthSection meta)
        {
            ndcRaw = null;
            meta = new DepthSection();

            // ── Get depth metadata — try multiple sources ──
            //
            // Priority order:
            //   1. Reflection into UnityEngine.XR.Oculus.Utils (may not exist in OpenXR routing)
            //   2. Shader globals: _EnvironmentDepthReprojectionMatrices → extract pose & FOV
            //   3. Shader globals: _EnvironmentDepthZBufferParams → extract near/far

            bool descValid = false;
            float fovLeft = 0, fovRight = 0, fovTop = 0, fovDown = 0;
            float nearZ = 0, farZ = 0, minD = 0, maxD = 0;
            double createTime = 0, predictedTime = 0;
            int descW = 0, descH = 0;
            Vector3 createPos = Vector3.zero;
            Quaternion createRot = Quaternion.identity;
            string fovSource = "none";

            // ── Source 1: Reflection ──
            _TryReadDepthDescriptorReflected(
                out descValid,
                out fovLeft, out fovRight, out fovTop, out fovDown,
                out nearZ, out farZ, out minD, out maxD,
                out createTime, out predictedTime,
                out descW, out descH,
                out createPos, out createRot);
            if (descValid) fovSource = "reflection";

            // ── Source 2: Shader globals (fallback) ──
            if (!descValid)
            {
                // Read depth reprojection matrix (2× 4×4, left/right eyes)
                var reprojMats = Shader.GetGlobalMatrixArray("_EnvironmentDepthReprojectionMatrices");
                if (reprojMats != null && reprojMats.Length >= 2)
                {
                    // Decompose: VP = camera projection × camera view
                    // Right column of VP gives us depth-space coords at infinity,
                    // from which we can extract the camera position and FOV.
                    var VP = reprojMats[0]; // left eye
                    var VPinv = VP.inverse;

                    // Camera position in world space
                    var camPos4 = VPinv * new Vector4(0, 0, 0, 1);
                    createPos = new Vector3(camPos4.x, camPos4.y, camPos4.z) / camPos4.w;

                    // Camera rotation: forward direction in world space
                    var fwd4 = VPinv * new Vector4(0, 0, 1, 0);
                    Vector3 fwd = new Vector3(fwd4.x, fwd4.y, fwd4.z).normalized;
                    var up4 = VPinv * new Vector4(0, 1, 0, 0);
                    Vector3 up = new Vector3(up4.x, up4.y, up4.z).normalized;
                    createRot = Quaternion.LookRotation(fwd, up);

                    // FOV from projection component
                    // For symmetric FOV: fx = w/2 * VP[0,0], so tan(halfFov) = 1/VP[0,0]
                    float invTanH = VP[0, 0];
                    float invTanV = VP[1, 1];
                    if (Mathf.Abs(invTanH) > 0.001f && Mathf.Abs(invTanV) > 0.001f)
                    {
                        fovRight = 1f / invTanH;
                        fovLeft  = 1f / invTanH;
                        fovTop   = 1f / invTanV;
                        fovDown  = 1f / invTanV;
                    }

                    descValid = true;
                    fovSource = "reprojection_matrix";
                    Debug.Log($"[RGBDCaptureTest] Reprojection matrix OK: pos=({createPos.x:F2},{createPos.y:F2},{createPos.z:F2}), fov_tan=({fovRight:F3},{fovDown:F3})");
                }
            }

            // ── Source 3: ZBufferParams → near/far ──
            Vector4 zbp = Shader.GetGlobalVector("_EnvironmentDepthZBufferParams");
            if (nearZ == 0 && farZ == 0 && Mathf.Abs(zbp.x) > 0.0001f)
            {
                float xAbs = Mathf.Abs(zbp.x);
                float yAbs = Mathf.Abs(zbp.y);
                if (Mathf.Abs(yAbs - 1f) < 0.01f)
                {
                    nearZ = xAbs / 2f;
                    farZ  = float.PositiveInfinity;
                }
                else
                {
                    nearZ = xAbs / (yAbs - 1f);
                    farZ  = xAbs / (yAbs + 1f);
                    if (nearZ > farZ) { var tmp = nearZ; nearZ = farZ; farZ = tmp; }
                    if (nearZ < 0) nearZ = Mathf.Abs(nearZ);
                    if (farZ < 0) farZ = Mathf.Abs(farZ);
                }
            }

            // Build depth metadata (even if readback fails, we have the descriptor)
            meta = BuildDepthMeta(descValid, fovSource, createTime, predictedTime, createPos, createRot,
                fovLeft, fovRight, fovTop, fovDown, nearZ, farZ, minD, maxD, descW, descH, zbp);

            // ── GPU readback: raw NDC ──
            if (_depthManager == null)
            {
                Debug.LogWarning("[RGBDCaptureTest] EnvironmentDepthManager not found");
                return false;
            }

            if (!_depthManager.IsDepthAvailable)
            {
                Debug.LogWarning("[RGBDCaptureTest] Depth not available yet");
                return false;
            }

            if (_depthMaterial == null)
            {
                Debug.LogWarning("[RGBDCaptureTest] Depth slice material not available");
                return false;
            }

            if (!SystemInfo.SupportsRenderTextureFormat(RenderTextureFormat.RFloat))
            {
                Debug.LogWarning("[RGBDCaptureTest] RFloat render texture format not supported");
                return false;
            }

            Texture sourceDepth = Shader.GetGlobalTexture(RawDepthGlobal);
            if (sourceDepth == null)
            {
                Debug.LogWarning($"[RGBDCaptureTest] Global texture '{RawDepthGlobal}' not found");
                return false;
            }

            if (sourceDepth.dimension != UnityEngine.Rendering.TextureDimension.Tex2DArray)
            {
                Debug.LogWarning($"[RGBDCaptureTest] Depth texture is not Tex2DArray: {sourceDepth.dimension}");
                return false;
            }

            int dw = sourceDepth.width;
            int dh = sourceDepth.height;

            if (dw <= 0 || dh <= 0)
            {
                Debug.LogWarning($"[RGBDCaptureTest] Invalid depth texture size: {dw}x{dh}");
                return false;
            }

            // ── Extract slice 0 → NDC float data ──
            EnsureDepthBuffers(dw, dh);

            _depthMaterial.SetTexture("_SourceDepthArray", sourceDepth);
            _depthMaterial.SetFloat("_ArraySlice", 0f);
            Graphics.Blit(null, _depthRt, _depthMaterial);

            RenderTexture previous = RenderTexture.active;
            RenderTexture.active = _depthRt;
            try
            {
                _depthReadback.ReadPixels(new Rect(0, 0, dw, dh), 0, 0, false);
                _depthReadback.Apply(false, false);
            }
            catch (Exception ex)
            {
                Debug.LogError($"[RGBDCaptureTest] Depth readback exception: {ex.Message}");
                return false;
            }
            finally
            {
                RenderTexture.active = previous;
            }

            // ── Get raw NDC float data (DO NOT linearize) ──
            NativeArray<float> floatData = _depthReadback.GetRawTextureData<float>();
            if (!floatData.IsCreated || floatData.Length == 0)
            {
                Debug.LogWarning("[RGBDCaptureTest] Depth readback returned empty buffer");
                return false;
            }

            int pixelCount = dw * dh;
            float[] ndcFloats = new float[pixelCount];
            floatData.CopyTo(ndcFloats);

            // ── Convert to byte array (float32 little-endian) ──
            ndcRaw = new byte[pixelCount * sizeof(float)];
            Buffer.BlockCopy(ndcFloats, 0, ndcRaw, 0, ndcRaw.Length);

            // ── Update depth metadata with actual resolution ──
            meta = BuildDepthMeta(descValid, fovSource,
                createTime, predictedTime, createPos, createRot,
                fovLeft, fovRight, fovTop, fovDown,
                nearZ, farZ, minD, maxD,
                descW, descH,
                zbp, dw, dh);

            // ── Log NDC range ──
            float ndcMin = float.MaxValue, ndcMax = float.MinValue;
            for (int i = 0; i < ndcFloats.Length; i++)
            {
                float v = ndcFloats[i];
                if (!float.IsNaN(v) && !float.IsInfinity(v))
                {
                    if (v < ndcMin) ndcMin = v;
                    if (v > ndcMax) ndcMax = v;
                }
            }
            Debug.Log($"[RGBDCaptureTest] Depth NDC range: [{ndcMin:F4}, {ndcMax:F4}], zbp=({zbp.x:F4},{zbp.y:F4},{zbp.z:F4},{zbp.w:F4})");

            return true;
        }

        private DepthSection BuildDepthMeta(bool descValid, string fovSource,
            double createTime, double predictedTime,
            Vector3 createPos, Quaternion createRot,
            float fovLeft, float fovRight, float fovTop, float fovDown,
            float nearZ, float farZ, float minD, float maxD,
            int descW, int descH,
            Vector4 zbp,
            int actualWidth = 0, int actualHeight = 0)
        {
            var meta = new DepthSection
            {
                swapchain_index = 0,
                is_valid = descValid,
                fov_source = fovSource ?? "none",
                zbuffer_params = new Vec4 { x = zbp.x, y = zbp.y, z = zbp.z, w = zbp.w }
            };

            if (descValid)
            {
                meta.timestamp_create = createTime;
                meta.timestamp_predicted = predictedTime;
                meta.fov_left = fovLeft;
                meta.fov_right = fovRight;
                meta.fov_top = fovTop;
                meta.fov_bottom = fovDown;
                meta.near_z = nearZ;
                meta.far_z = farZ;
                meta.min_depth = minD;
                meta.max_depth = maxD;
                meta.pose = new PoseInfo
                {
                    position = new Vec3 { x = createPos.x, y = createPos.y, z = createPos.z },
                    rotation = new Vec4 { x = createRot.x, y = createRot.y, z = createRot.z, w = createRot.w }
                };

                // Resolution from descriptor if available
                if (descW > 0 && descH > 0)
                    meta.resolution = new ResolutionInfo { w = descW, h = descH };
            }

            // Override resolution with actual GPU readback dimensions if available
            if (actualWidth > 0 && actualHeight > 0)
            {
                meta.resolution = new ResolutionInfo { w = actualWidth, h = actualHeight };
            }

            return meta;
        }

        // ═══════════════════════════════════════════════════════════════
        //  Buffer Management
        // ═══════════════════════════════════════════════════════════════

        private void EnsureRgbBuffers()
        {
            if (_rgbRt == null || _rgbRt.width != rgbOutputWidth || _rgbRt.height != rgbOutputHeight)
            {
                if (_rgbRt != null) { _rgbRt.Release(); Destroy(_rgbRt); }
                _rgbRt = new RenderTexture(rgbOutputWidth, rgbOutputHeight, 0, RenderTextureFormat.ARGB32)
                {
                    useMipMap = false,
                    autoGenerateMips = false,
                };
                _rgbRt.Create();
            }

            if (_rgbReadback == null || _rgbReadback.width != rgbOutputWidth || _rgbReadback.height != rgbOutputHeight)
            {
                if (_rgbReadback != null) Destroy(_rgbReadback);
                _rgbReadback = new Texture2D(rgbOutputWidth, rgbOutputHeight, TextureFormat.RGB24, false);
            }
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

            if (_depthReadback == null || _depthReadback.width != width || _depthReadback.height != height)
            {
                if (_depthReadback != null) Destroy(_depthReadback);
                _depthReadback = new Texture2D(width, height, TextureFormat.RFloat, false);
            }
        }

        // ═══════════════════════════════════════════════════════════════
        //  Permissions
        // ═══════════════════════════════════════════════════════════════

        private IEnumerator RequestPermissions()
        {
#if UNITY_ANDROID && !UNITY_EDITOR
            // Request camera permission (horizonos.permission.HEADSET_CAMERA)
            if (!HasCameraPermission())
            {
                Debug.Log("[RGBDCaptureTest] Requesting HEADSET_CAMERA permission...");
                var cameraCallbacks = new UnityEngine.Android.PermissionCallbacks();
                cameraCallbacks.PermissionGranted += _ => Debug.Log("[RGBDCaptureTest] HEADSET_CAMERA granted");
                cameraCallbacks.PermissionDenied += _ => Debug.LogWarning("[RGBDCaptureTest] HEADSET_CAMERA denied");
                cameraCallbacks.PermissionDeniedAndDontAskAgain += _ =>
                    Debug.LogWarning("[RGBDCaptureTest] HEADSET_CAMERA denied (don't ask again)");
                UnityEngine.Android.Permission.RequestUserPermission(CameraPermission, cameraCallbacks);
                yield return new WaitForSeconds(0.5f);
            }

            // Request scene permission (com.oculus.permission.USE_SCENE)
            if (!HasScenePermission())
            {
                Debug.Log("[RGBDCaptureTest] Requesting USE_SCENE permission...");
                var sceneCallbacks = new UnityEngine.Android.PermissionCallbacks();
                sceneCallbacks.PermissionGranted += _ => Debug.Log("[RGBDCaptureTest] USE_SCENE granted");
                sceneCallbacks.PermissionDenied += _ => Debug.LogWarning("[RGBDCaptureTest] USE_SCENE denied");
                sceneCallbacks.PermissionDeniedAndDontAskAgain += _ =>
                    Debug.LogWarning("[RGBDCaptureTest] USE_SCENE denied (don't ask again)");
                UnityEngine.Android.Permission.RequestUserPermission(ScenePermission, sceneCallbacks);
                yield return new WaitForSeconds(0.5f);
            }

            // Brief wait for permission dialogs to resolve
            yield return new WaitForSeconds(1.0f);
#else
            Debug.Log("[RGBDCaptureTest] Editor mode: skipping permission request");
            yield return null;
#endif
        }

        private static bool HasCameraPermission()
        {
#if UNITY_ANDROID && !UNITY_EDITOR
            return UnityEngine.Android.Permission.HasUserAuthorizedPermission(CameraPermission);
#else
            return true;
#endif
        }

        private static bool HasScenePermission()
        {
#if UNITY_ANDROID && !UNITY_EDITOR
            return UnityEngine.Android.Permission.HasUserAuthorizedPermission(ScenePermission);
#else
            return true;
#endif
        }

        // ═══════════════════════════════════════════════════════════════
        //  Log Collection
        // ═══════════════════════════════════════════════════════════════

        private void OnLogMessageReceived(string condition, string stackTrace, LogType type)
        {
            // Only collect our own logs and relevant warnings/errors
            if (condition.Contains("[RGBDCaptureTest]") ||
                condition.Contains("RGBDCaptureTest") ||
                (type == LogType.Error || type == LogType.Exception))
            {
                string entry = $"[{DateTime.UtcNow:yyyy-MM-dd HH:mm:ss.fff}] [{type}] {condition}";
                if (!string.IsNullOrEmpty(stackTrace) && type != LogType.Log)
                {
                    entry += $"\n  {stackTrace}";
                }
                _logEntries.Add(entry);
            }
        }

        private void SaveLogFile()
        {
            try
            {
                string logPath = Path.Combine(_outputRoot, "capture_log.txt");
                File.WriteAllLines(logPath, _logEntries);
                Debug.Log($"[RGBDCaptureTest] Log saved: {logPath} ({_logEntries.Count} entries)");
            }
            catch (Exception ex)
            {
                Debug.LogError($"[RGBDCaptureTest] Failed to save log: {ex.Message}");
            }
        }

        // ═══════════════════════════════════════════════════════════════
        //  HUD
        // ═══════════════════════════════════════════════════════════════

        private void CreateHUD()
        {
            _hudObject = new GameObject("RGBDCaptureHUD");
            _hudObject.transform.SetParent(transform);
            _hudText = _hudObject.AddComponent<TextMesh>();
            _hudText.fontSize = hudFontSize;
            _hudText.characterSize = 0.012f;
            _hudText.anchor = TextAnchor.MiddleCenter;
            _hudText.color = Color.green;
            _hudText.text = "RGBD Capture: Initializing...";
            _hudText.richText = true;
        }

        private void UpdateHUD(string message)
        {
            if (_hudText == null) return;

            // Color based on message content
            Color color = Color.green;
            if (message.StartsWith("ERROR"))
                color = Color.red;
            else if (message.StartsWith("Waiting"))
                color = Color.yellow;
            else if (message.StartsWith("Done"))
                color = new Color(0.3f, 1f, 0.3f);

            _hudText.text = message;
            _hudText.color = color;
        }

        private void UpdateHudPosition()
        {
            if (_hudObject == null) return;

            Camera cam = _xrCamera;
            if (cam == null)
            {
                cam = Camera.main;
                _xrCamera = cam;
            }

            if (cam == null) return;

            // Head-locked: position in front of the camera
            _hudObject.transform.position = cam.transform.position +
                cam.transform.forward * hudDistance +
                cam.transform.up * 0.15f;
            _hudObject.transform.LookAt(cam.transform);
            _hudObject.transform.Rotate(0, 180, 0); // face the user
        }

        // ═══════════════════════════════════════════════════════════════
        //  Reflection helper — avoids compile-time dependency on
        //  UnityEngine.XR.Oculus (which conflicts with OpenXR in v85)
        // ═══════════════════════════════════════════════════════════════

        private static void _TryReadDepthDescriptorReflected(
            out bool descValid,
            out float fovLeft, out float fovRight, out float fovTop, out float fovDown,
            out float nearZ, out float farZ, out float minD, out float maxD,
            out double createTime, out double predictedTime,
            out int descW, out int descH,
            out Vector3 createPos, out Quaternion createRot)
        {
            descValid = false;
            fovLeft = fovRight = fovTop = fovDown = 0;
            nearZ = farZ = minD = maxD = 0;
            createTime = predictedTime = 0;
            descW = descH = 0;
            createPos = Vector3.zero;
            createRot = Quaternion.identity;

            try
            {
                // Search all loaded assemblies for Utils type
                System.Type utilsType = null;
                foreach (var asm in System.AppDomain.CurrentDomain.GetAssemblies())
                {
                    utilsType = asm.GetType("UnityEngine.XR.Oculus.Utils");
                    if (utilsType != null)
                    {
                        Debug.Log($"[RGBDCaptureTest] Found Utils via assembly search: {asm.FullName}");
                        break;
                    }
                }

                if (utilsType == null)
                {
                    Debug.LogWarning("[RGBDCaptureTest] Utils type not found in any loaded assembly");
                    return;
                }

                var getDescMethod = utilsType.GetMethod("GetEnvironmentalDepthFrameDesc",
                    System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.Static);
                if (getDescMethod == null)
                {
                    Debug.LogWarning("[RGBDCaptureTest] GetEnvironmentalDepthFrameDesc method not found");
                    return;
                }

                object descObj = getDescMethod.Invoke(null, new object[] { 0 }); // eye=0
                if (descObj == null)
                {
                    Debug.LogWarning("[RGBDCaptureTest] GetEnvironmentalDepthFrameDesc returned null");
                    return;
                }

                var descType = descObj.GetType();

                fovLeft   = (float)descType.GetField("fovLeftAngle").GetValue(descObj);
                fovRight  = (float)descType.GetField("fovRightAngle").GetValue(descObj);
                fovTop    = (float)descType.GetField("fovTopAngle").GetValue(descObj);
                fovDown   = (float)descType.GetField("fovDownAngle").GetValue(descObj);
                nearZ     = (float)descType.GetField("nearZ").GetValue(descObj);
                farZ      = (float)descType.GetField("farZ").GetValue(descObj);
                minD      = (float)descType.GetField("minDepth").GetValue(descObj);
                maxD      = (float)descType.GetField("maxDepth").GetValue(descObj);
                createTime    = (double)descType.GetField("createTime").GetValue(descObj);
                predictedTime = (double)descType.GetField("predictedDisplayTime").GetValue(descObj);
                descW = (int)descType.GetField("width").GetValue(descObj);
                descH = (int)descType.GetField("height").GetValue(descObj);
                createPos = (Vector3)descType.GetField("createPoseLocation").GetValue(descObj);
                createRot = (Quaternion)descType.GetField("createPoseRotation").GetValue(descObj);

                descValid = true;
                Debug.Log($"[RGBDCaptureTest] Depth descriptor OK via reflection: fov=({fovLeft:F3},{fovRight:F3},{fovTop:F3},{fovDown:F3}), " +
                    $"pose=({createPos.x:F2},{createPos.y:F2},{createPos.z:F2}), depth_range=[{minD:F2},{maxD:F2}]m");
            }
            catch (Exception ex)
            {
                Debug.LogWarning($"[RGBDCaptureTest] Depth descriptor reflection failed: {ex.GetType().Name} - {ex.Message}");
            }
        }
    }
}
