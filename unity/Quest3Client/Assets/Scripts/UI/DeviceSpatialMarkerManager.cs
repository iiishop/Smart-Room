using System;
using System.Collections;
using System.Collections.Generic;
using SmartRoom.Interaction;
using SmartRoom.Tracking;
using UnityEngine;
using UnityEngine.Networking;

namespace SmartRoom.UI
{
    public sealed class DeviceSpatialMarkerManager : MonoBehaviour
    {
        private const string DefaultObjectName = "DeviceSpatialMarkerManager";

        [Header("References")]
        [SerializeField] private Camera xrCamera;
        [SerializeField] private TrackingManager trackingManager;

        [Header("Marker")]
        [SerializeField] private float markerRadius = 0.035f;
        [SerializeField] private float hoverRadius = 0.07f;
        [SerializeField] private float hoverMaxDistance = 10f;
        [SerializeField] private float hoverScaleMultiplier = 1.22f;
        [SerializeField] private Color markerColor = new Color(1f, 0.86f, 0.08f, 0.96f);
        [SerializeField] private Color hoverColor = new Color(1f, 1f, 0.42f, 1f);

        private readonly Dictionary<string, MarkerEntry> _markers = new Dictionary<string, MarkerEntry>();
        private MarkerEntry _hoveredMarker;
        private static DeviceSpatialMarkerManager _instance;
        private static readonly int BaseColorId = Shader.PropertyToID("_BaseColor");
        private static readonly int ColorId = Shader.PropertyToID("_Color");

        private sealed class MarkerEntry
        {
            public string ObjectId;
            public GameObject Root;
            public Material Material;
            public Vector3 Center;
            public float BaseDiameter;
        }

        public static bool IsHoveringMarker => _instance != null && _instance._hoveredMarker != null;

        public static DeviceSpatialMarkerManager EnsureExists(Camera camera = null)
        {
            if (_instance != null)
            {
                _instance.SetCamera(camera);
                return _instance;
            }

            DeviceSpatialMarkerManager existing = FindFirstObjectByType<DeviceSpatialMarkerManager>();
            if (existing != null)
            {
                _instance = existing;
                existing.SetCamera(camera);
                return existing;
            }

            GameObject go = GameObject.Find(DefaultObjectName);
            if (go == null)
                go = new GameObject(DefaultObjectName);

            DeviceSpatialMarkerManager manager = go.GetComponent<DeviceSpatialMarkerManager>();
            if (manager == null)
                manager = go.AddComponent<DeviceSpatialMarkerManager>();
            manager.SetCamera(camera);
            return manager;
        }

        public static void PlaceForObjectCenter(string objectId, Vector3 center, Camera camera = null)
        {
            EnsureExists(camera).PlaceForObjectCenterInternal(objectId, center);
        }

        public static void RemoveForObject(string objectId)
        {
            if (_instance == null || string.IsNullOrWhiteSpace(objectId))
                return;
            _instance.RemoveForObjectInternal(objectId);
        }

        public static void ClearMarkers()
        {
            if (_instance == null)
                return;
            _instance.ClearMarkersInternal();
        }

        public static void RefreshCompletedObjects()
        {
            EnsureExists(Camera.main).StartRefreshCompletedObjects();
        }

        public static bool UpdateHoverAndConsumeTrigger(Ray ray, bool triggerPressedDown)
        {
            DeviceSpatialMarkerManager manager = EnsureExists(Camera.main);
            manager.UpdateHover(ray);
            if (!triggerPressedDown || manager._hoveredMarker == null)
                return false;

            string objectId = manager._hoveredMarker.ObjectId;
            if (string.IsNullOrWhiteSpace(objectId))
                return false;

            DeviceBindingPanel.OpenForObject(objectId, manager.xrCamera);
            return true;
        }

        public void SetCamera(Camera camera)
        {
            if (camera != null)
                xrCamera = camera;
        }

        private void Awake()
        {
            _instance = this;
            ResolveReferences();
        }

        private void OnDestroy()
        {
            if (_instance == this)
                _instance = null;
            ClearMarkersInternal();
        }

        private void ResolveReferences()
        {
            if (xrCamera == null)
                xrCamera = Camera.main;
            if (trackingManager == null)
                trackingManager = FindFirstObjectByType<TrackingManager>();
        }

        private void PlaceForObjectCenterInternal(string objectId, Vector3 center)
        {
            if (string.IsNullOrWhiteSpace(objectId) || !IsFinite(center))
                return;

            RemoveForObjectInternal(objectId);
            GameObject marker = GameObject.CreatePrimitive(PrimitiveType.Sphere);
            marker.name = "DeviceCenterMarker_" + objectId;
            marker.transform.SetParent(transform, true);
            marker.transform.position = center;
            marker.transform.localScale = Vector3.one * (markerRadius * 2f);

            Collider col = marker.GetComponent<Collider>();
            if (col != null)
                Destroy(col);

            Material material = CreateMaterial(markerColor);
            marker.GetComponent<MeshRenderer>().sharedMaterial = material;

            _markers[objectId] = new MarkerEntry
            {
                ObjectId = objectId,
                Root = marker,
                Material = material,
                Center = center,
                BaseDiameter = markerRadius * 2f,
            };
        }

        private void UpdateHover(Ray ray)
        {
            if (ray.direction.sqrMagnitude < 0.0001f)
            {
                SetHovered(null);
                return;
            }

            Vector3 origin = ray.origin;
            Vector3 direction = ray.direction.normalized;
            float bestDistance = hoverRadius;
            MarkerEntry best = null;

            List<string> stale = null;
            foreach (KeyValuePair<string, MarkerEntry> item in _markers)
            {
                MarkerEntry marker = item.Value;
                if (marker == null || marker.Root == null)
                {
                    if (stale == null)
                        stale = new List<string>();
                    stale.Add(item.Key);
                    continue;
                }

                Vector3 toMarker = marker.Center - origin;
                float alongRay = Vector3.Dot(toMarker, direction);
                if (alongRay < 0f || alongRay > hoverMaxDistance)
                    continue;

                Vector3 closest = origin + direction * alongRay;
                float distance = Vector3.Distance(marker.Center, closest);
                if (distance <= bestDistance)
                {
                    bestDistance = distance;
                    best = marker;
                }
            }

            if (stale != null)
            {
                for (int i = 0; i < stale.Count; i++)
                    _markers.Remove(stale[i]);
            }
            SetHovered(best);
        }

        private void SetHovered(MarkerEntry marker)
        {
            if (_hoveredMarker == marker)
                return;

            if (_hoveredMarker != null && _hoveredMarker.Root != null)
            {
                _hoveredMarker.Root.transform.localScale = Vector3.one * _hoveredMarker.BaseDiameter;
                SetMaterialColor(_hoveredMarker.Material, markerColor);
            }

            _hoveredMarker = marker;
            if (_hoveredMarker != null && _hoveredMarker.Root != null)
            {
                _hoveredMarker.Root.transform.localScale = Vector3.one * (_hoveredMarker.BaseDiameter * hoverScaleMultiplier);
                SetMaterialColor(_hoveredMarker.Material, hoverColor);
            }
        }

        private void RemoveForObjectInternal(string objectId)
        {
            if (!_markers.TryGetValue(objectId, out MarkerEntry marker))
                return;

            if (_hoveredMarker == marker)
                _hoveredMarker = null;
            if (marker.Root != null)
                Destroy(marker.Root);
            if (marker.Material != null)
                Destroy(marker.Material);
            _markers.Remove(objectId);
        }

        private void ClearMarkersInternal()
        {
            foreach (MarkerEntry marker in _markers.Values)
            {
                if (marker.Root != null)
                    Destroy(marker.Root);
                if (marker.Material != null)
                    Destroy(marker.Material);
            }
            _markers.Clear();
            _hoveredMarker = null;
        }

        private void StartRefreshCompletedObjects()
        {
            ResolveReferences();
            if (trackingManager == null || !RoomCoordinateSystemPanel.HasEnteredRoom)
                return;
            StartCoroutine(RefreshCompletedObjectsAsync());
        }

        private IEnumerator RefreshCompletedObjectsAsync()
        {
            string url = trackingManager.BuildViewerUrl(
                "/api/room/object/list?room_id=" + UnityWebRequest.EscapeURL(RoomCoordinateSystemPanel.CurrentRoomId) +
                "&room_name=" + UnityWebRequest.EscapeURL(RoomCoordinateSystemPanel.CurrentRoomName) +
                "&device_id=" + UnityWebRequest.EscapeURL(SystemInfo.deviceUniqueIdentifier) +
                "&device_name=" + UnityWebRequest.EscapeURL(SystemInfo.deviceName) +
                "&device_model=" + UnityWebRequest.EscapeURL(SystemInfo.deviceModel));

            using (UnityWebRequest request = UnityWebRequest.Get(url))
            {
                yield return request.SendWebRequest();
                if (request.result != UnityWebRequest.Result.Success)
                    yield break;

                ObjectListResponse response = null;
                try
                {
                    response = JsonUtility.FromJson<ObjectListResponse>(request.downloadHandler.text);
                }
                catch
                {
                    yield break;
                }

                DeviceObjectRecord[] objects = response != null && response.objects != null
                    ? response.objects
                    : Array.Empty<DeviceObjectRecord>();
                for (int i = 0; i < objects.Length; i++)
                {
                    DeviceObjectRecord record = objects[i];
                    if (record == null || string.IsNullOrWhiteSpace(record.object_id))
                        continue;
                    DevicePlaceholderBoardManager.PlaceForObject(record.object_id, record.spatial, null, xrCamera);
                }
            }
        }

        private static Material CreateMaterial(Color color)
        {
            Shader shader = Shader.Find("Universal Render Pipeline/Unlit");
            if (shader == null)
                shader = Shader.Find("Unlit/Color");
            Material material = new Material(shader);
            SetMaterialColor(material, color);
            return material;
        }

        private static void SetMaterialColor(Material material, Color color)
        {
            if (material == null)
                return;
            material.SetColor(BaseColorId, color);
            material.SetColor(ColorId, color);
            material.color = color;
        }

        private static bool IsFinite(Vector3 value)
        {
            return IsFinite(value.x) && IsFinite(value.y) && IsFinite(value.z);
        }

        private static bool IsFinite(float value)
        {
            return !float.IsNaN(value) && !float.IsInfinity(value);
        }

        [Serializable]
        private sealed class ObjectListResponse
        {
            public bool ok = false;
            public DeviceObjectRecord[] objects = Array.Empty<DeviceObjectRecord>();
        }

        [Serializable]
        private sealed class DeviceObjectRecord
        {
            public string object_id = string.Empty;
            public TrackingManager.RoomObjectSpatialRecord spatial = new TrackingManager.RoomObjectSpatialRecord();
        }
    }
}
