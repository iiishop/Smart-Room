using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;

namespace SmartRoom.Rendering
{
    public sealed class BboxWireframeRenderPass : ScriptableRenderPass
    {
        private const int VerticesPerInstance = 24;
        private static readonly int InstanceDataId = Shader.PropertyToID("_InstanceData");
        private static readonly int LineAlphaId = Shader.PropertyToID("_LineAlpha");

        private ComputeBuffer _buffer;
        private int _instanceCount;
        private Material _material;
        private float _lineAlpha;

        public BboxWireframeRenderPass()
        {
            renderPassEvent = RenderPassEvent.AfterRenderingTransparents;
        }

        public void Setup(ComputeBuffer buffer, int instanceCount, Material material, float lineAlpha)
        {
            _buffer = buffer;
            _instanceCount = instanceCount;
            _material = material;
            _lineAlpha = lineAlpha;
        }

        public override void Execute(ScriptableRenderContext context, ref RenderingData renderingData)
        {
            if (_buffer == null || _instanceCount <= 0 || _material == null)
            {
                return;
            }

            CommandBuffer cmd = CommandBufferPool.Get(nameof(BboxWireframeRenderPass));
            try
            {
                cmd.SetGlobalBuffer(InstanceDataId, _buffer);
                cmd.SetGlobalFloat(LineAlphaId, _lineAlpha);
                cmd.DrawProcedural(Matrix4x4.identity, _material, 0, MeshTopology.Lines,
                    VerticesPerInstance, _instanceCount);
                context.ExecuteCommandBuffer(cmd);
            }
            finally
            {
                CommandBufferPool.Release(cmd);
            }
        }
    }
}
