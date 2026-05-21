using UnityEngine;
using UnityEngine.Rendering.Universal;

namespace SmartRoom.Vision
{
    public sealed class VisionRendererFeature : ScriptableRendererFeature
    {
        [SerializeField] private VisionBboxRenderer bboxRenderer;
        [SerializeField] private VisionMaskRenderer maskRenderer;
        [SerializeField] private RenderPassEvent passEvent = RenderPassEvent.AfterRenderingTransparents;

        private VisionRenderPass _renderPass;

        public override void Create()
        {
            if (_renderPass == null)
            {
                _renderPass = new VisionRenderPass();
            }

            _renderPass.renderPassEvent = passEvent;
        }

        public override void AddRenderPasses(ScriptableRenderer renderer, ref RenderingData renderingData)
        {
            if (_renderPass == null)
            {
                return;
            }

            if (bboxRenderer == null)
            {
                bboxRenderer = Object.FindFirstObjectByType<VisionBboxRenderer>();
            }

            if (maskRenderer == null)
            {
                maskRenderer = Object.FindFirstObjectByType<VisionMaskRenderer>();
            }

            if (bboxRenderer == null && maskRenderer == null)
            {
                return;
            }

            _renderPass.Setup(bboxRenderer, maskRenderer);
            renderer.EnqueuePass(_renderPass);
        }
    }
}
