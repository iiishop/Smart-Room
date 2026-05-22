using System;
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
                hit = depthStreamModule.TryRaycastViewport(u, v, ray, out depthM, out worldPoint, out cameraPoint);
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
            if (PassthroughRayResolver.TryGetViewportRay(
                passthroughCameraAccess,
                rayCamera,
                u,
                v,
                out ray,
                out rayTransform,
                out string source,
                out string warningMessage,
                "raycast queries"))
            {
                if (!_loggedPassthroughFallback && !string.IsNullOrEmpty(warningMessage))
                {
                    _loggedPassthroughFallback = true;
                    manager?.QueueUnityLog("WARNING", warningMessage);
                }

                LogRaySourceOnce(source);
                return true;
            }

            if (!_loggedPassthroughFallback && !string.IsNullOrEmpty(warningMessage))
            {
                _loggedPassthroughFallback = true;
                manager?.QueueUnityLog("WARNING", warningMessage);
            }

            ray = default;
            rayTransform = null;
            return false;
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
