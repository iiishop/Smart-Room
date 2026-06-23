using System.Collections.Generic;
using UnityEngine;

namespace SmartRoom.Interaction
{
    public sealed class PromptPointMarkerManager : MonoBehaviour
    {
        private const string DefaultObjectName = "PromptPointMarkerManager";

        [SerializeField] private float markerRadius = 0.025f;
        [SerializeField] private float hoverRadius = 0.05f;
        [SerializeField] private float hoverMaxDistance = 10f;
        [SerializeField] private float hoverScaleMultiplier = 1.05f;
        [SerializeField] private int maxMarkers = 128;
        [SerializeField] private Color positiveColor = new Color(0.1f, 1f, 0.35f, 0.95f);
        [SerializeField] private Color negativeColor = new Color(1f, 0.08f, 0.08f, 0.95f);

        private readonly List<MarkerHandle> _markers = new List<MarkerHandle>();
        private MarkerHandle _hoveredMarker;
        private static PromptPointMarkerManager _instance;
        private static readonly int BaseColorId = Shader.PropertyToID("_BaseColor");
        private static readonly int ColorId = Shader.PropertyToID("_Color");

        public sealed class MarkerHandle
        {
            internal GameObject GameObject;
            internal Material Material;
            internal float BaseDiameter;

            public Vector3 WorldPoint { get; internal set; }
            public int Label { get; internal set; }
            public bool IsValid => GameObject != null;
        }

        public static MarkerHandle AddMarker(Vector3 worldPoint, int label)
        {
            PromptPointMarkerManager manager = EnsureExists();
            return manager.CreateMarker(worldPoint, label > 0);
        }

        public static bool TryUpdateHover(Ray ray, out MarkerHandle hoveredMarker)
        {
            hoveredMarker = null;
            PromptPointMarkerManager manager = EnsureExists();
            return manager.TryUpdateHoverInternal(ray, out hoveredMarker);
        }

        public static void ClearHover()
        {
            if (_instance == null) return;
            _instance.SetHovered(null);
        }

        public static void RemoveMarker(MarkerHandle marker)
        {
            if (_instance == null || marker == null) return;
            _instance.RemoveMarkerInternal(marker);
        }

        public static int RemoveMarkersNear(Vector3 worldPoint, float radius)
        {
            if (_instance == null) return 0;
            return _instance.RemoveMarkersNearInternal(worldPoint, Mathf.Max(0f, radius));
        }

        public static void ClearMarkers()
        {
            if (_instance == null) return;
            _instance.SetHovered(null);
            for (int i = _instance._markers.Count - 1; i >= 0; i--)
            {
                _instance.DestroyMarker(_instance._markers[i]);
            }
            _instance._markers.Clear();
        }

        private static PromptPointMarkerManager EnsureExists()
        {
            if (_instance != null)
                return _instance;

            PromptPointMarkerManager existing = FindFirstObjectByType<PromptPointMarkerManager>();
            if (existing != null)
            {
                _instance = existing;
                return existing;
            }

            GameObject go = GameObject.Find(DefaultObjectName);
            if (go == null)
                go = new GameObject(DefaultObjectName);

            _instance = go.GetComponent<PromptPointMarkerManager>();
            if (_instance == null)
                _instance = go.AddComponent<PromptPointMarkerManager>();
            return _instance;
        }

        private void Awake()
        {
            _instance = this;
        }

        private void OnDestroy()
        {
            if (_instance == this)
                _instance = null;
        }

        private MarkerHandle CreateMarker(Vector3 worldPoint, bool positive)
        {
            GameObject marker = GameObject.CreatePrimitive(PrimitiveType.Sphere);
            marker.name = positive ? "PositivePromptPoint" : "NegativePromptPoint";
            marker.transform.SetParent(transform, true);
            marker.transform.position = worldPoint;
            marker.transform.localScale = Vector3.one * (markerRadius * 2f);

            Collider markerCollider = marker.GetComponent<Collider>();
            if (markerCollider != null)
                Destroy(markerCollider);

            MeshRenderer renderer = marker.GetComponent<MeshRenderer>();
            Material material = CreateMaterial(positive ? positiveColor : negativeColor);
            renderer.sharedMaterial = material;

            var handle = new MarkerHandle
            {
                GameObject = marker,
                Material = material,
                WorldPoint = worldPoint,
                Label = positive ? 1 : 0,
                BaseDiameter = markerRadius * 2f,
            };
            _markers.Add(handle);
            while (_markers.Count > maxMarkers)
            {
                MarkerHandle old = _markers[0];
                _markers.RemoveAt(0);
                DestroyMarker(old);
            }

            return handle;
        }

        private bool TryUpdateHoverInternal(Ray ray, out MarkerHandle hoveredMarker)
        {
            hoveredMarker = null;
            if (ray.direction.sqrMagnitude < 0.0001f)
            {
                SetHovered(null);
                return false;
            }

            Vector3 origin = ray.origin;
            Vector3 direction = ray.direction.normalized;
            float bestDistance = hoverRadius;
            MarkerHandle best = null;
            for (int i = _markers.Count - 1; i >= 0; i--)
            {
                MarkerHandle marker = _markers[i];
                if (marker == null || !marker.IsValid)
                {
                    _markers.RemoveAt(i);
                    continue;
                }

                Vector3 toMarker = marker.WorldPoint - origin;
                float alongRay = Vector3.Dot(toMarker, direction);
                if (alongRay < 0f || alongRay > hoverMaxDistance)
                    continue;

                Vector3 closest = origin + direction * alongRay;
                float distance = Vector3.Distance(marker.WorldPoint, closest);
                if (distance <= bestDistance)
                {
                    bestDistance = distance;
                    best = marker;
                }
            }

            SetHovered(best);
            hoveredMarker = best;
            return best != null;
        }

        private void SetHovered(MarkerHandle marker)
        {
            if (_hoveredMarker == marker)
                return;

            if (_hoveredMarker != null && _hoveredMarker.IsValid)
                _hoveredMarker.GameObject.transform.localScale = Vector3.one * _hoveredMarker.BaseDiameter;

            _hoveredMarker = marker;
            if (_hoveredMarker != null && _hoveredMarker.IsValid)
                _hoveredMarker.GameObject.transform.localScale = Vector3.one * (_hoveredMarker.BaseDiameter * hoverScaleMultiplier);
        }

        private void RemoveMarkerInternal(MarkerHandle marker)
        {
            if (marker == null) return;
            if (_hoveredMarker == marker)
                _hoveredMarker = null;
            _markers.Remove(marker);
            DestroyMarker(marker);
        }

        private int RemoveMarkersNearInternal(Vector3 worldPoint, float radius)
        {
            int removed = 0;
            float radiusSq = radius * radius;
            for (int i = _markers.Count - 1; i >= 0; i--)
            {
                MarkerHandle marker = _markers[i];
                if (marker == null || !marker.IsValid)
                {
                    _markers.RemoveAt(i);
                    continue;
                }

                if ((marker.WorldPoint - worldPoint).sqrMagnitude <= radiusSq)
                {
                    if (_hoveredMarker == marker)
                        _hoveredMarker = null;
                    _markers.RemoveAt(i);
                    DestroyMarker(marker);
                    removed++;
                }
            }
            return removed;
        }

        private void DestroyMarker(MarkerHandle marker)
        {
            if (marker == null) return;
            if (marker.GameObject != null)
                Destroy(marker.GameObject);
            if (marker.Material != null)
                Destroy(marker.Material);
            marker.GameObject = null;
            marker.Material = null;
        }

        private static Material CreateMaterial(Color color)
        {
            Shader shader = Shader.Find("Universal Render Pipeline/Unlit");
            if (shader == null) shader = Shader.Find("Unlit/Color");
            Material material = new Material(shader);
            material.SetColor(BaseColorId, color);
            material.SetColor(ColorId, color);
            material.color = color;
            return material;
        }
    }
}
