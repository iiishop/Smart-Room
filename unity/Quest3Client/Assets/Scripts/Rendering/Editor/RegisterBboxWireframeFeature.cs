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
            {
                return false;
            }

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
            changed |= RegisterFeature<BboxWireframeRenderFeature>(data, path, "BboxWireframeRenderFeature");
            changed |= RegisterFeature<VisionRendererFeature>(data, path, "VisionRendererFeature");

            if (changed)
            {
                EditorUtility.SetDirty(data);
                Debug.Log($"[SmartRoom] Registered missing render features to {path}");
            }
            else
            {
                Debug.Log($"[SmartRoom] Render features already present in {path}");
            }

            return changed;
        }

        private static bool RegisterFeature<TFeature>(UniversalRendererData data, string assetPath, string featureName)
            where TFeature : ScriptableRendererFeature
        {
            foreach (ScriptableRendererFeature feature in data.rendererFeatures)
            {
                if (feature is TFeature)
                {
                    return false;
                }
            }

            TFeature featureInstance = ScriptableObject.CreateInstance<TFeature>();
            featureInstance.name = featureName;
            AssetDatabase.AddObjectToAsset(featureInstance, assetPath);
            data.rendererFeatures.Add(featureInstance);
            EditorUtility.SetDirty(featureInstance);
            EditorUtility.SetDirty(data);
            return true;
        }
    }
}
#endif
