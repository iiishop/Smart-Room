#if UNITY_EDITOR
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering.Universal;

namespace SmartRoom.Rendering.Editor
{
    public static class RegisterBboxWireframeFeature
    {
        [MenuItem("SmartRoom/Register BboxWireframe Feature")]
        public static void Register()
        {
            RegisterToRenderer("Assets/Settings/PC_Renderer.asset");
            RegisterToRenderer("Assets/Settings/Mobile_Renderer.asset");
            AssetDatabase.SaveAssets();
            AssetDatabase.Refresh();
            Debug.Log("[SmartRoom] BboxWireframeRenderFeature registered to PC_Renderer and Mobile_Renderer");
        }

        private static void RegisterToRenderer(string path)
        {
            var data = AssetDatabase.LoadAssetAtPath<UniversalRendererData>(path);
            if (data == null)
            {
                Debug.LogWarning($"[SmartRoom] Renderer data not found at {path}");
                return;
            }

            foreach (var f in data.rendererFeatures)
            {
                if (f is BboxWireframeRenderFeature)
                {
                    Debug.Log($"[SmartRoom] Already registered in {path}");
                    return;
                }
            }

            var feature = ScriptableObject.CreateInstance<BboxWireframeRenderFeature>();
            feature.name = "BboxWireframeRenderFeature";
            data.rendererFeatures.Add(feature);
            EditorUtility.SetDirty(data);
            Debug.Log($"[SmartRoom] Registered to {path}");
        }
    }
}
#endif
