using System;
using System.Reflection;
using System.Text;
using Meta.XR;
using UnityEngine;

namespace SmartRoom.Networking
{
    public class VisionReceiverModule : MonoBehaviour
    {
        [SerializeField] private BackendCommunicationManager manager;
        [SerializeField] private DepthStreamModule depthStreamModule;
        [SerializeField] private Camera rayCamera;
        [SerializeField] private PassthroughCameraAccess passthroughCameraAccess;
        [SerializeField] private int samplesPerObject = 3;
        [SerializeField] private int maxObjectsPerFrame = 8;

        private MethodInfo _passthroughViewportRayMethod;
        private bool _passthroughViewportRayMethodResolved;
        private bool _loggedRaySource;
        private bool _loggedPassthroughFallback;

        private void Awake()
        {
            if (manager == null)
            {
                manager = FindFirstObjectByType<BackendCommunicationManager>();
            }

            if (depthStreamModule == null)
            {
                depthStreamModule = FindFirstObjectByType<DepthStreamModule>();
            }

            if (rayCamera == null)
            {
                rayCamera = Camera.main;
            }

            if (passthroughCameraAccess == null)
            {
                passthroughCameraAccess = FindFirstObjectByType<PassthroughCameraAccess>();
            }

            samplesPerObject = Mathf.Clamp(samplesPerObject, 1, 16);
            maxObjectsPerFrame = Mathf.Clamp(maxObjectsPerFrame, 1, 64);
        }

        private void OnEnable()
        {
            if (manager != null)
            {
                manager.VisionMessageReceived += OnVisionMessage;
            }
        }

        private void OnDisable()
        {
            if (manager != null)
            {
                manager.VisionMessageReceived -= OnVisionMessage;
            }
        }

        private void OnVisionMessage(string json)
        {
            if (string.IsNullOrWhiteSpace(json) || manager == null || depthStreamModule == null)
            {
                return;
            }

            VisionFramePayload frame;
            try
            {
                frame = JsonUtility.FromJson<VisionFramePayload>(json);
            }
            catch (Exception ex)
            {
                manager.QueueUnityLog("WARNING", $"Vision payload parse failed: {ex.Message}");
                return;
            }

            if (frame?.objects == null || frame.objects.Length == 0)
            {
                return;
            }

            int objectCount = Mathf.Min(frame.objects.Length, maxObjectsPerFrame);
            for (int objectIndex = 0; objectIndex < objectCount; objectIndex++)
            {
                VisionTrackedMaskPayload trackedMask = frame.objects[objectIndex];
                if (trackedMask?.mask_rle == null)
                {
                    continue;
                }

                VisionMaskSamplePoint[] samples = VisionMaskSampling.SampleMaskPixels(frame, trackedMask, samplesPerObject);
                if (samples.Length == 0)
                {
                    manager.QueueUnityLog("WARNING", $"Vision object_id={trackedMask.object_id} has no foreground pixels to sample.");
                    continue;
                }

                var successful = new StringBuilder();
                int hitCount = 0;
                for (int sampleIndex = 0; sampleIndex < samples.Length; sampleIndex++)
                {
                    VisionMaskSamplePoint sample = samples[sampleIndex];
                    if (!TryGetViewportRay(sample.ViewportU, sample.ViewportV, out Ray ray, out Transform rayTransform))
                    {
                        continue;
                    }

                    if (!depthStreamModule.TryRaycastViewport(sample.ViewportU, sample.ViewportV, ray, rayTransform, out float depthM, out Vector3 worldPoint, out _))
                    {
                        continue;
                    }

                    if (successful.Length > 0)
                    {
                        successful.Append("; ");
                    }

                    successful.AppendFormat(
                        "pixel=({0},{1}) viewport=({2:F3},{3:F3}) world=({4:F3},{5:F3},{6:F3}) depth={7:F3}",
                        sample.PixelX,
                        sample.PixelY,
                        sample.ViewportU,
                        sample.ViewportV,
                        worldPoint.x,
                        worldPoint.y,
                        worldPoint.z,
                        depthM
                    );
                    hitCount++;
                }

                if (hitCount > 0)
                {
                    string label = string.IsNullOrWhiteSpace(trackedMask.label) ? "unknown" : trackedMask.label;
                    manager.QueueUnityLog(
                        "INFO",
                        $"Vision object_id={trackedMask.object_id} label={label} hits={hitCount}/{samples.Length} samples=[{successful}]"
                    );
                }
                else
                {
                    manager.QueueUnityLog(
                        "WARNING",
                        $"Vision object_id={trackedMask.object_id} produced no 3D hits from {samples.Length} sampled mask pixels."
                    );
                }
            }
        }

        private bool TryGetViewportRay(float u, float v, out Ray ray, out Transform rayTransform)
        {
            if (TryGetPassthroughViewportRay(u, v, out ray))
            {
                rayTransform = passthroughCameraAccess != null ? passthroughCameraAccess.transform : null;
                LogRaySourceOnce("PassthroughCameraAccess.ViewportPointToRay");
                return true;
            }

            if (rayCamera != null)
            {
                ray = rayCamera.ViewportPointToRay(new Vector3(u, v, 0f));
                rayTransform = rayCamera.transform;
                if (!_loggedPassthroughFallback && passthroughCameraAccess != null)
                {
                    _loggedPassthroughFallback = true;
                    manager?.QueueUnityLog("WARNING", "PassthroughCameraAccess.ViewportPointToRay unavailable; falling back to Camera.ViewportPointToRay for vision receiver.");
                }

                LogRaySourceOnce($"Camera.ViewportPointToRay({rayCamera.name})");
                return true;
            }

            ray = default;
            rayTransform = null;
            return false;
        }

        private bool TryGetPassthroughViewportRay(float u, float v, out Ray ray)
        {
            ray = default;

            if (passthroughCameraAccess == null || !passthroughCameraAccess.enabled || !passthroughCameraAccess.IsPlaying)
            {
                return false;
            }

            MethodInfo method = ResolvePassthroughViewportRayMethod();
            if (method == null)
            {
                return false;
            }

            object arg = method.GetParameters()[0].ParameterType == typeof(Vector2)
                ? new Vector2(u, v)
                : new Vector3(u, v, 0f);

            object target = method.IsStatic ? null : passthroughCameraAccess;
            try
            {
                object result = method.Invoke(target, new[] { arg });
                if (result is Ray castRay)
                {
                    ray = castRay;
                    return true;
                }
            }
            catch (TargetInvocationException ex)
            {
                if (!_loggedPassthroughFallback)
                {
                    _loggedPassthroughFallback = true;
                    manager?.QueueUnityLog("WARNING", $"PassthroughCameraAccess.ViewportPointToRay failed: {ex.InnerException?.Message ?? ex.Message}");
                }
            }
            catch (Exception ex)
            {
                if (!_loggedPassthroughFallback)
                {
                    _loggedPassthroughFallback = true;
                    manager?.QueueUnityLog("WARNING", $"PassthroughCameraAccess.ViewportPointToRay failed: {ex.Message}");
                }
            }

            return false;
        }

        private MethodInfo ResolvePassthroughViewportRayMethod()
        {
            if (_passthroughViewportRayMethodResolved)
            {
                return _passthroughViewportRayMethod;
            }

            _passthroughViewportRayMethodResolved = true;
            foreach (MethodInfo method in typeof(PassthroughCameraAccess).GetMethods(BindingFlags.Public | BindingFlags.Instance | BindingFlags.Static))
            {
                if (method.Name != "ViewportPointToRay" || method.ReturnType != typeof(Ray))
                {
                    continue;
                }

                ParameterInfo[] parameters = method.GetParameters();
                if (parameters.Length != 1)
                {
                    continue;
                }

                Type parameterType = parameters[0].ParameterType;
                if (parameterType == typeof(Vector2) || parameterType == typeof(Vector3))
                {
                    _passthroughViewportRayMethod = method;
                    break;
                }
            }

            return _passthroughViewportRayMethod;
        }

        private void LogRaySourceOnce(string source)
        {
            if (_loggedRaySource)
            {
                return;
            }

            _loggedRaySource = true;
            manager?.QueueUnityLog("INFO", $"VisionReceiverModule ray source: {source}");
        }
    }
}
