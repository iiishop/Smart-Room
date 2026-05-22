using System;
using UnityEngine;

namespace SmartRoom.Vision
{
    public sealed class VisionBboxRenderer : MonoBehaviour
    {
        private static readonly int LineVerticesPropertyId = Shader.PropertyToID("_LineVertices");
        private static readonly int LineWidthPropertyId = Shader.PropertyToID("_LineWidth");
        private static readonly int AlphaPropertyId = Shader.PropertyToID("_Alpha");

        [SerializeField] private VisionRenderConfig renderConfig;
        [SerializeField] private VisionColorPalette colorPalette;
        [SerializeField] private Shader lineShader;
        [SerializeField] [Min(0f)] private float lineWidthPixels = 2f;
        [SerializeField] [Range(0f, 1f)] private float alpha = 1f;

        private ComputeBuffer _lineBuffer;
        private Material _lineMaterial;
        private VisionLineVertex[] _vertices = Array.Empty<VisionLineVertex>();
        private int _vertexCount;
        private bool _gpuRenderingDisabled;

        public ComputeBuffer LineBuffer => _lineBuffer;
        public Material LineMaterial => _lineMaterial;
        public int VertexCount => _vertexCount;
        public bool IsEnabled => !_gpuRenderingDisabled && (renderConfig == null || renderConfig.enabled);

        private void Awake()
        {
            EnsureMaterial();
            EnsureBufferCapacity(GetMaxObjectCount() * VisionBboxGeometry.VerticesPerBox);
        }

        private void OnDisable()
        {
            Clear();
        }

        private void OnDestroy()
        {
            ReleaseResources();
        }

        public void ApplyFrame(VisionFrameProcessedData frame)
        {
            UpdateBuffers(frame != null ? frame.Objects : Array.Empty<VisionObjectProcessedData>());
        }

        public void UpdateBuffers(VisionObjectProcessedData[] objects)
        {
            if (!IsEnabled || objects == null || objects.Length == 0)
            {
                Clear();
                return;
            }

            int maxObjects = Mathf.Min(GetMaxObjectCount(), objects.Length);
            EnsureBufferCapacity(maxObjects * VisionBboxGeometry.VerticesPerBox);

            int writeIndex = 0;
            for (int objectIndex = 0; objectIndex < maxObjects; objectIndex++)
            {
                VisionObjectProcessedData processedObject = objects[objectIndex];
                if (processedObject == null || !processedObject.CornersValid || processedObject.Corners3D == null || processedObject.Corners3D.Length < VisionBboxGeometry.CornerCount)
                {
                    continue;
                }

                Color32 color = ResolveColor(processedObject);
                writeIndex += VisionBboxGeometry.WriteBboxLineVertices(
                    processedObject.Corners3D,
                    color,
                    _vertices,
                    writeIndex);
            }

            UploadVertices(writeIndex);
        }

        public void Clear()
        {
            _vertexCount = 0;
        }

        private void UploadVertices(int vertexCount)
        {
            _vertexCount = vertexCount;
            if (_vertexCount == 0 || _lineBuffer == null || _lineMaterial == null)
            {
                return;
            }

            try
            {
                _lineBuffer.SetData(_vertices, 0, 0, _vertexCount);
                _lineMaterial.SetBuffer(LineVerticesPropertyId, _lineBuffer);
                _lineMaterial.SetFloat(LineWidthPropertyId, Mathf.Max(1f, lineWidthPixels > 0f ? lineWidthPixels : renderConfig != null ? renderConfig.bboxLineWidth : 2f));
                _lineMaterial.SetFloat(AlphaPropertyId, alpha);
            }
            catch (Exception ex)
            {
                Debug.LogError($"Failed to upload vision bbox vertices for this frame: {ex}");
                _vertexCount = 0;
            }
        }

        private void EnsureMaterial()
        {
            if (_lineMaterial != null)
            {
                return;
            }

            Shader shader = lineShader != null ? lineShader : Shader.Find("SmartRoom/Vision/VisionBboxLines");
            if (shader == null)
            {
                return;
            }

            _lineMaterial = new Material(shader)
            {
                hideFlags = HideFlags.HideAndDontSave
            };
        }

        private void EnsureBufferCapacity(int requiredVertexCapacity)
        {
            if (_gpuRenderingDisabled)
            {
                return;
            }

            int safeCapacity = Mathf.Max(requiredVertexCapacity, VisionBboxGeometry.VerticesPerBox);
            if (_vertices.Length < safeCapacity)
            {
                _vertices = new VisionLineVertex[safeCapacity];
            }

            if (_lineBuffer != null && _lineBuffer.count >= safeCapacity)
            {
                return;
            }

            if (_lineBuffer != null)
            {
                _lineBuffer.Release();
            }

            try
            {
                _lineBuffer = new ComputeBuffer(safeCapacity, 16, ComputeBufferType.Structured);
            }
            catch (Exception ex)
            {
                Debug.LogError($"Failed to allocate vision bbox buffer: {ex}");
                _lineBuffer = null;
                _vertexCount = 0;
                _gpuRenderingDisabled = true;
                return;
            }

            if (_lineMaterial != null)
            {
                _lineMaterial.SetBuffer(LineVerticesPropertyId, _lineBuffer);
            }
        }

        private int GetMaxObjectCount()
        {
            return renderConfig != null ? Mathf.Max(1, renderConfig.maxObjects) : 60;
        }

        private Color32 ResolveColor(VisionObjectProcessedData processedObject)
        {
            if (colorPalette != null && colorPalette.TryGetColor(processedObject.Label, out Color color))
            {
                return color;
            }

            return Color.HSVToRGB(Mathf.Repeat(processedObject.ObjectId * 0.173f, 1f), 0.85f, 1f);
        }

        private void ReleaseResources()
        {
            if (_lineBuffer != null)
            {
                _lineBuffer.Release();
                _lineBuffer = null;
            }

            if (_lineMaterial != null)
            {
                Destroy(_lineMaterial);
                _lineMaterial = null;
            }
        }
    }
}
