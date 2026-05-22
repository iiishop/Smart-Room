from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
VISION_DIR = REPO_ROOT / "unity" / "Quest3Client" / "Assets" / "Scripts" / "Vision"
RENDERING_DIR = REPO_ROOT / "unity" / "Quest3Client" / "Assets" / "Scripts" / "Rendering"


def read_script(name: str) -> str:
    return (VISION_DIR / name).read_text(encoding="utf-8")


def read_rendering_script(name: str) -> str:
    return (RENDERING_DIR / name).read_text(encoding="utf-8")


def test_compute_buffer_rendering_core_scripts_exist_with_expected_contracts():
    bbox_renderer = read_script("VisionBboxRenderer.cs")
    mask_renderer = read_script("VisionMaskRenderer.cs")
    overlay_manager = read_script("VisionOverlayManager.cs")
    renderer_feature = read_script("VisionRendererFeature.cs")
    render_pass = read_script("VisionRenderPass.cs")
    geometry = read_script("VisionBboxGeometry.cs")
    wireframe_manager = read_rendering_script("BboxWireframeManager.cs")

    assert "new ComputeBuffer" in bbox_renderer
    assert "catch (Exception ex)" in bbox_renderer
    assert 'Debug.LogError($"Failed to allocate vision bbox buffer: {ex}")' in bbox_renderer
    assert 'Debug.LogError($"Failed to upload vision bbox vertices for this frame: {ex}")' in bbox_renderer
    assert "public bool IsEnabled => !_gpuRenderingDisabled" in bbox_renderer
    assert "_vertexCount = 0;" in bbox_renderer
    assert "public void UpdateBuffers(VisionObjectProcessedData[] objects)" in bbox_renderer
    assert "VisionBboxGeometry.WriteBboxLineVertices" in bbox_renderer
    assert "new ComputeBuffer" in mask_renderer
    assert "catch (Exception ex)" in mask_renderer
    assert 'Debug.LogError($"Failed to allocate vision mask buffer: {ex}")' in mask_renderer
    assert 'Debug.LogError($"Failed to upload vision mask vertices for this frame: {ex}")' in mask_renderer
    assert "public bool IsEnabled => !_gpuRenderingDisabled" in mask_renderer
    assert "public void UpdateContours(VisionObjectProcessedData[] objects)" in mask_renderer
    assert "VisionBboxGeometry.WriteClosedContourLineVertices" in mask_renderer
    assert "catch (System.Exception ex)" in wireframe_manager
    assert 'Debug.LogError($"Failed to allocate bbox wireframe buffer: {ex}")' in wireframe_manager
    assert 'Debug.LogError($"Failed to upload bbox wireframe data for this frame: {ex}")' in wireframe_manager
    assert "public int ActiveCount => _renderableCount;" in wireframe_manager
    assert "_renderableCount = 0;" in wireframe_manager
    assert "receiverModule.OnFrameProcessed += HandleFrameProcessed;" in overlay_manager
    assert "bboxRenderer?.UpdateBuffers(objects);" in overlay_manager
    assert "maskRenderer?.UpdateContours(objects);" in overlay_manager
    assert "labelPool?.SyncObjects(objects);" in overlay_manager
    assert "ScriptableRendererFeature" in renderer_feature
    assert "RenderPassEvent.AfterRenderingTransparents" in renderer_feature
    assert "DrawProcedural" in render_pass
    assert "MeshTopology.Lines" in render_pass
    assert "VerticesPerBox = EdgeCount * VerticesPerEdge" in geometry
    assert "WriteClosedContourLineVertices" in geometry
