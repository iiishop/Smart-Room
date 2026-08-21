using UnityEngine;

namespace SmartRoom.Vision
{
    [CreateAssetMenu(menuName = "SmartRoom/Vision Render Config", fileName = "VisionRenderConfig")]
    public sealed class VisionRenderConfig : ScriptableObject
    {
        public bool enabled = true;
        public int maxObjects = 60;
        public float bboxLineWidth = 0.01f;
        public Vector3 labelOffset = new Vector3(0f, 0.05f, 0f);
    }
}
