using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using Meta.XR;
using Meta.XR.EnvironmentDepth;
using Unity.Collections;
using UnityEngine;

namespace SmartRoom.Testing
{
    /// <summary>
    /// SATURATION TEST: tries EVERY possible method to get depth camera World Pose in v85 SDK.
    /// ALL external types accessed via reflection — no compile-time dependency on Building Blocks.
    /// Captures 1 frame, runs all methods, logs results to persistentDataPath/depth_pose_test.log.
    /// Then captures 5 frames using the BEST available method.
    /// </summary>
    public class DepthPoseSaturationTest : MonoBehaviour
    {
        [Header("Config")]
        [SerializeField] private int captureFrames = 5;
        [SerializeField] private float frameInterval = 0.5f;
        [SerializeField] private int jpegQuality = 95;
        [SerializeField] private int rgbOutputWidth = 1280;
        [SerializeField] private int rgbOutputHeight = 1280;

        [Header("HUD")]
        [SerializeField] private float hudDistance = 1.5f;
        [SerializeField] private int hudFontSize = 48;

        // ── Runtime refs ──
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

        // ── State ──
        private string _outputRoot;
        private readonly List<string> _logLines = new List<string>();
        private int _captureIndex;

        // ── HUD ──
        private GameObject _hudObject;
        private TextMesh _hudText;

        // ── Cached depth descriptor data (refreshed from live descriptors during capture) ──
        private float _cachedFovLeft, _cachedFovRight, _cachedFovTop, _cachedFovBottom;
        private float _cachedNearZ = 0.1f, _cachedFarZ;
        private bool _hasCachedFov;
        private int _lastDepthTextureWidth;
        private int _lastDepthTextureHeight;
        private int _lastDepthTextureSlices;
        private string _lastDepthTextureDimension = "unknown";

        // ── Best method tracking ──
        private string _bestMethodName = "none";
        private int _bestMethodScore;
        private Pose _bestDepthPose;

        // ── DepthTextureAccess — fully via reflection ──
        private Component _dtaComponent;          // DepthTextureAccess instance
        private Delegate _dtaDelegate;            // OnDepthTextureUpdateCPU subscription
        private Pose _dtaPose;
        private bool _dtaPoseReceived;
        private bool _dtaSubscribed;
        private object _dtaLastFrameData;

        // ═══════════════════════════════════════════════════════════════
        //  Unity Lifecycle
        // ═══════════════════════════════════════════════════════════════

        private void Awake()
        {
            Application.logMessageReceived += OnLogMessageReceived;

            _pca = FindFirstObjectByType<PassthroughCameraAccess>();
            if (_pca != null)
            {
                _pca.RequestedResolution = new Vector2Int(1280, 1280);
                Log($"[INIT] PCA RequestedResolution set to 1280x1280");
                Log($"[INIT] PCA CameraPosition={_pca.CameraPosition}, selectedDepthEye={GetSelectedDepthEyeIndex()}");
            }
            _depthManager = FindFirstObjectByType<EnvironmentDepthManager>();
            _xrCamera = Camera.main;

            _depthShader = Shader.Find("Hidden/SmartRoom/DepthArraySliceToFloat");
            if (_depthShader == null)
                _depthShader = Shader.Find("Hidden/SmartRoom/DepthArraySliceToFloat_Resource");
            if (_depthShader == null)
                _depthShader = Resources.Load<Shader>("SmartRoomDepthArraySliceToFloat");
            if (_depthShader != null)
                _depthMaterial = new Material(_depthShader);

            _outputRoot = Path.Combine(Application.persistentDataPath, "rgbd_test");
            Directory.CreateDirectory(_outputRoot);

            // Find DepthTextureAccess via reflection (avoids compile-time dependency)
            TrySubscribeDepthTextureAccess();

            CreateHUD();
            Log($"[INIT] Saturation test ready. output={_outputRoot}");
        }

        /// <summary>
        /// Find DepthTextureAccess component using reflection and subscribe to OnDepthTextureUpdateCPU.
        /// This avoids compile-time dependency on Meta.XR.BuildingBlocks.AIBlocks assembly.
        /// </summary>
        private void TrySubscribeDepthTextureAccess()
        {
            try
            {
                // Find the type in any loaded assembly
                Type dtaType = null;
                foreach (var asm in AppDomain.CurrentDomain.GetAssemblies())
                {
                    dtaType = asm.GetType("Meta.XR.BuildingBlocks.AIBlocks.DepthTextureAccess");
                    if (dtaType != null) { Log($"[INIT] DepthTextureAccess type found in {asm.GetName().Name}"); break; }
                }

                if (dtaType == null)
                {
                    Log("[INIT] DepthTextureAccess type not in any loaded assembly (Building Blocks may not be in scene)");
                    return;
                }

                // Find instance in scene
                var instances = FindObjectsByType(dtaType, FindObjectsSortMode.None);
                if (instances == null || instances.Length == 0)
                {
                    Log("[INIT] DepthTextureAccess instance not in scene — add DepthTextureAccess Building Block to use Method A");
                    return;
                }

                _dtaComponent = (Component)instances[0];
                Log($"[INIT] DepthTextureAccess found: {_dtaComponent.name}");

                // Get the OnDepthTextureUpdateCPU event (Action<DepthFrameData>)
                var eventField = dtaType.GetField("OnDepthTextureUpdateCPU", BindingFlags.Public | BindingFlags.Instance);
                if (eventField == null) { Log("[INIT] OnDepthTextureUpdateCPU field not found"); return; }

                // Create delegate: use a generic shim to match Action<DepthFrameData> signature
                // OnDTAFrameCallback(object) doesn't match Action<T> due to contravariance in Mono
                var frameDataType = dtaType.GetNestedType("DepthFrameData", BindingFlags.Public);
                if (frameDataType == null) { Log("[INIT] DepthFrameData nested type not found"); return; }
                var actionType = typeof(Action<>).MakeGenericType(frameDataType);
                var genericMethod = typeof(DepthPoseSaturationTest).GetMethod("OnDTAFrameCallbackGeneric",
                    BindingFlags.NonPublic | BindingFlags.Instance);
                var typedMethod = genericMethod.MakeGenericMethod(frameDataType);
                _dtaDelegate = Delegate.CreateDelegate(actionType, this, typedMethod);

                // Subscribe: combine with existing delegate
                var existing = eventField.GetValue(_dtaComponent) as Delegate;
                eventField.SetValue(_dtaComponent, Delegate.Combine(existing, _dtaDelegate));
                _dtaSubscribed = true;

                Log("[INIT] Subscribed to DepthTextureAccess.OnDepthTextureUpdateCPU via reflection");
            }
            catch (Exception ex)
            {
                Log($"[INIT] DepthTextureAccess subscription failed: {ex.GetType().Name}: {ex.Message}");
            }
        }

        /// <summary>
        /// Generic shim for DepthTextureAccess.OnDepthTextureUpdateCPU callback.
        /// T will be DepthFrameData at runtime — this provides type-safe delegate creation.
        /// </summary>
        private void OnDTAFrameCallbackGeneric<T>(T frameData)
        {
            OnDTAFrameCallback(frameData);
        }

        /// <summary>
        /// Callback for DepthTextureAccess.OnDepthTextureUpdateCPU.
        /// The parameter is typed as object because DepthFrameData is not compile-time accessible.
        /// </summary>
        private void OnDTAFrameCallback(object frameDataObj)
        {
            if (frameDataObj == null) return;
            _dtaLastFrameData = frameDataObj;

            try
            {
                // Extract CameraPose from DepthFrameData via reflection
                var frameType = frameDataObj.GetType();
                var poseField = frameType.GetField("CameraPose", BindingFlags.Public | BindingFlags.Instance);
                if (poseField != null)
                {
                    _dtaPose = (Pose)poseField.GetValue(frameDataObj);
                    _dtaPoseReceived = true;
                }
            }
            catch { }
        }

        private void Start()
        {
            StartCoroutine(TestRoutine());
        }

        private void OnDestroy()
        {
            Application.logMessageReceived -= OnLogMessageReceived;

            // Unsubscribe DTA via reflection
            if (_dtaSubscribed && _dtaComponent != null && _dtaDelegate != null)
            {
                try
                {
                    var dtaType = _dtaComponent.GetType();
                    var eventField = dtaType.GetField("OnDepthTextureUpdateCPU", BindingFlags.Public | BindingFlags.Instance);
                    if (eventField != null)
                    {
                        var existing = eventField.GetValue(_dtaComponent) as Delegate;
                        if (existing != null)
                            eventField.SetValue(_dtaComponent, Delegate.Remove(existing, _dtaDelegate));
                    }
                }
                catch { }
            }

            SaveLogFile();

            if (_rgbRt != null) { _rgbRt.Release(); Destroy(_rgbRt); }
            if (_rgbReadback != null) Destroy(_rgbReadback);
            if (_depthRt != null) { _depthRt.Release(); Destroy(_depthRt); }
            if (_depthReadback != null) Destroy(_depthReadback);
            if (_depthMaterial != null) Destroy(_depthMaterial);
            if (_hudObject != null) Destroy(_hudObject);
        }

        // ═══════════════════════════════════════════════════════════════
        //  Main Test Routine
        // ═══════════════════════════════════════════════════════════════

        private IEnumerator TestRoutine()
        {
            UpdateHUD("Waiting permissions...");
            yield return StartCoroutine(RequestPermissions());

            if (_pca == null || !_pca.isActiveAndEnabled)
            {
                Log("[FATAL] PCA not available");
                UpdateHUD("FATAL: No PCA");
                yield break;
            }

            // Wait for PCA
            UpdateHUD("Waiting PCA...");
            float waitStart = Time.time;
            while (!_pca.IsPlaying && Time.time - waitStart < 10f) yield return null;
            if (!_pca.IsPlaying) { Log("[FATAL] PCA not playing"); yield break; }

            // Wait for depth
            if (_depthManager != null)
            {
                UpdateHUD("Waiting depth...");
                waitStart = Time.time;
                while (!_depthManager.IsDepthAvailable && Time.time - waitStart < 15f) yield return null;
                Log($"[READY] Depth available={_depthManager.IsDepthAvailable}");
            }

            // Give DepthTextureAccess time to fire
            yield return new WaitForSeconds(10.0f);

            // ═══════════════════════════════════════════════
            //  SATURATION TEST: Run ALL methods
            // ═══════════════════════════════════════════════
            UpdateHUD("Running saturation test...");
            Log("");
            Log("══════════════════════════════════════════════════");
            Log("  SATURATION TEST: Depth Camera Pose Methods");
            Log("══════════════════════════════════════════════════");

            Pose rgbPose = _pca.GetCameraPose();
            Log($"  RGB Pose (reference): pos=({rgbPose.position.x:F4},{rgbPose.position.y:F4},{rgbPose.position.z:F4}), rot=({rgbPose.rotation.eulerAngles.x:F1},{rgbPose.rotation.eulerAngles.y:F1},{rgbPose.rotation.eulerAngles.z:F1})");

            // ── Method A: DepthTextureAccess.OnDepthTextureUpdateCPU ──
            TestMethod_A_DepthTextureAccess(rgbPose);

            // ── Method B: OVRPlugin depth APIs (reflection scan) ──
            TestMethod_B_OVRPluginDepthAPIs(rgbPose);

            // ── Method C: Unity.XR.Oculus.Utils (correct namespace) reflection ──
            TestMethod_D_UnityXROculusUtils(rgbPose);

            // ── Method D: UnityEngine.XR.Oculus.Utils (old/wrong namespace, try anyway) ──
            TestMethod_E_UnityEngineXROculusUtils(rgbPose);

            // ── Method F: Scan ALL assemblies for Utils type ──
            TestMethod_F_AssemblyScanUtils(rgbPose);

            // ── Method G: Scan ALL assemblies for GetEnvironmentalDepthFrameDesc ──
            TestMethod_G_AssemblyScanMethod(rgbPose);

            // ── Method H: Shader globals _EnvironmentDepthReprojectionMatrices (improved extraction) ──
            TestMethod_H_ReprojectionMatrixImproved(rgbPose);

            // ── Method I: _EnvironmentDepthReprojectionMatrices + separate camera pose extraction ──
            TestMethod_I_ReprojectionPositionOnly(rgbPose);

            // ── Method J: EnvironmentDepthManager reflection for hidden members ──
            TestMethod_J_EnvDepthManagerReflection(rgbPose);

            // ── Method K: frameDescriptors[selectedEye] — internal descriptor array ──
            TestMethod_K_FrameDescriptors(rgbPose);

            Log("══════════════════════════════════════════════════");
            Log($"  BEST METHOD: {_bestMethodName} (score={_bestMethodScore})");
            Log("══════════════════════════════════════════════════");

            // Start frame capture

            _captureIndex = 0;
            yield return new WaitForSeconds(1.0f);

            while (_captureIndex < captureFrames)
            {
                CaptureSingleFrame();
                if (_captureIndex < captureFrames)
                    yield return new WaitForSeconds(frameInterval);
            }

            SaveLogFile();
            UpdateHUD($"Done! {captureFrames} frames + saturation log");
            Log($"[DONE] All data saved to {_outputRoot}");
        }

        // ═══════════════════════════════════════════════════════════════
        //  SATURATION TEST METHODS
        // ═══════════════════════════════════════════════════════════════

        // ── Method A: DepthTextureAccess ──
        private void TestMethod_A_DepthTextureAccess(Pose rgbPose)
        {
            Log("");
            Log("  [Method A] DepthTextureAccess.OnDepthTextureUpdateCPU (reflection)");
            if (!_dtaSubscribed)
            {
                Log("    SKIP: DepthTextureAccess not subscribed (type not found or instance not in scene)");
                return;
            }
            if (!_dtaPoseReceived)
            {
                Log("    FAIL: No depth frame received from DTA yet (may need longer wait)");
                return;
            }
            Log($"    → pose pos=({_dtaPose.position.x:F4},{_dtaPose.position.y:F4},{_dtaPose.position.z:F4})");
            Log($"    → pose rot euler=({_dtaPose.rotation.eulerAngles.x:F1},{_dtaPose.rotation.eulerAngles.y:F1},{_dtaPose.rotation.eulerAngles.z:F1})");

            // Also extract VP matrix and pixel count from frame data
            if (_dtaLastFrameData != null)
            {
                try
                {
                    var fwType = _dtaLastFrameData.GetType();
                    var vpField = fwType.GetField("ViewProjectionMatrix", BindingFlags.Public | BindingFlags.Instance);
                    if (vpField != null)
                    {
                        var vpObj = vpField.GetValue(_dtaLastFrameData);
                        if (vpObj is Matrix4x4[] vpArr)
                            Log($"    → VP matrices: {vpArr.Length} (first: {vpArr[0]})");
                    }
                    var pxField = fwType.GetField("DepthTexturePixels", BindingFlags.Public | BindingFlags.Instance);
                    if (pxField != null)
                    {
                        var pxObj = pxField.GetValue(_dtaLastFrameData);
                        if (pxObj is NativeArray<float> na)
                            Log($"    → DepthTexturePixels length={na.Length}");
                    }
                }
                catch (Exception ex) { Log($"    → frame data inspection error: {ex.Message}"); }
            }

            int score = ScoreMethod(rgbPose, _dtaPose, "DepthTextureAccess");
            if (score > _bestMethodScore)
            {
                _bestMethodScore = score;
                _bestMethodName = "DepthTextureAccess";
                _bestDepthPose = _dtaPose;
            }
            Log($"    score={score} ✓ PASS");
        }

        // ── Method B: OVRPlugin depth APIs (reflection — multiple possible API surfaces) ──
        private void TestMethod_B_OVRPluginDepthAPIs(Pose rgbPose)
        {
            Log("");
            Log("  [Method B] OVRPlugin depth APIs — reflection, multiple entry points");

            // In v85 Meta XR Core SDK, the depth pose may come from different API surfaces:
            // 1. OVRPlugin.GetEnvironmentDepthFrameDesc(eye) — Oculus XR Plugin era API
            // 2. OVRPlugin.GetEnvironmentDepthFrameDesc — same but different signature
            // 3. OVRPlugin.occlusionPlugin — internal plugin
            // 4. Unity.XR.Oculus.Utils.GetEnvironmentalDepthFrameDesc — Oculus XR Plugin
            // 5. Some other static method on OVRPlugin

            var ovrType = typeof(OVRPlugin);
            bool found = false;

            // Try B1: GetEnvironmentDepthFrameDesc(int eye) — most common signature
            foreach (var m in ovrType.GetMethods(BindingFlags.Public | BindingFlags.Static | BindingFlags.NonPublic))
            {
                if (!m.Name.Contains("GetEnvironmentDepthFrameDesc") && !m.Name.Contains("GetDepth"))
                    continue;

                var parms = m.GetParameters();
                Log($"    → Found OVRPlugin.{m.Name}({string.Join(",", parms.Select(p => $"{p.ParameterType.Name} {p.Name}"))})");

                try
                {
                    object[] args;
                    if (parms.Length == 1 && parms[0].ParameterType == typeof(int))
                        args = new object[] { GetSelectedDepthEyeIndex() };
                    else if (parms.Length == 0)
                        args = new object[0];
                    else
                        continue;

                    var result = m.Invoke(null, args);
                    if (result == null) { Log($"      returned null"); continue; }

                    var resType = result.GetType();
                    Log($"      return type: {resType.FullName}");

                    // Check for isValid
                    var isValidField = resType.GetField("isValid");
                    bool hasIsValid = isValidField != null;
                    if (hasIsValid)
                    {
                        bool isValid = (bool)isValidField.GetValue(result);
                        Log($"      isValid={isValid}");
                        if (!isValid) { Log($"      (frame not valid, skipping)"); continue; }
                    }

                    // Try to extract pose fields
                    Pose extractedPose;
                    if (TryExtractPoseFromObject(result, resType, out extractedPose))
                    {
                        found = true;
                        Pose dp = extractedPose;
                        Log($"      → pose pos=({dp.position.x:F4},{dp.position.y:F4},{dp.position.z:F4})");
                        Log($"      → pose rot euler=({dp.rotation.eulerAngles.x:F1},{dp.rotation.eulerAngles.y:F1},{dp.rotation.eulerAngles.z:F1})");
                        int score = ScoreMethod(rgbPose, dp, $"OVRPlugin.{m.Name}");
                        if (score > _bestMethodScore)
                        {
                            _bestMethodScore = score;
                            _bestMethodName = $"OVRPlugin.{m.Name}";
                            _bestDepthPose = dp;
                        }
                        Log($"      score={score} ✓");
                    }
                    else
                    {
                        Log($"      (no pose fields found in return type)");
                    }
                }
                catch (Exception ex)
                {
                    Log($"      invoke failed: {ex.GetType().Name}: {ex.Message}");
                }
            }

            if (!found) Log($"    No working OVRPlugin depth method found");
        }

        /// <summary>
        /// Try to extract (position, rotation) from an arbitrary struct/object.
        /// Returns true if extraction succeeded.
        /// </summary>
        private bool TryExtractPoseFromObject(object obj, Type objType, out Pose pose)
        {
            pose = Pose.identity;

            // Try createPoseLocation / createPoseRotation
            // NOTE: these are internal fields in Meta SDK, need NonPublic binding
            var bf = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance;
            var posField = objType.GetField("createPoseLocation", bf);
            var rotField = objType.GetField("createPoseRotation", bf);
            if (posField != null && rotField != null)
            {
                Vector3 pos = (Vector3)posField.GetValue(obj);
                var rotVal = rotField.GetValue(obj);
                string rotTypeName = (rotVal != null) ? rotVal.GetType().Name : "null";
                Log($"      [TryExtract] rotVal type={rotTypeName}, value={rotVal}");

                // Try Quaternion cast
                try
                {
                    if (rotVal is Quaternion q)
                    {
                        pose = new Pose(pos, q);
                        return true;
                    }
                }
                catch (Exception ex) { Log($"      [TryExtract] Quaternion cast failed: {ex.Message}"); }

                // Try Vector4 cast
                try
                {
                    if (rotVal is Vector4 v4)
                    {
                        pose = new Pose(pos, new Quaternion(v4.x, v4.y, v4.z, v4.w));
                        return true;
                    }
                }
                catch (Exception ex) { Log($"      [TryExtract] Vector4 cast failed: {ex.Message}"); }

                // Last resort: try hard cast to Quaternion
                try
                {
                    Quaternion q = (Quaternion)rotVal;
                    pose = new Pose(pos, q);
                    return true;
                }
                catch (Exception ex) { Log($"      [TryExtract] hard Quaternion cast: {ex.Message}"); }

                // Last resort: try hard cast to Vector4
                try
                {
                    Vector4 v4 = (Vector4)rotVal;
                    pose = new Pose(pos, new Quaternion(v4.x, v4.y, v4.z, v4.w));
                    return true;
                }
                catch (Exception ex) { Log($"      [TryExtract] hard Vector4 cast: {ex.Message}"); }

                Log($"      [TryExtract] ALL casts failed for createPoseRotation");
                return false;
            }

            // Try position / rotation (Unity Pose style)
            posField = objType.GetField("position", bf);
            rotField = objType.GetField("rotation", bf);
            if (posField != null && rotField != null)
            {
                pose = new Pose((Vector3)posField.GetValue(obj), (Quaternion)rotField.GetValue(obj));
                return true;
            }

            // Try CameraPose (DepthFrameData style)
            var poseField = objType.GetField("CameraPose");
            if (poseField != null)
            {
                var poseVal = poseField.GetValue(obj);
                if (poseVal is Pose p) { pose = p; return true; }
            }

            return false;
        }

        // ── Method C: OVRPlugin via reflection ──
        // ── Method C: Unity.XR.Oculus.Utils (correct namespace) ──
        private void TestMethod_D_UnityXROculusUtils(Pose rgbPose)
        {
            Log("");
            Log("  [Method D] Unity.XR.Oculus.Utils — correct namespace, Oculus XR Plugin");
            try
            {
                Type utilsType = null;
                foreach (var asm in AppDomain.CurrentDomain.GetAssemblies())
                {
                    utilsType = asm.GetType("Unity.XR.Oculus.Utils");
                    if (utilsType != null) { Log($"    Found in: {asm.FullName}"); break; }
                }

                if (utilsType == null)
                {
                    Log("    SKIP: Unity.XR.Oculus.Utils not in any loaded assembly (OpenXR routing)");
                    return;
                }

                var method = utilsType.GetMethod("GetEnvironmentalDepthFrameDesc",
                    BindingFlags.Public | BindingFlags.Static);
                if (method == null) { Log("    FAIL: method not found"); return; }

                var descObj = method.Invoke(null, new object[] { GetSelectedDepthEyeIndex() });
                if (descObj == null) { Log("    FAIL: returned null"); return; }

                var descType = descObj.GetType();
                bool isValid = (bool)descType.GetField("isValid").GetValue(descObj);
                if (isValid)
                {
                    Vector3 pos = (Vector3)descType.GetField("createPoseLocation").GetValue(descObj);
                    Vector4 rotV4 = (Vector4)descType.GetField("createPoseRotation").GetValue(descObj);
                    Pose depthPose = new Pose(pos, new Quaternion(rotV4.x, rotV4.y, rotV4.z, rotV4.w));
                    Log($"    → pose pos=({pos.x:F4},{pos.y:F4},{pos.z:F4})");
                    Log($"    → pose rot euler=({depthPose.rotation.eulerAngles.x:F1},{depthPose.rotation.eulerAngles.y:F1},{depthPose.rotation.eulerAngles.z:F1})");

                    int score = ScoreMethod(rgbPose, depthPose, "Unity.XR.Oculus.Utils");
                    if (score > _bestMethodScore)
                    {
                        _bestMethodScore = score;
                        _bestMethodName = "Unity.XR.Oculus.Utils";
                        _bestDepthPose = depthPose;
                    }
                    Log($"    score={score} ✓ PASS");
                }
                else { Log("    FAIL: isValid=false"); }
            }
            catch (Exception ex)
            {
                Log($"    FAIL: {ex.GetType().Name}: {ex.Message}");
            }
        }

        // ── Method E: UnityEngine.XR.Oculus.Utils (OLD wrong namespace, try anyway) ──
        private void TestMethod_E_UnityEngineXROculusUtils(Pose rgbPose)
        {
            Log("");
            Log("  [Method E] UnityEngine.XR.Oculus.Utils — old/wrong namespace, try anyway");
            try
            {
                Type utilsType = null;
                foreach (var asm in AppDomain.CurrentDomain.GetAssemblies())
                {
                    utilsType = asm.GetType("UnityEngine.XR.Oculus.Utils");
                    if (utilsType != null) { Log($"    Found in: {asm.FullName}"); break; }
                }
                if (utilsType == null) { Log("    SKIP: not found (expected in OpenXR routing)"); return; }

                var method = utilsType.GetMethod("GetEnvironmentalDepthFrameDesc",
                    BindingFlags.Public | BindingFlags.Static);
                if (method == null) { Log("    FAIL: method not found"); return; }

                var descObj = method.Invoke(null, new object[] { GetSelectedDepthEyeIndex() });
                var descType = descObj.GetType();
                bool isValid = (bool)descType.GetField("isValid").GetValue(descObj);
                if (isValid)
                {
                    Vector3 pos = (Vector3)descType.GetField("createPoseLocation").GetValue(descObj);
                    Vector4 rotV4 = (Vector4)descType.GetField("createPoseRotation").GetValue(descObj);
                    Pose depthPose = new Pose(pos, new Quaternion(rotV4.x, rotV4.y, rotV4.z, rotV4.w));
                    int score = ScoreMethod(rgbPose, depthPose, "UnityEngine.XR.Oculus.Utils");
                    if (score > _bestMethodScore)
                    {
                        _bestMethodScore = score;
                        _bestMethodName = "UnityEngine.XR.Oculus.Utils";
                        _bestDepthPose = depthPose;
                    }
                    Log($"    score={score} ✓ PASS (surprising!)");
                }
            }
            catch (Exception ex) { Log($"    FAIL: {ex.GetType().Name}: {ex.Message}"); }
        }

        // ── Method F: Scan ALL assemblies for ANY type named "Utils" ──
        private void TestMethod_F_AssemblyScanUtils(Pose rgbPose)
        {
            Log("");
            Log("  [Method F] Full assembly scan: ANY type named 'Utils' with GetEnvironmentalDepthFrameDesc");
            int found = 0;
            try
            {
                foreach (var asm in AppDomain.CurrentDomain.GetAssemblies())
                {
                    foreach (var t in asm.GetTypes())
                    {
                        if (t.Name == "Utils" || t.FullName.Contains("Oculus.Utils") || t.FullName.Contains("XR.Utils"))
                        {
                            var method = t.GetMethod("GetEnvironmentalDepthFrameDesc",
                                BindingFlags.Public | BindingFlags.Static | BindingFlags.NonPublic);
                            if (method != null)
                            {
                                found++;
                                Log($"    → Found: {t.FullName} in {asm.GetName().Name}");
                                try
                                {
                                    var descObj = method.Invoke(null, new object[] { GetSelectedDepthEyeIndex() });
                                    var descType = descObj.GetType();
                                    bool isValid = (bool)descType.GetField("isValid").GetValue(descObj);
                                    if (isValid)
                                    {
                                        Vector3 pos = (Vector3)descType.GetField("createPoseLocation").GetValue(descObj);
                                        Vector4 rotV4 = (Vector4)descType.GetField("createPoseRotation").GetValue(descObj);
                                        Pose depthPose = new Pose(pos, new Quaternion(rotV4.x, rotV4.y, rotV4.z, rotV4.w));
                                        int score = ScoreMethod(rgbPose, depthPose, $"AssemblyScan:{t.FullName}");
                                        if (score > _bestMethodScore)
                                        {
                                            _bestMethodScore = score;
                                            _bestMethodName = $"AssemblyScan:{t.FullName}";
                                            _bestDepthPose = depthPose;
                                        }
                                        Log($"      score={score} ✓");
                                    }
                                    else { Log($"      isValid=false"); }
                                }
                                catch (Exception ex2) { Log($"      invoke failed: {ex2.Message}"); }
                            }
                        }
                    }
                }
                if (found == 0) Log("    No matching Utils types found");
            }
            catch (Exception ex) { Log($"    FAIL during scan: {ex.GetType().Name}: {ex.Message}"); }
        }

        // ── Method G: Scan for GetEnvironmentalDepthFrameDesc on ANY type ──
        private void TestMethod_G_AssemblyScanMethod(Pose rgbPose)
        {
            Log("");
            Log("  [Method G] Full assembly scan: ANY type with 'GetEnvironmentalDepthFrameDesc' method");
            int found = 0;
            try
            {
                foreach (var asm in AppDomain.CurrentDomain.GetAssemblies())
                {
                    try
                    {
                        foreach (var t in asm.GetTypes())
                        {
                            var method = t.GetMethod("GetEnvironmentalDepthFrameDesc",
                                BindingFlags.Public | BindingFlags.Static | BindingFlags.NonPublic);
                            if (method != null)
                            {
                                found++;
                                Log($"    → Found: {t.FullName}.GetEnvironmentalDepthFrameDesc in {asm.GetName().Name}");
                                try
                                {
                                    var descObj = method.Invoke(null, new object[] { GetSelectedDepthEyeIndex() });
                                    if (descObj != null)
                                    {
                                        var dt = descObj.GetType();
                                        bool isValid = (bool)dt.GetField("isValid").GetValue(descObj);
                                        Log($"      isValid={isValid}, type={dt.FullName}");
                                        if (isValid)
                                        {
                                            Vector3 pos = (Vector3)dt.GetField("createPoseLocation").GetValue(descObj);
                                            Vector4 rotV4 = (Vector4)dt.GetField("createPoseRotation").GetValue(descObj);
                                            Pose depthPose = new Pose(pos, new Quaternion(rotV4.x, rotV4.y, rotV4.z, rotV4.w));
                                            int score = ScoreMethod(rgbPose, depthPose, $"MethodScan:{t.FullName}");
                                            if (score > _bestMethodScore)
                                            {
                                                _bestMethodScore = score;
                                                _bestMethodName = $"MethodScan:{t.FullName}";
                                                _bestDepthPose = depthPose;
                                            }
                                            Log($"      score={score} ✓");
                                        }
                                    }
                                    else { Log($"      returned null"); }
                                }
                                catch (Exception ex2) { Log($"      invoke failed: {ex2.Message}"); }
                            }
                        }
                    }
                    catch (ReflectionTypeLoadException) { /* skip assemblies that fail to load types */ }
                }
                if (found == 0) Log("    No types with GetEnvironmentalDepthFrameDesc found");
                else Log($"    Total found: {found}");
            }
            catch (Exception ex) { Log($"    FAIL: {ex.GetType().Name}: {ex.Message}"); }
        }

        // ── Method H: Reprojection matrix — improved extraction ──
        private void TestMethod_H_ReprojectionMatrixImproved(Pose rgbPose)
        {
            Log("");
            Log("  [Method H] _EnvironmentDepthReprojectionMatrices — improved extraction");
            try
            {
                // Unity 6: GetGlobalMatrixArray uses List<Matrix4x4>, not Matrix4x4[]
                var mats = new System.Collections.Generic.List<Matrix4x4>();
                Shader.GetGlobalMatrixArray("_EnvironmentDepthReprojectionMatrices", mats);
                int eyeIndex = GetSelectedDepthEyeIndex();
                if (mats.Count <= eyeIndex) { Log($"    FAIL: matrix list too short for eye {eyeIndex}"); return; }
                var vpInv = mats[eyeIndex].inverse;
                // Extract camera position from VP_inv
                var camPos4 = vpInv * new Vector4(0, 0, 0, 1);
                Vector3 camPos = new Vector3(camPos4.x, camPos4.y, camPos4.z) / camPos4.w;

                // Extract column vectors for rotation (more robust than LookRotation)
                // VP = P * V, so V = P^-1 * VP
                // But we don't have P separately. Instead, extract the view matrix columns:
                // V_inv (= camera world matrix) columns are the camera's world-space axes
                Vector3 right = new Vector3(vpInv.m00, vpInv.m10, vpInv.m20).normalized;
                Vector3 up = new Vector3(vpInv.m01, vpInv.m11, vpInv.m21).normalized;
                Vector3 forward = new Vector3(vpInv.m02, vpInv.m12, vpInv.m22).normalized;

                // Build rotation from axes
                Quaternion camRot = Quaternion.LookRotation(forward, up);

                Log($"    pos=({camPos.x:F4},{camPos.y:F4},{camPos.z:F4})");
                Log($"    rot euler=({camRot.eulerAngles.x:F1},{camRot.eulerAngles.y:F1},{camRot.eulerAngles.z:F1})");
                Log($"    right=({right.x:F3},{right.y:F3},{right.z:F3})");
                Log($"    up=({up.x:F3},{up.y:F3},{up.z:F3})");
                Log($"    forward=({forward.x:F3},{forward.y:F3},{forward.z:F3})");

                Pose depthPose = new Pose(camPos, camRot);
                int score = ScoreMethod(rgbPose, depthPose, "ReprojectionMatrixImproved");
                Log($"    score={score} (comparison only — not reliable for extrinsics)");

                // Also try just the position (ignore rotation)
                float baseline = Vector3.Distance(rgbPose.position, camPos);
                Log($"    baseline to RGB camera: {baseline*100:F1}cm");
            }
            catch (Exception ex) { Log($"    FAIL: {ex.GetType().Name}: {ex.Message}"); }
        }

        // ── Method I: Reprojection position only (assume same rotation as RGB) ──
        private void TestMethod_I_ReprojectionPositionOnly(Pose rgbPose)
        {
            Log("");
            Log("  [Method I] _EnvironmentDepthReprojectionMatrices — position only, use RGB rotation");
            try
            {
                var mats = new System.Collections.Generic.List<Matrix4x4>();
                Shader.GetGlobalMatrixArray("_EnvironmentDepthReprojectionMatrices", mats);
                int eyeIndex = GetSelectedDepthEyeIndex();
                if (mats.Count <= eyeIndex) { Log($"    FAIL: matrix list too short for eye {eyeIndex}"); return; }
                var vpInv = mats[eyeIndex].inverse;
                var camPos4 = vpInv * new Vector4(0, 0, 0, 1);
                Vector3 camPos = new Vector3(camPos4.x, camPos4.y, camPos4.z) / camPos4.w;
                Pose depthPose = new Pose(camPos, rgbPose.rotation);

                float baseline = Vector3.Distance(rgbPose.position, camPos);
                Log($"    pos=({camPos.x:F4},{camPos.y:F4},{camPos.z:F4})");
                Log($"    using RGB rotation: euler=({rgbPose.rotation.eulerAngles.x:F1},{rgbPose.rotation.eulerAngles.y:F1},{rgbPose.rotation.eulerAngles.z:F1})");
                Log($"    baseline={baseline*100:F1}cm");
                Log($"    NOTE: assumes depth sensor is coaxial with RGB (rotation may differ slightly)");
            }
            catch (Exception ex) { Log($"    FAIL: {ex.GetType().Name}: {ex.Message}"); }
        }

        // ── Method J: EnvironmentDepthManager hidden members ──
        private void TestMethod_J_EnvDepthManagerReflection(Pose rgbPose)
        {
            Log("");
            Log("  [Method J] EnvironmentDepthManager — reflection for hidden pose members");
            try
            {
                if (_depthManager == null) { Log("    SKIP: no EnvironmentDepthManager"); return; }

                var type = _depthManager.GetType();
                Log($"    type={type.FullName}");

                // List ALL public fields/properties
                var fields = type.GetFields(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                var props = type.GetProperties(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                var methods = type.GetMethods(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance)
                    .Where(m => m.Name.ToLower().Contains("pose") || m.Name.ToLower().Contains("depth"));

                Log($"    Fields ({fields.Length}):");
                foreach (var f in fields.Take(20))
                    Log($"      {f.FieldType.Name} {f.Name}");

                Log($"    Pose-related methods ({methods.Count()}):");
                foreach (var m in methods.Take(15))
                    Log($"      {m.ReturnType.Name} {m.Name}({string.Join(",", m.GetParameters().Select(p => p.ParameterType.Name))})");

                // Check for any Pose-typed fields or CameraPose-like properties
                foreach (var p in props.Where(p => p.Name.ToLower().Contains("pose") || p.Name.ToLower().Contains("camera")))
                {
                    try
                    {
                        var val = p.GetValue(_depthManager);
                        Log($"    Property {p.Name}: {val?.GetType().Name ?? "null"} = {val}");
                    }
                    catch { Log($"    Property {p.Name}: getter failed"); }
                }
            }
            catch (Exception ex) { Log($"    FAIL: {ex.GetType().Name}: {ex.Message}"); }
        }

        // ── Method K: EnvironmentDepthManager.frameDescriptors[selectedEye] — direct descriptor array ──
        private void TestMethod_K_FrameDescriptors(Pose rgbPose)
        {
            Log("");
            Log($"  [Method K] EnvironmentDepthManager.frameDescriptors[selectedEye={GetSelectedDepthEyeIndex()}] — internal descriptor array");
            try
            {
                if (_depthManager == null) { Log("    SKIP: no EnvironmentDepthManager"); return; }

                var mgrType = _depthManager.GetType();
                var fdField = mgrType.GetField("frameDescriptors",
                    BindingFlags.NonPublic | BindingFlags.Instance);
                if (fdField == null) { Log("    FAIL: frameDescriptors field not found"); return; }

                var fdValue = fdField.GetValue(_depthManager);
                if (fdValue == null) { Log("    FAIL: frameDescriptors is null"); return; }

                var fdArray = fdValue as Array;
                if (fdArray == null || fdArray.Length == 0)
                {
                    Log($"    FAIL: frameDescriptors not array or empty (type={fdValue.GetType().Name})");
                    return;
                }

                int eyeIndex = GetSelectedDepthEyeIndex();
                if (fdArray.Length <= eyeIndex)
                {
                    Log($"    FAIL: frameDescriptors length {fdArray.Length} does not contain eye {eyeIndex}");
                    return;
                }

                Log($"    frameDescriptors.Length={fdArray.Length}, selectedEye={eyeIndex}");
                var fdObj = fdArray.GetValue(eyeIndex);
                if (fdObj == null) { Log($"    FAIL: frameDescriptors[{eyeIndex}] is null"); return; }

                var fdType = fdObj.GetType();
                Log($"    frameDescriptors[{eyeIndex}] type: {fdType.FullName}");

                // Dump ALL fields
                var fields = fdType.GetFields(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                Log($"    Fields ({fields.Length}):");
                foreach (var f in fields)
                {
                    try
                    {
                        var val = f.GetValue(fdObj);
                        string valStr = val?.ToString() ?? "null";
                        if (val is Vector3 v3) valStr = $"({v3.x:F4},{v3.y:F4},{v3.z:F4})";
                        else if (val is Quaternion q) valStr = $"({q.x:F4},{q.y:F4},{q.z:F4},{q.w:F4})";
                        else if (val is Vector4 v4) valStr = $"({v4.x:F4},{v4.y:F4},{v4.z:F4},{v4.w:F4})";
                        else if (val is Array arr) valStr = $"[{arr.Length}]";
                        Log($"      {f.FieldType.Name} {f.Name} = {valStr}");
                    }
                    catch { Log($"      {f.FieldType.Name} {f.Name} = ERROR"); }
                }

                // Cache FOV and depth params for frame capture
                try
                {
                    TryCacheDepthDescriptorFields(fdObj, fdType);
                    if (_hasCachedFov)
                        Log($"    Cached FOV: L={_cachedFovLeft:F3} R={_cachedFovRight:F3} T={_cachedFovTop:F3} B={_cachedFovBottom:F3} near={_cachedNearZ} far={_cachedFarZ}");
                }
                catch (Exception ex) { Log($"    FOV cache failed: {ex.Message}"); }

                Pose extractedPose;
                if (TryExtractPoseFromObject(fdObj, fdType, out extractedPose))
                {
                    Pose dp = extractedPose;
                    Log($"    → pose: pos=({dp.position.x:F4},{dp.position.y:F4},{dp.position.z:F4})");
                    Log($"    → rot euler=({dp.rotation.eulerAngles.x:F1},{dp.rotation.eulerAngles.y:F1},{dp.rotation.eulerAngles.z:F1})");
                    int score = ScoreMethod(rgbPose, dp, $"frameDescriptors[{eyeIndex}]");
                    if (score > _bestMethodScore)
                    {
                        _bestMethodScore = score;
                        _bestMethodName = $"frameDescriptors[{eyeIndex}]";
                        _bestDepthPose = dp;
                    }
                    Log($"    score={score}");
                }
                else
                {
                    Log("    (no pose fields in frame descriptor)");
                }
            }
            catch (Exception ex) { Log($"    FAIL: {ex.GetType().Name}: {ex.Message}"); }
        }

        // ═══════════════════════════════════════════════════════════════
        //  Scoring: how "reasonable" is this depth pose?
        // ═══════════════════════════════════════════════════════════════
        private int ScoreMethod(Pose rgbPose, Pose depthPose, string methodName)
        {
            int score = 0;
            float baseline = Vector3.Distance(rgbPose.position, depthPose.position);
            float angleDiff = Quaternion.Angle(rgbPose.rotation, depthPose.rotation);

            Log($"    baseline={baseline*100:F1}cm, angle_diff={angleDiff:F1}°");

            // Baseline: Quest 3 depth sensor is ~2-8cm from RGB camera
            if (baseline < 0.001f) { score += 5; Log("      baseline ≈ 0 (may be same sensor)"); }
            else if (baseline < 0.05f) { score += 4; Log("      baseline < 5cm (very tight, plausible)"); }
            else if (baseline < 0.10f) { score += 3; Log("      baseline < 10cm (plausible)"); }
            else if (baseline < 0.20f) { score += 2; Log("      baseline < 20cm (possible)"); }
            else { score += 1; Log("      baseline > 20cm (suspicious)"); }

            // Rotation: should be roughly coaxial (same direction)
            if (angleDiff < 5f) { score += 5; Log("      rotation diff < 5° (coaxial ✓)"); }
            else if (angleDiff < 15f) { score += 3; Log("      rotation diff < 15° (slightly off)"); }
            else if (angleDiff < 45f) { score += 1; Log("      rotation diff < 45° (may be different sensor orientation)"); }
            else { score += 0; Log("      rotation diff > 45° (likely wrong)"); }

            // Pose is non-zero
            if (depthPose.position.magnitude > 0.01f) score += 1;

            return score;
        }

        // ═══════════════════════════════════════════════════════════════
        //  Frame Capture (using best method)
        // ═══════════════════════════════════════════════════════════════

        private void CaptureSingleFrame()
        {
            int idx = _captureIndex;
            string frameDir = Path.Combine(_outputRoot, $"capture_{idx:D4}");
            Directory.CreateDirectory(frameDir);

            Log($"── Frame {idx+1}/{captureFrames} (unity_frame={Time.frameCount}) ──");

            // RGB
            bool rgbOk = TryCaptureRGB(out byte[] jpegBytes, out RgbMeta rgbMeta);

            // Depth (NDC)
            bool depthOk = TryCaptureDepthNDC(out byte[] ndcRaw);

            // Depth pose from the current descriptor/source
            Pose depthPose = Pose.identity;
            string poseSource = "none";
            bool poseOk = TryGetDepthPoseBestMethod(out depthPose, out poseSource);
            if (!poseOk)
                Log($"  WARN: no alignable current depth pose for frame {idx}");

            int selectedEye = GetSelectedDepthEyeIndex();
            Matrix4x4[] shaderReprojectionMatrices = GetEnvironmentDepthReprojectionMatrices();
            Matrix4x4 trackingSpaceWorldToLocal = GetTrackingSpaceWorldToLocalMatrix();
            Matrix4x4 descriptorReprojection = CalculateDescriptorReprojection(depthPose);

            // Build meta
            var meta = new CaptureMeta
            {
                capture_index = idx,
                unity_frame = Time.frameCount,
                timestamp_unix_ms = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                rgb = rgbMeta,
                depth = new DepthMeta
                {
                    is_valid = depthOk && poseOk,
                    pose_source = poseSource,
                    selected_eye = selectedEye,
                    texture_width = _lastDepthTextureWidth,
                    texture_height = _lastDepthTextureHeight,
                    texture_slices = _lastDepthTextureSlices,
                    texture_dimension = _lastDepthTextureDimension,
                    depth_values = "float32_raw_environment_depth_0_1",
                    depth_origin = "Graphics.Blit_Texture2DArray_slice_to_RFloat_ReadPixels",
                    pose_position_x = depthPose.position.x,
                    pose_position_y = depthPose.position.y,
                    pose_position_z = depthPose.position.z,
                    pose_rotation_x = depthPose.rotation.x,
                    pose_rotation_y = depthPose.rotation.y,
                    pose_rotation_z = depthPose.rotation.z,
                    pose_rotation_w = depthPose.rotation.w,
                    resolution_w = 320, resolution_h = 320,
                    fov_left = _cachedFovLeft, fov_right = _cachedFovRight,
                    fov_top = _cachedFovTop, fov_bottom = _cachedFovBottom,
                    near_z = _cachedNearZ, far_z = _cachedFarZ,
                    zbuffer_x = Shader.GetGlobalVector("_EnvironmentDepthZBufferParams").x,
                    zbuffer_y = Shader.GetGlobalVector("_EnvironmentDepthZBufferParams").y,
                    zbuffer_z = Shader.GetGlobalVector("_EnvironmentDepthZBufferParams").z,
                    zbuffer_w = Shader.GetGlobalVector("_EnvironmentDepthZBufferParams").w,
                    reprojection_matrix = MatrixToRowMajorArray(shaderReprojectionMatrices[Mathf.Clamp(selectedEye, 0, shaderReprojectionMatrices.Length - 1)]),
                    reprojection_matrix_eye0 = MatrixToRowMajorArray(shaderReprojectionMatrices[0]),
                    reprojection_matrix_eye1 = MatrixToRowMajorArray(shaderReprojectionMatrices[1]),
                    tracking_space_world_to_local = MatrixToRowMajorArray(trackingSpaceWorldToLocal),
                    descriptor_reprojection_matrix = MatrixToRowMajorArray(descriptorReprojection),
                }
            };

            // Save
            string metaPath = Path.Combine(frameDir, "meta.json");
            File.WriteAllText(metaPath, JsonUtility.ToJson(meta, true));

            if (rgbOk && jpegBytes != null)
                File.WriteAllBytes(Path.Combine(frameDir, "rgb.jpg"), jpegBytes);

            if (depthOk && ndcRaw != null)
                File.WriteAllBytes(Path.Combine(frameDir, "depth.raw"), ndcRaw);

            Log($"  saved: rgb={(rgbOk?"OK":"FAIL")} depth={(depthOk?"OK":"FAIL")} pose_ok={(poseOk?"OK":"FAIL")} alignable={((depthOk && poseOk)?"YES":"NO")} pose_source={poseSource}");

            _captureIndex++;
        }

        private bool TryGetDepthPoseBestMethod(out Pose pose, out string source)
        {
            pose = Pose.identity;
            source = "none";

            // Priority: query the current OVR depth descriptor directly.
            if (TryGetCurrentOvrDepthPose(out pose, out source))
                return true;

            // Fallback: read a fresh pose from the current internal descriptor array.
            if (TryGetCurrentFrameDescriptorPose(out pose))
            {
                source = $"frameDescriptors[{GetSelectedDepthEyeIndex()}](current)";
                return true;
            }

            Log("  WARN: current depth pose unavailable; refusing async/cached fallback for capture");
            return false;
        }

        private bool TryGetCurrentFrameDescriptorPose(out Pose pose)
        {
            pose = Pose.identity;

            try
            {
                if (_depthManager == null)
                    return false;

                var mgrType = _depthManager.GetType();
                var fdField = mgrType.GetField("frameDescriptors",
                    BindingFlags.NonPublic | BindingFlags.Instance);
                if (fdField == null)
                    return false;

                var fdValue = fdField.GetValue(_depthManager);
                if (fdValue is not Array fdArray || fdArray.Length == 0)
                    return false;

                int eyeIndex = GetSelectedDepthEyeIndex();
                if (fdArray.Length <= eyeIndex)
                    return false;

                var fdObj = fdArray.GetValue(eyeIndex);
                if (fdObj == null)
                    return false;

                var fdType = fdObj.GetType();
                TryCacheDepthDescriptorFields(fdObj, fdType);
                return TryExtractPoseFromObject(fdObj, fdType, out pose);
            }
            catch
            {
                return false;
            }
        }

        private bool TryGetCurrentOvrDepthPose(out Pose pose, out string source)
        {
            pose = Pose.identity;
            source = "none";

            try
            {
                var ovrType = typeof(OVRPlugin);
                foreach (var m in ovrType.GetMethods(BindingFlags.Public | BindingFlags.Static | BindingFlags.NonPublic))
                {
                    if (!m.Name.Contains("GetEnvironmentDepthFrameDesc") && !m.Name.Contains("GetDepth"))
                        continue;
                    var parms = m.GetParameters();
                    if (parms.Length != 1 || parms[0].ParameterType != typeof(int)) continue;

                    var result = m.Invoke(null, new object[] { GetSelectedDepthEyeIndex() });
                    if (result == null) continue;

                    var resType = result.GetType();
                    var isValidField = resType.GetField("isValid");
                    if (isValidField != null && !(bool)isValidField.GetValue(result)) continue;

                    var extracted = TryExtractPoseFromObject(result, resType, out var extPose);
                    if (extracted)
                    {
                        TryCacheDepthDescriptorFields(result, resType);
                        pose = extPose;
                        source = $"OVRPlugin.{m.Name}(current)";
                        return true;
                    }
                }
            }
            catch { }

            return false;
        }

        private void TryCacheDepthDescriptorFields(object descriptorObj, Type descriptorType)
        {
            try
            {
                float? fovLeft = TryReadFloatField(descriptorType, descriptorObj, "fovLeftAngleTangent")
                    ?? TryReadFloatField(descriptorType, descriptorObj, "fovLeftAngle");
                float? fovRight = TryReadFloatField(descriptorType, descriptorObj, "fovRightAngleTangent")
                    ?? TryReadFloatField(descriptorType, descriptorObj, "fovRightAngle");
                float? fovTop = TryReadFloatField(descriptorType, descriptorObj, "fovTopAngleTangent")
                    ?? TryReadFloatField(descriptorType, descriptorObj, "fovTopAngle");
                float? fovBottom = TryReadFloatField(descriptorType, descriptorObj, "fovDownAngleTangent")
                    ?? TryReadFloatField(descriptorType, descriptorObj, "fovDownAngle");

                if (fovLeft.HasValue && fovRight.HasValue && fovTop.HasValue && fovBottom.HasValue)
                {
                    _cachedFovLeft = fovLeft.Value;
                    _cachedFovRight = fovRight.Value;
                    _cachedFovTop = fovTop.Value;
                    _cachedFovBottom = fovBottom.Value;
                    _hasCachedFov = true;
                }

                float? nearZ = TryReadFloatField(descriptorType, descriptorObj, "nearZ");
                if (nearZ.HasValue)
                    _cachedNearZ = nearZ.Value;

                float? farZ = TryReadFloatField(descriptorType, descriptorObj, "farZ");
                if (farZ.HasValue)
                    _cachedFarZ = farZ.Value;
            }
            catch
            {
                // Best-effort only; capture can continue with older cached values.
            }
        }

        private float? TryReadFloatField(Type type, object obj, string fieldName)
        {
            var field = type.GetField(fieldName, BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
            if (field == null)
                return null;

            object value = field.GetValue(obj);
            return value is float f ? f : null;
        }

        // ═══════════════════════════════════════════════════════════════
        //  RGB Capture (unchanged from original)
        // ═══════════════════════════════════════════════════════════════

        private bool TryCaptureRGB(out byte[] jpegBytes, out RgbMeta meta)
        {
            jpegBytes = null;
            meta = new RgbMeta();

            if (_pca == null || !_pca.isActiveAndEnabled || !_pca.IsPlaying) return false;
            Texture source = _pca.GetTexture();
            if (source == null) return false;

            Vector2Int requestedResolution = _pca.RequestedResolution;
            Vector2Int currentResolution = _pca.CurrentResolution;
            int captureWidth = source.width > 0 ? source.width : currentResolution.x;
            int captureHeight = source.height > 0 ? source.height : currentResolution.y;
            if (captureWidth <= 0 || captureHeight <= 0)
            {
                Log("  RGB capture error: source/current resolution invalid");
                return false;
            }

            EnsureRgbBuffers(captureWidth, captureHeight);

            RenderTexture previous = RenderTexture.active;
            try
            {
                Graphics.Blit(source, _rgbRt);
                RenderTexture.active = _rgbRt;
                _rgbReadback.ReadPixels(new Rect(0, 0, captureWidth, captureHeight), 0, 0, false);
                _rgbReadback.Apply(false, false);
                jpegBytes = _rgbReadback.EncodeToJPG(jpegQuality);
            }
            catch (Exception ex)
            {
                Log($"  RGB readback error: {ex.Message}");
                return false;
            }
            finally { RenderTexture.active = previous; }

            if (jpegBytes == null || jpegBytes.Length == 0) return false;

            var intrinsics = _pca.Intrinsics;
            Pose camPose = _pca.GetCameraPose();
            float scaleX = intrinsics.SensorResolution.x > 0 ? (float)captureWidth / intrinsics.SensorResolution.x : 1f;
            float scaleY = intrinsics.SensorResolution.y > 0 ? (float)captureHeight / intrinsics.SensorResolution.y : 1f;

            if (currentResolution.x > 0 && currentResolution.y > 0 &&
                (currentResolution.x != captureWidth || currentResolution.y != captureHeight))
            {
                Log($"  RGB resolution warning: source={captureWidth}x{captureHeight}, current={currentResolution.x}x{currentResolution.y}");
            }

            meta = new RgbMeta
            {
                timestamp_ticks = _pca.Timestamp.Ticks,
                resolution_w = captureWidth,
                resolution_h = captureHeight,
                requested_resolution_w = requestedResolution.x,
                requested_resolution_h = requestedResolution.y,
                current_resolution_w = currentResolution.x,
                current_resolution_h = currentResolution.y,
                source_resolution_w = captureWidth,
                source_resolution_h = captureHeight,
                camera_position = _pca.CameraPosition.ToString(),
                selected_depth_eye = GetSelectedDepthEyeIndex(),
                sensor_resolution_w = intrinsics.SensorResolution.x,
                sensor_resolution_h = intrinsics.SensorResolution.y,
                focal_length_x = intrinsics.FocalLength.x * scaleX,
                focal_length_y = intrinsics.FocalLength.y * scaleY,
                principal_point_x = intrinsics.PrincipalPoint.x * scaleX,
                principal_point_y = intrinsics.PrincipalPoint.y * scaleY,
                sensor_focal_length_x = intrinsics.FocalLength.x,
                sensor_focal_length_y = intrinsics.FocalLength.y,
                sensor_principal_point_x = intrinsics.PrincipalPoint.x,
                sensor_principal_point_y = intrinsics.PrincipalPoint.y,
                pose_position_x = camPose.position.x,
                pose_position_y = camPose.position.y,
                pose_position_z = camPose.position.z,
                pose_rotation_x = camPose.rotation.x,
                pose_rotation_y = camPose.rotation.y,
                pose_rotation_z = camPose.rotation.z,
                pose_rotation_w = camPose.rotation.w,
            };
            return true;
        }

        // ═══════════════════════════════════════════════════════════════
        //  Depth Capture (NDC — unchanged from original)
        // ═══════════════════════════════════════════════════════════════

        private bool TryCaptureDepthNDC(out byte[] ndcRaw)
        {
            ndcRaw = null;
            _lastDepthTextureWidth = 0;
            _lastDepthTextureHeight = 0;
            _lastDepthTextureSlices = 0;
            _lastDepthTextureDimension = "unknown";

            if (_depthManager == null || !_depthManager.IsDepthAvailable) return false;
            if (_depthMaterial == null) return false;
            if (!SystemInfo.SupportsRenderTextureFormat(RenderTextureFormat.RFloat)) return false;

            Texture sourceDepth = Shader.GetGlobalTexture("_EnvironmentDepthTexture");
            if (sourceDepth == null || sourceDepth.dimension != UnityEngine.Rendering.TextureDimension.Tex2DArray)
                return false;

            int dw = sourceDepth.width, dh = sourceDepth.height;
            if (dw <= 0 || dh <= 0) return false;
            _lastDepthTextureWidth = dw;
            _lastDepthTextureHeight = dh;
            _lastDepthTextureDimension = sourceDepth.dimension.ToString();
            RenderTexture sourceRt = sourceDepth as RenderTexture;
            _lastDepthTextureSlices = sourceRt != null ? sourceRt.volumeDepth : 0;

            EnsureDepthBuffers(dw, dh);
            _depthMaterial.SetTexture("_SourceDepthArray", sourceDepth);
            _depthMaterial.SetFloat("_ArraySlice", (float)GetSelectedDepthEyeIndex());
            Graphics.Blit(null, _depthRt, _depthMaterial);

            RenderTexture previous = RenderTexture.active;
            RenderTexture.active = _depthRt;
            try
            {
                _depthReadback.ReadPixels(new Rect(0, 0, dw, dh), 0, 0, false);
                _depthReadback.Apply(false, false);
            }
            catch { return false; }
            finally { RenderTexture.active = previous; }

            NativeArray<float> floatData = _depthReadback.GetRawTextureData<float>();
            if (!floatData.IsCreated || floatData.Length == 0) return false;

            float[] ndcFloats = new float[dw * dh];
            floatData.CopyTo(ndcFloats);
            ndcRaw = new byte[dw * dh * sizeof(float)];
            Buffer.BlockCopy(ndcFloats, 0, ndcRaw, 0, ndcRaw.Length);
            return true;
        }

        // ═══════════════════════════════════════════════════════════════
        //  Permissions, HUD, Buffer, Log (unchanged)
        // ═══════════════════════════════════════════════════════════════

        private IEnumerator RequestPermissions()
        {
#if UNITY_ANDROID && !UNITY_EDITOR
            if (!HasCameraPermission())
            {
                UnityEngine.Android.Permission.RequestUserPermission("horizonos.permission.HEADSET_CAMERA");
                yield return new WaitForSeconds(0.5f);
            }
            if (!HasScenePermission())
            {
                UnityEngine.Android.Permission.RequestUserPermission("com.oculus.permission.USE_SCENE");
                yield return new WaitForSeconds(0.5f);
            }
            yield return new WaitForSeconds(1.0f);
#endif
            yield return null;
        }

        private static bool HasCameraPermission()
        {
#if UNITY_ANDROID && !UNITY_EDITOR
            return UnityEngine.Android.Permission.HasUserAuthorizedPermission("horizonos.permission.HEADSET_CAMERA");
#else
            return true;
#endif
        }

        private static bool HasScenePermission()
        {
#if UNITY_ANDROID && !UNITY_EDITOR
            return UnityEngine.Android.Permission.HasUserAuthorizedPermission("com.oculus.permission.USE_SCENE");
#else
            return true;
#endif
        }

        private void EnsureRgbBuffers(int width, int height)
        {
            if (_rgbRt == null || _rgbRt.width != width || _rgbRt.height != height)
            {
                if (_rgbRt != null) { _rgbRt.Release(); Destroy(_rgbRt); }
                _rgbRt = new RenderTexture(width, height, 0, RenderTextureFormat.ARGB32)
                { useMipMap = false, autoGenerateMips = false };
                _rgbRt.Create();
            }
            if (_rgbReadback == null || _rgbReadback.width != width || _rgbReadback.height != height)
            {
                if (_rgbReadback != null) Destroy(_rgbReadback);
                _rgbReadback = new Texture2D(width, height, TextureFormat.RGB24, false);
            }
        }

        private int GetSelectedDepthEyeIndex()
        {
            if (_pca == null)
                return 0;

            return string.Equals(_pca.CameraPosition.ToString(), "Right", StringComparison.OrdinalIgnoreCase) ? 1 : 0;
        }

        private Matrix4x4[] GetEnvironmentDepthReprojectionMatrices()
        {
            Matrix4x4[] mats = new Matrix4x4[2];
            try
            {
                List<Matrix4x4> matrixList = new List<Matrix4x4>(2);
                Shader.GetGlobalMatrixArray("_EnvironmentDepthReprojectionMatrices", matrixList);
                for (int i = 0; i < Mathf.Min(mats.Length, matrixList.Count); i++)
                    mats[i] = matrixList[i];
            }
            catch (Exception ex)
            {
                Log($"  WARN: failed to read _EnvironmentDepthReprojectionMatrices: {ex.Message}");
                mats[0] = Matrix4x4.identity;
                mats[1] = Matrix4x4.identity;
            }

            return mats;
        }

        private Matrix4x4 GetTrackingSpaceWorldToLocalMatrix()
        {
            if (_depthManager != null && _depthManager.CustomTrackingSpace != null)
                return _depthManager.CustomTrackingSpace.worldToLocalMatrix;

            OVRCameraRig cameraRig = FindFirstObjectByType<OVRCameraRig>();
            Transform trackingSpace = cameraRig != null ? cameraRig.trackingSpace : null;
            return trackingSpace != null ? trackingSpace.worldToLocalMatrix : Matrix4x4.identity;
        }

        private Matrix4x4 CalculateDescriptorReprojection(Pose depthPose)
        {
            if (!_hasCachedFov)
                return Matrix4x4.identity;

            float near = _cachedNearZ;
            float far = _cachedFarZ;
            float x = 2.0f / (_cachedFovRight + _cachedFovLeft);
            float y = 2.0f / (_cachedFovTop + _cachedFovBottom);
            float a = (_cachedFovRight - _cachedFovLeft) / (_cachedFovRight + _cachedFovLeft);
            float b = (_cachedFovTop - _cachedFovBottom) / (_cachedFovTop + _cachedFovBottom);
            float c;
            float d;
            if (float.IsInfinity(far) || far < near)
            {
                c = -1.0f;
                d = -2.0f * near;
            }
            else
            {
                c = -(far + near) / (far - near);
                d = -(2.0f * far * near) / (far - near);
            }

            Matrix4x4 projection = new Matrix4x4
            {
                m00 = x,
                m01 = 0,
                m02 = a,
                m03 = 0,
                m10 = 0,
                m11 = y,
                m12 = b,
                m13 = 0,
                m20 = 0,
                m21 = 0,
                m22 = c,
                m23 = d,
                m30 = 0,
                m31 = 0,
                m32 = -1.0f,
                m33 = 0
            };
            Matrix4x4 view = Matrix4x4.TRS(depthPose.position, depthPose.rotation, new Vector3(1, 1, -1)).inverse;
            return projection * view;
        }

        private float[] MatrixToRowMajorArray(Matrix4x4 m)
        {
            return new[]
            {
                m.m00, m.m01, m.m02, m.m03,
                m.m10, m.m11, m.m12, m.m13,
                m.m20, m.m21, m.m22, m.m23,
                m.m30, m.m31, m.m32, m.m33
            };
        }

        private void EnsureDepthBuffers(int w, int h)
        {
            if (_depthRt == null || _depthRt.width != w || _depthRt.height != h)
            {
                if (_depthRt != null) { _depthRt.Release(); Destroy(_depthRt); }
                _depthRt = new RenderTexture(w, h, 0, RenderTextureFormat.RFloat)
                { useMipMap = false, autoGenerateMips = false };
                _depthRt.Create();
            }
            if (_depthReadback == null || _depthReadback.width != w || _depthReadback.height != h)
            {
                if (_depthReadback != null) Destroy(_depthReadback);
                _depthReadback = new Texture2D(w, h, TextureFormat.RFloat, false);
            }
        }

        // ── HUD ──
        private void CreateHUD()
        {
            _hudObject = new GameObject("DepthPoseTestHUD");
            _hudObject.transform.SetParent(transform);
            _hudText = _hudObject.AddComponent<TextMesh>();
            _hudText.fontSize = hudFontSize;
            _hudText.characterSize = 0.012f;
            _hudText.anchor = TextAnchor.MiddleCenter;
            _hudText.color = Color.green;
            _hudText.text = "DepthPoseTest: Init...";
        }

        private void UpdateHUD(string msg)
        {
            if (_hudText == null) return;
            Color c = Color.green;
            if (msg.StartsWith("FATAL") || msg.StartsWith("ERROR")) c = Color.red;
            else if (msg.StartsWith("Waiting")) c = Color.yellow;
            else if (msg.StartsWith("Done")) c = new Color(0.3f, 1f, 0.3f);
            _hudText.text = msg;
            _hudText.color = c;
        }

        private void UpdateHudPosition()
        {
            if (_hudObject == null) return;
            Camera cam = _xrCamera ?? Camera.main;
            if (cam == null) return;
            _hudObject.transform.position = cam.transform.position + cam.transform.forward * hudDistance + cam.transform.up * 0.15f;
            _hudObject.transform.LookAt(cam.transform);
            _hudObject.transform.Rotate(0, 180, 0);
        }

        private void Update() { UpdateHudPosition(); }

        // ── Log ──
        private void OnLogMessageReceived(string condition, string stackTrace, LogType type)
        {
            if (condition.Contains("[DepthPoseTest]") || condition.Contains("DepthPoseSaturationTest") ||
                condition.Contains("SATURATION") || condition.Contains("[Method ") ||
                condition.Contains("── Frame") || condition.Contains("[INIT]") ||
                condition.Contains("[READY]") || condition.Contains("[DONE]") || condition.Contains("[FATAL]"))
            {
                string entry = $"[{DateTime.UtcNow:yyyy-MM-dd HH:mm:ss.fff}] [{type}] {condition}";
                if (!string.IsNullOrEmpty(stackTrace) && type != LogType.Log)
                    entry += $"\n  {stackTrace.Replace("\n", "\n  ")}";
                _logLines.Add(entry);
            }
        }

        private void Log(string msg) { Debug.Log($"[DepthPoseTest] {msg}"); }

        private void SaveLogFile()
        {
            try
            {
                string path = Path.Combine(_outputRoot, "saturation_test_log.txt");
                File.WriteAllLines(path, _logLines);
                Debug.Log($"[DepthPoseTest] Log saved: {path} ({_logLines.Count} entries)");
            }
            catch (Exception ex) { Debug.LogError($"Log save failed: {ex.Message}"); }
        }

        // ── Serializable types ──
        [Serializable] private class CaptureMeta
        {
            public int capture_index;
            public int unity_frame;
            public long timestamp_unix_ms;
            public RgbMeta rgb;
            public DepthMeta depth;
        }

        [Serializable] private class RgbMeta
        {
            public long timestamp_ticks;
            public int resolution_w, resolution_h;
            public int requested_resolution_w, requested_resolution_h;
            public int current_resolution_w, current_resolution_h;
            public int source_resolution_w, source_resolution_h;
            public string camera_position;
            public int selected_depth_eye;
            public int sensor_resolution_w, sensor_resolution_h;
            public float focal_length_x, focal_length_y;
            public float principal_point_x, principal_point_y;
            public float sensor_focal_length_x, sensor_focal_length_y;
            public float sensor_principal_point_x, sensor_principal_point_y;
            public float pose_position_x, pose_position_y, pose_position_z;
            public float pose_rotation_x, pose_rotation_y, pose_rotation_z, pose_rotation_w;
        }

        [Serializable] private class DepthMeta
        {
            public bool is_valid;
            public string pose_source;
            public int selected_eye;
            public int texture_width, texture_height, texture_slices;
            public string texture_dimension;
            public string depth_values;
            public string depth_origin;
            public float pose_position_x, pose_position_y, pose_position_z;
            public float pose_rotation_x, pose_rotation_y, pose_rotation_z, pose_rotation_w;
            public int resolution_w, resolution_h;
            public float fov_left, fov_right, fov_top, fov_bottom;
            public float near_z, far_z;
            public float zbuffer_x, zbuffer_y, zbuffer_z, zbuffer_w;
            public float[] reprojection_matrix;
            public float[] reprojection_matrix_eye0;
            public float[] reprojection_matrix_eye1;
            public float[] tracking_space_world_to_local;
            public float[] descriptor_reprojection_matrix;
        }
    }
}
