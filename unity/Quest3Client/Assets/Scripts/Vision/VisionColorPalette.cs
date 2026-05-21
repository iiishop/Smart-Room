using System;
using UnityEngine;

namespace SmartRoom.Vision
{
    [Serializable]
    public sealed class VisionLabelColorEntry
    {
        public string Label;
        public Color color = Color.white;
    }

    [CreateAssetMenu(menuName = "SmartRoom/Vision Color Palette", fileName = "VisionColorPalette")]
    public sealed class VisionColorPalette : ScriptableObject
    {
        public VisionLabelColorEntry[] entries = Array.Empty<VisionLabelColorEntry>();

        public bool TryGetColor(string label, out Color color)
        {
            if (!string.IsNullOrWhiteSpace(label))
            {
                for (int i = 0; i < entries.Length; i++)
                {
                    VisionLabelColorEntry entry = entries[i];
                    if (entry != null && string.Equals(entry.Label, label, StringComparison.Ordinal))
                    {
                        color = entry.color;
                        return true;
                    }
                }
            }

            color = Color.white;
            return false;
        }
    }
}
