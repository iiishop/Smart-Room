using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;
using UnityEngine.Rendering.RenderGraphModule;

namespace SmartRoom.Vision
{
    public sealed class VisionRenderPass : ScriptableRenderPass
    {
        private VisionBboxRenderer _bboxRenderer;
        private VisionMaskRenderer _maskRenderer;

        public VisionRenderPass()
        {
            renderPassEvent = RenderPassEvent.AfterRenderingTransparents;
        }

        public void Setup(VisionBboxRenderer bboxRenderer, VisionMaskRenderer maskRenderer)
        {
            _bboxRenderer = bboxRenderer;
            _maskRenderer = maskRenderer;
        }

        [System.Obsolete]
        public override void Execute(ScriptableRenderContext context, ref RenderingData renderingData)
        {
            if ((_bboxRenderer == null || _bboxRenderer.VertexCount == 0) && (_maskRenderer == null || _maskRenderer.VertexCount == 0))
                return;

            CommandBuffer commandBuffer = CommandBufferPool.Get(nameof(VisionRenderPass));
            try
            {
                DrawRenderer(commandBuffer, _bboxRenderer);
                DrawRenderer(commandBuffer, _maskRenderer);
                context.ExecuteCommandBuffer(commandBuffer);
            }
            finally
            {
                CommandBufferPool.Release(commandBuffer);
            }
        }

        public override void RecordRenderGraph(RenderGraph renderGraph, ContextContainer frameData)
        {
            // Compatibility mode: rendering handled by Execute()
        }

        private static void DrawRenderer(CommandBuffer commandBuffer, VisionBboxRenderer renderer)
        {
            if (renderer == null || renderer.VertexCount == 0 || renderer.LineMaterial == null || renderer.LineBuffer == null)
                return;

            commandBuffer.DrawProcedural(Matrix4x4.identity, renderer.LineMaterial, 0, MeshTopology.Lines, renderer.VertexCount, 1);
        }

        private static void DrawRenderer(CommandBuffer commandBuffer, VisionMaskRenderer renderer)
        {
            if (renderer == null || renderer.VertexCount == 0 || renderer.LineMaterial == null || renderer.LineBuffer == null)
                return;

            commandBuffer.DrawProcedural(Matrix4x4.identity, renderer.LineMaterial, 0, MeshTopology.Lines, renderer.VertexCount, 1);
        }
    }
}
