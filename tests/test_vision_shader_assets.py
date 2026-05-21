from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SHADER_DIR = REPO_ROOT / "unity" / "Quest3Client" / "Assets" / "Shaders"


def read_shader(name: str) -> str:
    return (SHADER_DIR / name).read_text(encoding="utf-8")


class VisionShaderAssetTests(unittest.TestCase):
    def test_vision_line_unlit_matches_draw_procedural_contract(self) -> None:
        content = read_shader("VisionLineUnlit.shader")

        self.assertIn('Shader "SmartRoom/VisionLineUnlit"', content)
        self.assertIn("StructuredBuffer<LineVertexData> _LineVertices;", content)
        self.assertIn("TransformWorldToHClip(vertex.positionWS)", content)
        self.assertIn("Blend SrcAlpha OneMinusSrcAlpha", content)
        self.assertIn("ZTest Always", content)

    def test_vision_overlay_unlit_matches_canvas_contract(self) -> None:
        content = read_shader("VisionOverlayUnlit.shader")

        self.assertIn('Shader "SmartRoom/VisionOverlayUnlit"', content)
        self.assertIn('[PerRendererData] _MainTex ("Sprite Texture", 2D) = "white" {}', content)
        self.assertIn("#pragma multi_compile_local _ UNITY_UI_CLIP_RECT", content)
        self.assertIn("ZTest [unity_GUIZTestMode]", content)
        self.assertIn('"CanUseSpriteAtlas" = "True"', content)
