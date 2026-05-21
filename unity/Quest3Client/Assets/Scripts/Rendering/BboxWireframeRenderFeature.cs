using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;

namespace SmartRoom.Rendering
{
    public class BboxWireframeRenderFeature : ScriptableRendererFeature
    {
        [System.Serializable]
        public class Settings
        {
        }

        public Settings settings = new();

        private BboxWireframeRenderPass _renderPass;
        private BboxWireframeManager _cachedManager;

        public override void Create()
        {
            _renderPass = new BboxWireframeRenderPass
            {
                renderPassEvent = RenderPassEvent.AfterRenderingTransparents
            };
        }

        public override void AddRenderPasses(ScriptableRenderer renderer, ref RenderingData renderingData)
        {
            if (_renderPass == null)
            {
                return;
            }

            if (_cachedManager == null)
            {
                _cachedManager = FindFirstObjectByType<BboxWireframeManager>();
            }

            if (_cachedManager == null)
            {
                return;
            }

            if (!_cachedManager.isActiveAndEnabled || _cachedManager.ActiveCount <= 0 || _cachedManager.Material == null)
            {
                return;
            }

            CameraData cameraData = renderingData.cameraData;
            if (cameraData.cameraType != CameraType.Game && cameraData.cameraType != CameraType.SceneView)
            {
                return;
            }

            _renderPass.manager = _cachedManager;
            renderer.EnqueuePass(_renderPass);
        }

        protected override void Dispose(bool disposing)
        {
            _renderPass = null;
            _cachedManager = null;
        }

        private class BboxWireframeRenderPass : ScriptableRenderPass
        {
            public BboxWireframeManager manager;

            public override void Execute(ScriptableRenderContext context, ref RenderingData renderingData)
            {
                if (manager == null || manager.ActiveCount <= 0 || manager.Material == null)
                {
                    return;
                }

                CommandBuffer cmd = CommandBufferPool.Get("BboxWireframe");
                manager.DrawWithCommandBuffer(cmd);
                context.ExecuteCommandBuffer(cmd);
                CommandBufferPool.Release(cmd);
            }
        }
    }
}
