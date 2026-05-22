#if UNITY_EDITOR
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering.Universal;
using SmartRoom.Rendering;
using SmartRoom.Vision;

namespace SmartRoom.Rendering.Editor
{
    public static class RegisterBboxWireframeFeature
    {
        private const string PcRendererPath = "Assets/Settings/PC_Renderer.asset";
        private const string MobileRendererPath = "Assets/Settings/Mobile_Renderer.asset";

        [InitializeOnLoadMethod]
        private static void RegisterOnEditorLoad()
        {
            EnsureFeaturesRegistered();
        }

        [MenuItem("SmartRoom/Register Vision Render Features")]
        public static void RegisterVisionRenderFeatures()
        {
            bool changed = EnsureFeaturesRegistered();
            Debug.Log(changed
                ? "[SmartRoom] Vision render features registered to PC_Renderer and Mobile_Renderer"
                : "[SmartRoom] Vision render features already registered to PC_Renderer and Mobile_Renderer");
        }

        [MenuItem("SmartRoom/Register BboxWireframe Feature")]
        public static void Register()
        {
            RegisterVisionRenderFeatures();
        }

        private static bool EnsureFeaturesRegistered()
        {
            bool changed = false;
            changed |= RegisterFeaturesToRenderer(PcRendererPath);
            changed |= RegisterFeaturesToRenderer(MobileRendererPath);

            if (!changed)
                return false;

            AssetDatabase.SaveAssets();
            AssetDatabase.Refresh();
            return true;
        }

        private static bool RegisterFeaturesToRenderer(string path)
        {
            UniversalRendererData data = AssetDatabase.LoadAssetAtPath<UniversalRendererData>(path);
            if (data == null)
            {
                Debug.LogWarning($"[SmartRoom] Renderer data not found at {path}");
                return false;
            }

            bool changed = false;

            // BboxWireframeRenderFeature — resolved at runtime to avoid Unity 6 assembly resolution issues
            System.Type bboxType = System.Type.GetType("SmartRoom.Rendering.BboxWireframeRenderFeature, Assembly-CSharp");
            if (bboxType != null)
            {
                bool hasBbox = false;
                foreach (ScriptableRendererFeature f in data.rendererFeatures)
                {
                    if (f != null && bboxType.IsInstanceOfType(f)) { hasBbox = true; break; }
                }
                if (!hasBbox)
                {
                    var bboxFeature = (ScriptableRendererFeature)ScriptableObject.CreateInstance(bboxType);
                    bboxFeature.name = "BboxWireframeRenderFeature";
                    AssetDatabase.AddObjectToAsset(bboxFeature, path);
                    data.rendererFeatures.Add(bboxFeature);
                    EditorUtility.SetDirty(bboxFeature);
                    changed = true;
                }
            }
            else
            {
                Debug.LogWarning("[SmartRoom] BboxWireframeRenderFeature type not found at runtime, skipping registration");
            }

            // VisionRendererFeature — inline check to avoid Unity 6 generic resolution issues
            bool hasVision = false;
            foreach (ScriptableRendererFeature f in data.rendererFeatures)
            {
                if (f is VisionRendererFeature) { hasVision = true; break; }
            }
            if (!hasVision)
            {
                VisionRendererFeature visionFeature = ScriptableObject.CreateInstance<VisionRendererFeature>();
                visionFeature.name = "VisionRendererFeature";
                AssetDatabase.AddObjectToAsset(visionFeature, path);
                data.rendererFeatures.Add(visionFeature);
                EditorUtility.SetDirty(visionFeature);
                changed = true;
            }

            if (changed)
            {
                EditorUtility.SetDirty(data);
                Debug.Log($"[SmartRoom] Registered missing render features to {path}");
            }

            return changed;
        }
    }
}
#endif
