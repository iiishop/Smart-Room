using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Rendering;

namespace SmartRoom.Rendering
{
    public static class BboxColorTable
    {
        private static readonly Color[] Colors = new Color[]
        {
            new(1f, 0.267f, 0.267f, 1f), new(0.267f, 1f, 0.267f, 1f), new(0.267f, 0.533f, 1f, 1f),
            new(1f, 0.843f, 0f, 1f),     new(1f, 0.267f, 1f, 1f),   new(0.267f, 1f, 1f, 1f),
            new(1f, 0.533f, 0f, 1f),     new(0f, 1f, 0.533f, 1f),   new(0.533f, 0.267f, 1f, 1f),
            new(1f, 0.267f, 0.533f, 1f), new(0.533f, 1f, 0.267f, 1f), new(0.267f, 0.533f, 0.267f, 1f),
            new(1f, 0.667f, 0f, 1f),     new(0.667f, 1f, 0f, 1f),    new(0f, 0.667f, 1f, 1f),
            new(0.667f, 0f, 1f, 1f),
        };

        public static Color GetColor(int objectId)
        {
            return Colors[objectId % Colors.Length];
        }
    }

    public class BboxWireframeManager : MonoBehaviour
    {
        [Header("Shader")]
        [SerializeField] private Shader wireframeShader;
        [SerializeField] private float lineAlpha = 0.6f;
        [SerializeField] private int maxInstances = 64;

        [Header("Material Override")]
        [SerializeField] private Material overrideMaterial;

        [Header("Rendering")]
        [SerializeField] private bool useBuiltinCallback = true;

        private Material _material;
        private ComputeBuffer _cornerBuffer;
        private ComputeBuffer _colorBuffer;

        private readonly List<Vector3> _cornerList = new();
        private readonly List<Color> _colorList = new();
        private int _activeCount;

        private Vector3[] _cornerUploadArray;
        private Color[] _colorUploadArray;

        private static readonly int BboxCornerBufferId = Shader.PropertyToID("_BboxCornerBuffer");
        private static readonly int BboxColorBufferId = Shader.PropertyToID("_BboxColorBuffer");
        private static readonly int BboxCountId = Shader.PropertyToID("_BboxCount");
        private static readonly int LineAlphaId = Shader.PropertyToID("_LineAlpha");

        public bool UseBuiltinCallback => useBuiltinCallback;

        public int ActiveCount => _activeCount;
        public Material Material => _material;

        private void Awake()
        {
            if (overrideMaterial != null)
            {
                _material = overrideMaterial;
            }
            else
            {
                if (wireframeShader == null)
                {
                    wireframeShader = Shader.Find("SmartRoom/BboxWireframe");
                }

                if (wireframeShader != null)
                {
                    _material = new Material(wireframeShader);
                }
            }

            int cornerCount = maxInstances * 8;
            _cornerBuffer = new ComputeBuffer(cornerCount, sizeof(float) * 3, ComputeBufferType.Structured);
            _colorBuffer = new ComputeBuffer(maxInstances, sizeof(float) * 4, ComputeBufferType.Structured);
            _cornerUploadArray = new Vector3[cornerCount];
            _colorUploadArray = new Color[maxInstances];

            if (_material != null)
            {
                _material.SetFloat(LineAlphaId, lineAlpha);
                _material.SetInt(BboxCountId, 0);
                _material.SetBuffer(BboxCornerBufferId, _cornerBuffer);
                _material.SetBuffer(BboxColorBufferId, _colorBuffer);
            }
        }

        private void OnEnable()
        {
            if (useBuiltinCallback)
            {
                RenderPipelineManager.endCameraRendering += OnEndCameraRendering;
            }
        }

        private void OnDisable()
        {
            RenderPipelineManager.endCameraRendering -= OnEndCameraRendering;
        }

        private void OnDestroy()
        {
            RenderPipelineManager.endCameraRendering -= OnEndCameraRendering;

            _cornerBuffer?.Release();
            _cornerBuffer = null;
            _colorBuffer?.Release();
            _colorBuffer = null;

            if (_material != null && overrideMaterial == null)
            {
                Destroy(_material);
                _material = null;
            }
        }

        private void OnEndCameraRendering(ScriptableRenderContext context, Camera camera)
        {
            if (!useBuiltinCallback)
            {
                return;
            }

            if (_activeCount <= 0 || _material == null)
            {
                return;
            }

            if (camera.cameraType == CameraType.Preview)
            {
                return;
            }

            CommandBuffer cmd = CommandBufferPool.Get("BboxWireframe");
            DrawWithCommandBuffer(cmd);
            context.ExecuteCommandBuffer(cmd);
            CommandBufferPool.Release(cmd);
        }

        public void ClearFrameData()
        {
            _cornerList.Clear();
            _colorList.Clear();
            _activeCount = 0;
        }

        public void SetBboxData(int objectId, Vector3[] eightCorners, Color color)
        {
            if (eightCorners == null || eightCorners.Length != 8)
            {
                return;
            }

            if (_cornerList.Count >= maxInstances * 8)
            {
                Debug.LogWarning($"[BboxWireframeManager] Max instances ({maxInstances}) reached. Dropping object_id={objectId}.");
                return;
            }

            for (int i = 0; i < 8; i++)
            {
                _cornerList.Add(eightCorners[i]);
            }

            _colorList.Add(color);
        }

        public void UploadAndApply()
        {
            _activeCount = _colorList.Count;
            if (_activeCount <= 0)
            {
                if (_material != null)
                {
                    _material.SetInt(BboxCountId, 0);
                }
                return;
            }

            if (_cornerBuffer == null || _colorBuffer == null)
            {
                return;
            }

            int cornerCount = _activeCount * 8;
            if (cornerCount > _cornerBuffer.count)
            {
                cornerCount = (_cornerBuffer.count / 8) * 8;
            }

            int colorCount = _activeCount;
            if (colorCount > _colorBuffer.count)
            {
                colorCount = _colorBuffer.count;
            }

            if (cornerCount > 0)
            {
                _cornerList.CopyTo(0, _cornerUploadArray, 0, cornerCount);
                _cornerBuffer.SetData(_cornerUploadArray, 0, 0, cornerCount);
            }

            if (colorCount > 0)
            {
                _colorList.CopyTo(0, _colorUploadArray, 0, colorCount);
                _colorBuffer.SetData(_colorUploadArray, 0, 0, colorCount);
            }

            if (_material != null)
            {
                _material.SetInt(BboxCountId, colorCount);
            }
        }

        public void DrawWithCommandBuffer(CommandBuffer cmd)
        {
            if (_activeCount <= 0 || _material == null)
            {
                return;
            }

            cmd.DrawProcedural(
                Matrix4x4.identity,
                _material,
                0,
                MeshTopology.Lines,
                _activeCount * 24,
                1
            );
        }
    }
}
