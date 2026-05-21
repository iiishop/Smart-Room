from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
VISION_DIR = REPO_ROOT / "unity" / "Quest3Client" / "Assets" / "Scripts" / "Vision"


def read_script(name: str) -> str:
    return (VISION_DIR / name).read_text(encoding="utf-8")


def test_compute_buffer_rendering_core_scripts_exist_with_expected_contracts():
    bbox_renderer = read_script("VisionBboxRenderer.cs")
    mask_renderer = read_script("VisionMaskRenderer.cs")
    renderer_feature = read_script("VisionRendererFeature.cs")
    render_pass = read_script("VisionRenderPass.cs")
    geometry = read_script("VisionBboxGeometry.cs")

    assert "new ComputeBuffer" in bbox_renderer
    assert "receiverModule.OnFrameProcessed += ApplyFrame;" in bbox_renderer
    assert "VisionBboxGeometry.WriteBboxLineVertices" in bbox_renderer
    assert "new ComputeBuffer" in mask_renderer
    assert "VisionBboxGeometry.WriteClosedContourLineVertices" in mask_renderer
    assert "ScriptableRendererFeature" in renderer_feature
    assert "RenderPassEvent.AfterRenderingTransparents" in renderer_feature
    assert "DrawProcedural" in render_pass
    assert "MeshTopology.Lines" in render_pass
    assert "VerticesPerBox = EdgeCount * VerticesPerEdge" in geometry
    assert "WriteClosedContourLineVertices" in geometry
