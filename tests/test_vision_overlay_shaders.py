from pathlib import Path


SHADERS_DIR = Path("unity/Quest3Client/Assets/Shaders")


def read_shader(name: str) -> str:
    return (SHADERS_DIR / name).read_text(encoding="utf-8")


def test_overlay_line_shaders_use_urp_transparent_compute_buffer():
    for shader_name in ("VisionBboxLines.shader", "VisionMaskLines.shader"):
        contents = read_shader(shader_name)
        assert '"RenderPipeline" = "UniversalPipeline"' in contents
        assert '"Queue" = "Transparent"' in contents
        assert "Blend SrcAlpha OneMinusSrcAlpha" in contents
        assert "ZWrite Off" in contents
        assert "StructuredBuffer<LineVertexData> _LineVertices;" in contents
        assert "uint color;" in contents
        assert "#pragma target 4.5" in contents


def test_anchor_shader_is_transparent_and_instanced():
    contents = read_shader("VisionAnchorSphere.shader")
    assert '"RenderPipeline" = "UniversalPipeline"' in contents
    assert '"Queue" = "Transparent"' in contents
    assert "Blend SrcAlpha OneMinusSrcAlpha" in contents
    assert "#pragma multi_compile_instancing" in contents
    assert "UNITY_DEFINE_INSTANCED_PROP(float4, _InstanceColor)" in contents
