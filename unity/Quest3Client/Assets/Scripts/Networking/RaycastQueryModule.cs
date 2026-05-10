using System;
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
        [SerializeField] private bool fallbackToPhysicsRaycast = false;
        [SerializeField] private float maxDistanceMeters = 10f;
        [SerializeField] private LayerMask raycastLayerMask = ~0;

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

            if (rayCamera == null || manager == null)
            {
                return;
            }

            float u = Mathf.Clamp01(query.u);
            float v = Mathf.Clamp01(query.v);

            bool hit = false;
            float depthM = -1f;
            Vector3 worldPoint = Vector3.zero;
            Vector3 cameraPoint = Vector3.zero;
            string label = "depth";

            if (depthStreamModule != null)
            {
                hit = depthStreamModule.TryRaycastViewport(u, v, rayCamera, out depthM, out worldPoint, out cameraPoint);
            }

            if (!hit && fallbackToPhysicsRaycast)
            {
                Ray ray = rayCamera.ViewportPointToRay(new Vector3(u, v, 0f));
                if (Physics.Raycast(ray, out RaycastHit hitInfo, maxDistanceMeters, raycastLayerMask, QueryTriggerInteraction.Ignore))
                {
                    hit = true;
                    depthM = hitInfo.distance;
                    worldPoint = hitInfo.point;
                    cameraPoint = rayCamera.transform.InverseTransformPoint(worldPoint);
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
