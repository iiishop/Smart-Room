using System.Collections.Generic;
using SmartRoom.Tracking;
using UnityEngine;
using UnityEngine.UI;

namespace SmartRoom.UI
{
    public sealed class DevicePlaceholderBoardManager : MonoBehaviour
    {
        private const string DefaultObjectName = "DevicePlaceholderBoardManager";

        [Header("References")]
        [SerializeField] private Camera xrCamera;

        [Header("Board")]
        [SerializeField] private Vector2 boardSizeMeters = new Vector2(0.34f, 0.22f);
        [SerializeField] private float canvasPixelsPerMeter = 1000f;
        [SerializeField] private float minimumSideOffsetMeters = 0.32f;
        [SerializeField] private float maximumSideOffsetMeters = 0.95f;
        [SerializeField] private float sideMarginMeters = 0.12f;
        [SerializeField] private float upwardBiasMeters = 0.08f;
        [SerializeField] private float maxUpwardBiasMeters = 0.24f;
        [SerializeField] private float rotationLerp = 14f;

        [Header("Colors")]
        [SerializeField] private Color panelColor = new Color(1f, 1f, 1f, 0.96f);
        [SerializeField] private Color borderColor = new Color(0.72f, 0.76f, 0.82f, 1f);
        [SerializeField] private Color shadowColor = new Color(0f, 0f, 0f, 0.18f);

        private readonly Dictionary<string, BoardEntry> _boards = new Dictionary<string, BoardEntry>();
        private static DevicePlaceholderBoardManager _instance;

        private sealed class BoardEntry
        {
            public string ObjectId;
            public GameObject Root;
            public Vector3 DeviceCenter;
            public Vector3 BoardPosition;
        }

        public static DevicePlaceholderBoardManager EnsureExists(Camera camera = null)
        {
            if (_instance != null)
            {
                _instance.SetCamera(camera);
                return _instance;
            }

            DevicePlaceholderBoardManager existing = FindFirstObjectByType<DevicePlaceholderBoardManager>();
            if (existing != null)
            {
                _instance = existing;
                existing.SetCamera(camera);
                return existing;
            }

            GameObject go = GameObject.Find(DefaultObjectName);
            if (go == null)
                go = new GameObject(DefaultObjectName);

            DevicePlaceholderBoardManager manager = go.GetComponent<DevicePlaceholderBoardManager>();
            if (manager == null)
                manager = go.AddComponent<DevicePlaceholderBoardManager>();
            manager.SetCamera(camera);
            return manager;
        }

        public static void PlaceForObject(string objectId, TrackingManager.RoomObjectPointRecord[] points, Camera camera = null)
        {
            EnsureExists(camera).PlaceForObjectInternal(objectId, null, points);
        }

        public static void PlaceForObject(
            string objectId,
            TrackingManager.RoomObjectSpatialRecord spatial,
            TrackingManager.RoomObjectPointRecord[] points,
            Camera camera = null)
        {
            EnsureExists(camera).PlaceForObjectInternal(objectId, spatial, points);
        }

        public static void RemoveForObject(string objectId)
        {
            if (_instance == null || string.IsNullOrWhiteSpace(objectId))
                return;
            _instance.RemoveForObjectInternal(objectId);
        }

        public static void ClearBoards()
        {
            if (_instance == null)
                return;
            _instance.ClearBoardsInternal();
        }

        public void SetCamera(Camera camera)
        {
            if (camera != null)
                xrCamera = camera;
        }

        private void Awake()
        {
            _instance = this;
            if (xrCamera == null)
                xrCamera = Camera.main;
        }

        private void OnDestroy()
        {
            if (_instance == this)
                _instance = null;
        }

        private void Update()
        {
            if (xrCamera == null)
                xrCamera = Camera.main;
            if (xrCamera == null)
                return;

            foreach (BoardEntry entry in _boards.Values)
                UpdateBoardRotation(entry);
        }

        private void PlaceForObjectInternal(
            string objectId,
            TrackingManager.RoomObjectSpatialRecord spatial,
            TrackingManager.RoomObjectPointRecord[] points)
        {
            if (string.IsNullOrWhiteSpace(objectId))
                return;
            if (xrCamera == null)
                xrCamera = Camera.main;
            if (xrCamera == null)
                return;

            bool solved = TrySolvePlacement(spatial, out Vector3 center, out Vector3 boardPosition);
            if (!solved)
                solved = TrySolvePlacement(points, out center, out boardPosition);
            if (!solved)
                return;

            RemoveForObjectInternal(objectId);
            BoardEntry entry = CreateBoard(objectId, center, boardPosition);
            _boards[objectId] = entry;
            UpdateBoardRotation(entry, snap: true);
            Debug.Log($"[DevicePlaceholderBoard] Placed board for {objectId} center={center:F3} board={boardPosition:F3}");
        }

        private bool TrySolvePlacement(
            TrackingManager.RoomObjectSpatialRecord spatial,
            out Vector3 center,
            out Vector3 boardPosition)
        {
            center = Vector3.zero;
            boardPosition = Vector3.zero;
            if (spatial == null || !spatial.valid || spatial.center_xyz_m == null || spatial.center_xyz_m.Length < 3)
                return false;

            center = new Vector3(spatial.center_xyz_m[0], spatial.center_xyz_m[1], spatial.center_xyz_m[2]);
            if (!IsFinite(center))
                return false;

            Vector3 side = ResolveHorizontalSide(center);
            float sideRadius = Mathf.Max(0.08f, spatial.radius_m * 0.35f);
            float upRadius = 0.12f;
            if (spatial.min_xyz_m != null && spatial.min_xyz_m.Length >= 3 &&
                spatial.max_xyz_m != null && spatial.max_xyz_m.Length >= 3)
            {
                Vector3 min = new Vector3(spatial.min_xyz_m[0], spatial.min_xyz_m[1], spatial.min_xyz_m[2]);
                Vector3 max = new Vector3(spatial.max_xyz_m[0], spatial.max_xyz_m[1], spatial.max_xyz_m[2]);
                if (IsFinite(min) && IsFinite(max))
                {
                    Vector3 half = (max - min) * 0.5f;
                    upRadius = Mathf.Abs(half.y);
                    sideRadius = Mathf.Max(
                        sideRadius,
                        Mathf.Abs(side.x) * Mathf.Abs(half.x) +
                        Mathf.Abs(side.y) * Mathf.Abs(half.y) +
                        Mathf.Abs(side.z) * Mathf.Abs(half.z));
                }
            }

            boardPosition = BuildBoardPosition(center, side, sideRadius, upRadius);
            return true;
        }

        private bool TrySolvePlacement(
            TrackingManager.RoomObjectPointRecord[] points,
            out Vector3 center,
            out Vector3 boardPosition)
        {
            if (points == null || points.Length == 0)
            {
                center = Vector3.zero;
                boardPosition = Vector3.zero;
                return false;
            }

            List<Vector3> positivePoints = new List<Vector3>();
            List<Vector3> allPoints = new List<Vector3>();
            for (int i = 0; i < points.Length; i++)
            {
                TrackingManager.RoomObjectPointRecord point = points[i];
                if (point == null || point.world_xyz_m == null || point.world_xyz_m.Length < 3)
                    continue;
                Vector3 world = new Vector3(point.world_xyz_m[0], point.world_xyz_m[1], point.world_xyz_m[2]);
                if (!IsFinite(world))
                    continue;
                allPoints.Add(world);
                if (point.label > 0)
                    positivePoints.Add(world);
            }

            List<Vector3> source = positivePoints.Count > 0 ? positivePoints : allPoints;
            if (source.Count == 0)
            {
                center = Vector3.zero;
                boardPosition = Vector3.zero;
                return false;
            }

            center = RobustCenter(source);

            Vector3 side = ResolveHorizontalSide(center);
            float sideRadius = 0f;
            float upRadius = 0f;
            for (int i = 0; i < source.Count; i++)
            {
                Vector3 delta = source[i] - center;
                sideRadius = Mathf.Max(sideRadius, Mathf.Abs(Vector3.Dot(delta, side)));
                upRadius = Mathf.Max(upRadius, Mathf.Abs(Vector3.Dot(delta, Vector3.up)));
            }

            boardPosition = BuildBoardPosition(center, side, sideRadius, upRadius);
            return true;
        }

        private Vector3 ResolveHorizontalSide(Vector3 center)
        {
            Vector3 side = Vector3.ProjectOnPlane(xrCamera.transform.right, Vector3.up);
            if (side.sqrMagnitude < 0.0001f)
                side = Vector3.ProjectOnPlane(Vector3.Cross(Vector3.up, center - xrCamera.transform.position), Vector3.up);
            if (side.sqrMagnitude < 0.0001f)
                side = Vector3.right;
            side.Normalize();
            return side;
        }

        private Vector3 BuildBoardPosition(Vector3 center, Vector3 side, float sideRadius, float upRadius)
        {
            float sideOffset = Mathf.Clamp(
                sideRadius + boardSizeMeters.x * 0.5f + sideMarginMeters,
                minimumSideOffsetMeters,
                maximumSideOffsetMeters);
            float upOffset = Mathf.Clamp(upwardBiasMeters + upRadius * 0.25f, upwardBiasMeters, maxUpwardBiasMeters);
            return center + side * sideOffset + Vector3.up * upOffset;
        }

        private BoardEntry CreateBoard(string objectId, Vector3 center, Vector3 boardPosition)
        {
            GameObject root = new GameObject("DevicePlaceholderBoard_" + objectId, typeof(RectTransform), typeof(Canvas), typeof(CanvasScaler));
            root.transform.SetParent(transform, true);
            root.transform.position = boardPosition;

            RectTransform rootRect = root.GetComponent<RectTransform>();
            rootRect.sizeDelta = new Vector2(boardSizeMeters.x * canvasPixelsPerMeter, boardSizeMeters.y * canvasPixelsPerMeter);
            rootRect.localScale = Vector3.one / canvasPixelsPerMeter;

            Canvas canvas = root.GetComponent<Canvas>();
            canvas.renderMode = RenderMode.WorldSpace;
            canvas.worldCamera = xrCamera;
            canvas.sortingOrder = 20;

            CanvasScaler scaler = root.GetComponent<CanvasScaler>();
            scaler.dynamicPixelsPerUnit = canvasPixelsPerMeter;
            scaler.referencePixelsPerUnit = 100f;

            GameObject shadow = CreateImage(rootRect, "Shadow", shadowColor);
            RectTransform shadowRect = (RectTransform)shadow.transform;
            Stretch(shadowRect, -7f, -7f, -7f, -7f);
            shadowRect.anchoredPosition = new Vector2(8f, -8f);

            GameObject border = CreateImage(rootRect, "Border", borderColor);
            Stretch((RectTransform)border.transform, -3f, -3f, -3f, -3f);

            GameObject panel = CreateImage(rootRect, "WhitePanel", panelColor);
            Stretch((RectTransform)panel.transform, 5f, 5f, 5f, 5f);

            return new BoardEntry
            {
                ObjectId = objectId,
                Root = root,
                DeviceCenter = center,
                BoardPosition = boardPosition,
            };
        }

        private static GameObject CreateImage(Transform parent, string name, Color color)
        {
            GameObject go = new GameObject(name, typeof(RectTransform), typeof(CanvasRenderer), typeof(Image));
            go.transform.SetParent(parent, false);
            Image image = go.GetComponent<Image>();
            image.color = color;
            image.raycastTarget = false;
            return go;
        }

        private void UpdateBoardRotation(BoardEntry entry, bool snap = false)
        {
            if (entry == null || entry.Root == null || xrCamera == null)
                return;

            Vector3 forward = entry.Root.transform.position - xrCamera.transform.position;
            if (forward.sqrMagnitude < 0.0001f)
                forward = xrCamera.transform.forward;
            Quaternion targetRotation = Quaternion.LookRotation(forward.normalized, Vector3.up);
            if (snap)
            {
                entry.Root.transform.rotation = targetRotation;
                return;
            }

            float t = 1f - Mathf.Exp(-rotationLerp * Time.deltaTime);
            entry.Root.transform.rotation = Quaternion.Slerp(entry.Root.transform.rotation, targetRotation, t);
        }

        private void RemoveForObjectInternal(string objectId)
        {
            if (!_boards.TryGetValue(objectId, out BoardEntry entry))
                return;
            if (entry.Root != null)
                Destroy(entry.Root);
            _boards.Remove(objectId);
        }

        private void ClearBoardsInternal()
        {
            foreach (BoardEntry entry in _boards.Values)
            {
                if (entry.Root != null)
                    Destroy(entry.Root);
            }
            _boards.Clear();
        }

        private static Vector3 RobustCenter(List<Vector3> points)
        {
            if (points.Count == 1)
                return points[0];

            float[] xs = new float[points.Count];
            float[] ys = new float[points.Count];
            float[] zs = new float[points.Count];
            for (int i = 0; i < points.Count; i++)
            {
                xs[i] = points[i].x;
                ys[i] = points[i].y;
                zs[i] = points[i].z;
            }
            return new Vector3(Median(xs), Median(ys), Median(zs));
        }

        private static float Median(float[] values)
        {
            System.Array.Sort(values);
            int mid = values.Length / 2;
            if ((values.Length & 1) == 1)
                return values[mid];
            return (values[mid - 1] + values[mid]) * 0.5f;
        }

        private static bool IsFinite(Vector3 value)
        {
            return IsFinite(value.x) && IsFinite(value.y) && IsFinite(value.z);
        }

        private static bool IsFinite(float value)
        {
            return !float.IsNaN(value) && !float.IsInfinity(value);
        }

        private static void Stretch(RectTransform rectTransform, float left, float right, float top, float bottom)
        {
            rectTransform.anchorMin = Vector2.zero;
            rectTransform.anchorMax = Vector2.one;
            rectTransform.offsetMin = new Vector2(left, bottom);
            rectTransform.offsetMax = new Vector2(-right, -top);
        }
    }
}
