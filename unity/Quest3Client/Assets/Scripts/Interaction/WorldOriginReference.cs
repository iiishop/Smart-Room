using System.Collections.Generic;
using TMPro;
using UnityEngine;

namespace SmartRoom.Interaction
{
    /// <summary>
    /// Runtime-only world origin marker with meter-scale XYZ axes.
    /// </summary>
    public sealed class WorldOriginReference : MonoBehaviour
    {
        private const string DefaultObjectName = "WorldOriginReference";

        [Header("Scale")]
        [SerializeField] private float axisLength = 0.35f;
        [SerializeField] private float axisWidth = 0.006f;
        [SerializeField] private float originRadius = 0.025f;
        [SerializeField] private float arrowLength = 0.045f;
        [SerializeField] private float arrowRadius = 0.014f;

        [Header("Labels")]
        [SerializeField] private float axisLabelFontSize = 0.075f;
        [SerializeField] private float originLabelFontSize = 0.045f;
        [SerializeField] private Vector2 axisLabelSize = new Vector2(0.2f, 0.12f);
        [SerializeField] private Vector2 originLabelSize = new Vector2(0.5f, 0.16f);
        [SerializeField] private Vector3 originLabelOffset = new Vector3(0f, 0.06f, 0f);

        [Header("Colors")]
        [SerializeField] private Color originColor = new Color(1f, 1f, 1f, 0.95f);
        [SerializeField] private Color xColor = new Color(1f, 0.12f, 0.08f, 1f);
        [SerializeField] private Color yColor = new Color(0.12f, 0.9f, 0.2f, 1f);
        [SerializeField] private Color zColor = new Color(0.18f, 0.42f, 1f, 1f);
        [SerializeField] private Color labelOutlineColor = new Color(0f, 0f, 0f, 0.9f);
        [SerializeField, Range(0f, 1f)] private float labelOutlineWidth = 0.25f;

        private readonly List<Transform> _billboardLabels = new List<Transform>();
        private readonly List<Material> _ownedMaterials = new List<Material>();
        private readonly List<Mesh> _ownedMeshes = new List<Mesh>();
        private TextMeshPro _originLabelText;
        private Vector3 _originPosition = Vector3.zero;
        private Quaternion _originRotation = Quaternion.identity;
        private string _originDisplayName = string.Empty;
        private Camera _billboardCamera;
        private bool _built;

        private static readonly int BaseColorId = Shader.PropertyToID("_BaseColor");
        private static readonly int ColorId = Shader.PropertyToID("_Color");

        public static WorldOriginReference EnsureExists(Camera billboardCamera = null)
        {
            WorldOriginReference existing = FindFirstObjectByType<WorldOriginReference>();
            if (existing != null)
            {
                existing.SetBillboardCamera(billboardCamera);
                return existing;
            }

            GameObject referenceObject = GameObject.Find(DefaultObjectName);
            if (referenceObject == null)
                referenceObject = new GameObject(DefaultObjectName);

            WorldOriginReference reference = referenceObject.GetComponent<WorldOriginReference>();
            if (reference == null)
                reference = referenceObject.AddComponent<WorldOriginReference>();

            reference.SetBillboardCamera(billboardCamera);
            return reference;
        }

        public static void DestroyExisting()
        {
            WorldOriginReference existing = FindFirstObjectByType<WorldOriginReference>();
            if (existing != null)
            {
                Destroy(existing.gameObject);
                return;
            }

            GameObject referenceObject = GameObject.Find(DefaultObjectName);
            if (referenceObject != null)
                Destroy(referenceObject);
        }

        public void SetBillboardCamera(Camera camera)
        {
            if (camera != null)
                _billboardCamera = camera;
        }

        public void SetOriginPose(Pose originPose, string displayName)
        {
            _originPosition = originPose.position;
            _originRotation = originPose.rotation;
            _originDisplayName = displayName ?? string.Empty;
            ApplyOriginTransform();
            UpdateOriginLabel();
        }

        private void Awake()
        {
            Build();
        }

        private void OnEnable()
        {
            Build();
        }

        private void LateUpdate()
        {
            ApplyOriginTransform();
            BillboardLabels();
        }

        private void OnDestroy()
        {
            for (int i = 0; i < _ownedMaterials.Count; i++)
            {
                if (_ownedMaterials[i] != null)
                    Destroy(_ownedMaterials[i]);
            }

            for (int i = 0; i < _ownedMeshes.Count; i++)
            {
                if (_ownedMeshes[i] != null)
                    Destroy(_ownedMeshes[i]);
            }
        }

        private void Build()
        {
            if (_built) return;

            gameObject.name = DefaultObjectName;
            ApplyOriginTransform();

            CreateOriginMarker();
            CreateAxis("XAxis", Vector3.right, xColor, "X");
            CreateAxis("YAxis", Vector3.up, yColor, "Y");
            CreateAxis("ZAxis", Vector3.forward, zColor, "Z");
            _originLabelText = CreateLabel("OriginLabel", string.Empty, originLabelOffset, originColor, originLabelSize, originLabelFontSize);
            UpdateOriginLabel();

            _built = true;
        }

        private void ApplyOriginTransform()
        {
            if (transform.parent != null)
                transform.SetParent(null, true);

            transform.SetPositionAndRotation(_originPosition, _originRotation);
            transform.localScale = Vector3.one;
        }

        private void UpdateOriginLabel()
        {
            if (_originLabelText == null) return;

            _originLabelText.text = string.IsNullOrWhiteSpace(_originDisplayName)
                ? "Origin\n(0, 0, 0) m"
                : _originDisplayName + "\nOrigin (0,0,0) m";
        }

        private void CreateOriginMarker()
        {
            var originObject = new GameObject("OriginMarker", typeof(MeshFilter), typeof(MeshRenderer));
            originObject.transform.SetParent(transform, false);
            originObject.transform.localPosition = Vector3.zero;
            originObject.transform.localRotation = Quaternion.identity;
            originObject.transform.localScale = Vector3.one * originRadius;

            Mesh mesh = CreateSphereMesh();
            _ownedMeshes.Add(mesh);
            originObject.GetComponent<MeshFilter>().sharedMesh = mesh;
            originObject.GetComponent<MeshRenderer>().sharedMaterial = CreateMaterial(originColor);
        }

        private void CreateAxis(string name, Vector3 direction, Color color, string label)
        {
            Vector3 normalizedDirection = direction.normalized;
            float lineEnd = Mathf.Max(0f, axisLength - arrowLength);

            var axisRoot = new GameObject(name);
            axisRoot.transform.SetParent(transform, false);

            var lineObject = new GameObject(name + "Line");
            lineObject.transform.SetParent(axisRoot.transform, false);
            LineRenderer lineRenderer = lineObject.AddComponent<LineRenderer>();
            lineRenderer.useWorldSpace = false;
            lineRenderer.positionCount = 2;
            lineRenderer.SetPosition(0, Vector3.zero);
            lineRenderer.SetPosition(1, normalizedDirection * lineEnd);
            lineRenderer.startWidth = axisWidth;
            lineRenderer.endWidth = axisWidth;
            lineRenderer.startColor = color;
            lineRenderer.endColor = color;
            lineRenderer.material = CreateMaterial(color);

            var arrowObject = new GameObject(name + "Arrow", typeof(MeshFilter), typeof(MeshRenderer));
            arrowObject.transform.SetParent(axisRoot.transform, false);
            arrowObject.transform.localPosition = normalizedDirection * lineEnd;
            arrowObject.transform.localRotation = RotationForDirection(normalizedDirection);
            arrowObject.transform.localScale = new Vector3(arrowRadius, arrowRadius, arrowLength);

            Mesh arrowMesh = CreateConeMesh();
            _ownedMeshes.Add(arrowMesh);
            arrowObject.GetComponent<MeshFilter>().sharedMesh = arrowMesh;
            arrowObject.GetComponent<MeshRenderer>().sharedMaterial = CreateMaterial(color);

            CreateLabel(name + "Label", label, normalizedDirection * (axisLength + 0.04f), color, axisLabelSize, axisLabelFontSize);
        }

        private TextMeshPro CreateLabel(string name, string text, Vector3 localPosition, Color color, Vector2 size, float fontSize)
        {
            var labelObject = new GameObject(name, typeof(TextMeshPro));
            labelObject.transform.SetParent(transform, false);
            labelObject.transform.localPosition = localPosition;
            labelObject.transform.localRotation = Quaternion.identity;
            labelObject.transform.localScale = Vector3.one;

            TextMeshPro labelText = labelObject.GetComponent<TextMeshPro>();
            labelText.text = text;
            labelText.fontSize = fontSize;
            labelText.color = color;
            labelText.alignment = TextAlignmentOptions.Center;
            labelText.textWrappingMode = TextWrappingModes.NoWrap;
            labelText.overflowMode = TextOverflowModes.Overflow;
            labelText.outlineColor = labelOutlineColor;
            labelText.outlineWidth = labelOutlineWidth;

            RectTransform rectTransform = labelObject.GetComponent<RectTransform>();
            if (rectTransform != null)
            {
                rectTransform.sizeDelta = size;
            }

            _billboardLabels.Add(labelObject.transform);
            return labelText;
        }

        private void BillboardLabels()
        {
            Camera camera = ResolveBillboardCamera();
            if (camera == null) return;

            for (int i = 0; i < _billboardLabels.Count; i++)
            {
                Transform label = _billboardLabels[i];
                if (label == null) continue;

                label.LookAt(label.position + camera.transform.forward, camera.transform.up);
            }
        }

        private Camera ResolveBillboardCamera()
        {
            if (_billboardCamera == null)
                _billboardCamera = Camera.main;
            return _billboardCamera;
        }

        private Material CreateMaterial(Color color)
        {
            Shader shader = Shader.Find("Universal Render Pipeline/Unlit");
            if (shader == null) shader = Shader.Find("Unlit/Color");

            var material = new Material(shader);
            material.SetColor(BaseColorId, color);
            material.SetColor(ColorId, color);
            material.color = color;
            _ownedMaterials.Add(material);
            return material;
        }

        private static Quaternion RotationForDirection(Vector3 direction)
        {
            Vector3 up = Mathf.Abs(Vector3.Dot(direction, Vector3.up)) > 0.99f
                ? Vector3.forward
                : Vector3.up;
            return Quaternion.LookRotation(direction, up);
        }

        private static Mesh CreateSphereMesh()
        {
            var mesh = new Mesh { name = "WorldOriginReferenceSphere" };
            const int segments = 16;
            const int rings = 12;
            int vertCount = (segments + 1) * (rings + 1);
            var verts = new Vector3[vertCount];
            var tris = new int[6 * segments * rings];

            for (int ring = 0; ring <= rings; ring++)
            {
                float phi = Mathf.PI * ring / rings;
                for (int seg = 0; seg <= segments; seg++)
                {
                    float theta = 2f * Mathf.PI * seg / segments;
                    int i = ring * (segments + 1) + seg;
                    verts[i] = new Vector3(
                        Mathf.Sin(phi) * Mathf.Cos(theta),
                        Mathf.Cos(phi),
                        Mathf.Sin(phi) * Mathf.Sin(theta));
                }
            }

            int ti = 0;
            for (int ring = 0; ring < rings; ring++)
            {
                for (int seg = 0; seg < segments; seg++)
                {
                    int a = ring * (segments + 1) + seg;
                    int b = a + segments + 1;
                    tris[ti++] = a;
                    tris[ti++] = b;
                    tris[ti++] = a + 1;
                    tris[ti++] = a + 1;
                    tris[ti++] = b;
                    tris[ti++] = b + 1;
                }
            }

            mesh.vertices = verts;
            mesh.triangles = tris;
            mesh.RecalculateNormals();
            mesh.RecalculateBounds();
            return mesh;
        }

        private static Mesh CreateConeMesh()
        {
            var mesh = new Mesh { name = "WorldOriginReferenceCone" };
            const int segments = 20;
            var verts = new Vector3[segments + 2];
            var tris = new int[segments * 6];

            verts[0] = Vector3.zero;
            verts[1] = Vector3.forward;
            for (int i = 0; i < segments; i++)
            {
                float angle = 2f * Mathf.PI * i / segments;
                verts[i + 2] = new Vector3(Mathf.Cos(angle), Mathf.Sin(angle), 0f);
            }

            int ti = 0;
            for (int i = 0; i < segments; i++)
            {
                int current = i + 2;
                int next = ((i + 1) % segments) + 2;

                tris[ti++] = 1;
                tris[ti++] = current;
                tris[ti++] = next;

                tris[ti++] = 0;
                tris[ti++] = next;
                tris[ti++] = current;
            }

            mesh.vertices = verts;
            mesh.triangles = tris;
            mesh.RecalculateNormals();
            mesh.RecalculateBounds();
            return mesh;
        }
    }
}
