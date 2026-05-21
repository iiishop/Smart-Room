using UnityEngine;
using UnityEngine.Rendering.Universal;

namespace SmartRoom.Rendering
{
    public sealed class BboxWireframeRenderFeature : ScriptableRendererFeature
    {
        [SerializeField] private Material _material;
        [SerializeField] private RenderPassEvent _passEvent = RenderPassEvent.AfterRenderingTransparents;
        [SerializeField] [Range(0f, 1f)] private float _lineAlpha = 0.5f;
        [SerializeField] private string _cameraTagFilter = "";

        private BboxWireframeRenderPass _renderPass;

        public override void Create()
        {
            if (_renderPass == null)
            {
                _renderPass = new BboxWireframeRenderPass();
            }

            _renderPass.renderPassEvent = _passEvent;
        }

        public override void AddRenderPasses(ScriptableRenderer renderer, ref RenderingData renderingData)
        {
            if (_renderPass == null)
            {
                return;
            }

            if (!string.IsNullOrEmpty(_cameraTagFilter) &&
                !renderingData.cameraData.camera.CompareTag(_cameraTagFilter))
            {
                return;
            }

            var manager = Object.FindFirstObjectByType<BboxWireframeManager>();
            if (manager == null || manager.BboxBuffer == null || manager.ActiveCount <= 0)
            {
                return;
            }

            Material mat = _material;
            if (mat == null)
            {
                mat = Resources.Load<Material>("BboxWireframe");
            }

            if (mat == null)
            {
                return;
            }

            _renderPass.Setup(manager.BboxBuffer, manager.ActiveCount, mat, _lineAlpha);
            renderer.EnqueuePass(_renderPass);
        }
    }
}
