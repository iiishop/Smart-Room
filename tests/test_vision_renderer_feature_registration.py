from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
EDITOR_SCRIPT = (
    REPO_ROOT
    / "unity"
    / "Quest3Client"
    / "Assets"
    / "Scripts"
    / "Rendering"
    / "Editor"
    / "RegisterBboxWireframeFeature.cs"
)


def test_register_script_auto_registers_vision_render_features():
    contents = EDITOR_SCRIPT.read_text(encoding="utf-8")

    assert "[InitializeOnLoadMethod]" in contents
    assert "EnsureFeaturesRegistered();" in contents
    assert '[MenuItem("SmartRoom/Register Vision Render Features")]' in contents
    assert 'RegisterFeaturesToRenderer(PcRendererPath);' in contents
    assert 'RegisterFeaturesToRenderer(MobileRendererPath);' in contents
    assert 'RegisterFeature<BboxWireframeRenderFeature>(data, path, "BboxWireframeRenderFeature")' in contents
    assert 'RegisterFeature<VisionRendererFeature>(data, path, "VisionRendererFeature")' in contents
    assert "AssetDatabase.AddObjectToAsset(featureInstance, assetPath);" in contents
    assert "AssetDatabase.SaveAssets();" in contents
    assert "AssetDatabase.Refresh();" in contents
