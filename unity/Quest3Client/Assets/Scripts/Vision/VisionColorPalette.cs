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
        [SerializeField] private Color32[] objectColors = VisionObjectColorTable.CreateDefaultPalette();
        public VisionLabelColorEntry[] entries = Array.Empty<VisionLabelColorEntry>();

        public Color32 ResolveColor(int objectId)
        {
            return VisionObjectColorTable.GetColor(objectId, objectColors);
        }

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

        private void OnValidate()
        {
            if (objectColors != null && objectColors.Length == VisionObjectColorTable.PaletteSize)
            {
                return;
            }

            Color32[] normalized = VisionObjectColorTable.CreateDefaultPalette();
            if (objectColors != null)
            {
                Array.Copy(objectColors, normalized, Math.Min(objectColors.Length, normalized.Length));
            }

            objectColors = normalized;
        }
    }
}
