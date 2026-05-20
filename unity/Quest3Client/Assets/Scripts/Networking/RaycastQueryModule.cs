using System;
using System.Reflection;
using Meta.XR;
using UnityEngine;

namespace SmartRoom.Networking
{
    [Serializable]
    public class RaycastQueryPayload
    {
        public string type;
        public int query_id;
        public long timestamp_ms;
        public float u;
        public float v;
    }

    [Serializable]
    public class RaycastResultPayload
    {
        public string type;
        public int query_id;
        public long timestamp_ms;
        public float u;
        public float v;
        public bool hit;
        public float depth_m;
        public float[] world_xyz;
        public float[] camera_xyz;
        public string hit_surface_label;
    }

    public class RaycastQueryModule : MonoBehaviour
    {
        [SerializeField] private BackendCommunicationManager manager;
        [SerializeField] private DepthStreamModule depthStreamModule;
        [SerializeField] private Camera rayCamera;
        [SerializeField] private PassthroughCameraAccess passthroughCameraAccess;
        [SerializeField] private bool fallbackToPhysicsRaycast = false;
        [SerializeField] private float maxDistanceMeters = 10f;
        [SerializeField] private LayerMask raycastLayerMask = ~0;

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

            if (rayCamera == null)
            {
                rayCamera = Camera.main;
            }

            if (passthroughCameraAccess == null)
            {
                passthroughCameraAccess = FindFirstObjectByType<PassthroughCameraAccess>();
            }

            if (depthStreamModule == null)
            {
                depthStreamModule = FindFirstObjectByType<DepthStreamModule>();
            }
        }

        private void OnEnable()
        {
            if (manager != null)
            {
                manager.ControlMessageReceived += OnControlMessage;
            }
        }

        private void OnDisable()
        {
            if (manager != null)
            {
                manager.ControlMessageReceived -= OnControlMessage;
            }
        }

        private void OnControlMessage(string json)
        {
            if (string.IsNullOrWhiteSpace(json))
            {
                return;
            }

            RaycastQueryPayload query;
            try
            {
                query = JsonUtility.FromJson<RaycastQueryPayload>(json);
            }
            catch
            {
                return;
            }

            if (query == null || query.type != "raycast_query")
            {
                return;
            }

            if (manager == null)
            {
                return;
            }

            float u = Mathf.Clamp01(query.u);
            float v = Mathf.Clamp01(query.v);
            if (!TryGetViewportRay(u, v, out Ray ray, out Transform rayTransform))
            {
                return;
            }

            bool hit = false;
            float depthM = -1f;
            Vector3 worldPoint = Vector3.zero;
            Vector3 cameraPoint = Vector3.zero;
            string label = "depth";

            if (depthStreamModule != null)
            {
                hit = depthStreamModule.TryRaycastViewport(u, v, ray, rayTransform, out depthM, out worldPoint, out cameraPoint);
            }

            if (!hit && fallbackToPhysicsRaycast)
            {
                if (Physics.Raycast(ray, out RaycastHit hitInfo, maxDistanceMeters, raycastLayerMask, QueryTriggerInteraction.Ignore))
                {
                    hit = true;
                    depthM = hitInfo.distance;
                    worldPoint = hitInfo.point;
                    cameraPoint = rayTransform != null
                        ? rayTransform.InverseTransformPoint(worldPoint)
                        : ray.direction.normalized * depthM;
                    label = GetSurfaceLabel(hitInfo.collider);
                }
            }

            var payload = new RaycastResultPayload
            {
                type = "raycast_result",
                query_id = query.query_id,
                timestamp_ms = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                u = u,
                v = v,
                hit = hit,
                depth_m = hit ? depthM : -1f,
                world_xyz = hit
                    ? new[] { worldPoint.x, worldPoint.y, worldPoint.z }
                    : new[] { 0f, 0f, 0f },
                camera_xyz = hit
                    ? new[] { cameraPoint.x, cameraPoint.y, cameraPoint.z }
                    : new[] { 0f, 0f, 0f },
                hit_surface_label = hit ? label : "none",
            };

            manager.QueueControlJson(JsonUtility.ToJson(payload));
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
                    manager?.QueueUnityLog("WARNING", "PassthroughCameraAccess.ViewportPointToRay unavailable; falling back to Camera.ViewportPointToRay for raycast queries.");
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
            manager?.QueueUnityLog("INFO", $"RaycastQueryModule ray source: {source}");
        }

        private static string GetSurfaceLabel(Collider col)
        {
            if (col == null)
            {
                return "none";
            }

            if (!string.IsNullOrWhiteSpace(col.tag) && col.tag != "Untagged")
            {
                return col.tag;
            }

            return col.gameObject.name;
        }
    }
}
