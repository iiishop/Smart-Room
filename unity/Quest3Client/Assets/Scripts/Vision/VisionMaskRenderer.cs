using System;
using UnityEngine;

namespace SmartRoom.Vision
{
    public sealed class VisionMaskRenderer : MonoBehaviour
    {
        private static readonly int LineVerticesPropertyId = Shader.PropertyToID("_LineVertices");
        private static readonly int LineWidthPropertyId = Shader.PropertyToID("_LineWidth");
        private static readonly int DepthOffsetPropertyId = Shader.PropertyToID("_DepthOffsetMeters");
        private static readonly int AlphaPropertyId = Shader.PropertyToID("_Alpha");

        [SerializeField] private VisionRenderConfig renderConfig;
        [SerializeField] private VisionColorPalette colorPalette;
        [SerializeField] private Shader lineShader;
        [SerializeField] [Min(0f)] private float lineWidthPixels = 2f;
        [SerializeField] [Min(0f)] private float depthOffsetMeters = 0.01f;
        [SerializeField] [Range(0f, 1f)] private float alpha = 0.85f;

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
            EnsureBufferCapacity(2);
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
            UpdateContours(frame != null ? frame.Objects : Array.Empty<VisionObjectProcessedData>());
        }

        public void UpdateContours(VisionObjectProcessedData[] objects)
        {
            if (!IsEnabled || objects == null || objects.Length == 0)
            {
                Clear();
                return;
            }

            int maxObjects = Mathf.Min(GetMaxObjectCount(), objects.Length);
            int requiredVertexCount = 0;
            for (int objectIndex = 0; objectIndex < maxObjects; objectIndex++)
            {
                VisionObjectProcessedData processedObject = objects[objectIndex];
                if (processedObject?.Contour3D == null || processedObject.Contour3D.Length < 2)
                {
                    continue;
                }

                requiredVertexCount += processedObject.Contour3D.Length * VisionBboxGeometry.VerticesPerEdge;
            }

            if (requiredVertexCount == 0)
            {
                Clear();
                return;
            }

            EnsureBufferCapacity(requiredVertexCount);

            int writeIndex = 0;
            for (int objectIndex = 0; objectIndex < maxObjects; objectIndex++)
            {
                VisionObjectProcessedData processedObject = objects[objectIndex];
                if (processedObject?.Contour3D == null || processedObject.Contour3D.Length < 2)
                {
                    continue;
                }

                writeIndex += VisionBboxGeometry.WriteClosedContourLineVertices(
                    processedObject.Contour3D,
                    ResolveColor(processedObject),
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
                _lineMaterial.SetFloat(LineWidthPropertyId, Mathf.Max(1f, lineWidthPixels));
                _lineMaterial.SetFloat(DepthOffsetPropertyId, depthOffsetMeters);
                _lineMaterial.SetFloat(AlphaPropertyId, alpha);
            }
            catch (Exception ex)
            {
                Debug.LogError($"Failed to upload vision mask vertices for this frame: {ex}");
                _vertexCount = 0;
            }
        }

        private void EnsureMaterial()
        {
            if (_lineMaterial != null)
            {
                return;
            }

            Shader shader = lineShader != null ? lineShader : Shader.Find("SmartRoom/Vision/VisionMaskLines");
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

            int safeCapacity = Mathf.Max(requiredVertexCapacity, 2);
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
                Debug.LogError($"Failed to allocate vision mask buffer: {ex}");
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
            return colorPalette != null
                ? colorPalette.ResolveColor(processedObject.ObjectId)
                : VisionObjectColorTable.GetColor(processedObject.ObjectId);
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
