using System;
using UnityEngine;

namespace SmartRoom.Rendering
{
    [CreateAssetMenu(fileName = "VisionOverlayConfig", menuName = "Smart Room/Vision Overlay Config")]
    public sealed class VisionOverlayConfig : ScriptableObject
    {
        private const int PaletteSize = 8;

        [SerializeField] private Color32[] palette = new Color32[PaletteSize]
        {
            new Color32(0xF4, 0x43, 0x36, 0xFF),
            new Color32(0xE9, 0x1E, 0x63, 0xFF),
            new Color32(0x9C, 0x27, 0xB0, 0xFF),
            new Color32(0x3F, 0x51, 0xB5, 0xFF),
            new Color32(0x03, 0xA9, 0xF4, 0xFF),
            new Color32(0x4C, 0xAF, 0x50, 0xFF),
            new Color32(0xFF, 0x98, 0x00, 0xFF),
            new Color32(0xFF, 0x57, 0x22, 0xFF)
        };

        [SerializeField, Min(1)] private int maxObjects = 8;
        [SerializeField, Min(1)] private int labelFontSize = 24;
        [SerializeField] private bool debugOverlayEnabled;

        public Color32[] Palette => palette;
        public int MaxObjects => maxObjects;
        public int LabelFontSize => labelFontSize;
        public bool DebugOverlayEnabled => debugOverlayEnabled;

        public Color32 GetPaletteColor(int index)
        {
            if (palette == null || palette.Length == 0)
            {
                return new Color32(255, 255, 255, 255);
            }

            int safeIndex = Mathf.Abs(index % palette.Length);
            return palette[safeIndex];
        }

        private void OnValidate()
        {
            maxObjects = Mathf.Max(1, maxObjects);
            labelFontSize = Mathf.Max(1, labelFontSize);
            NormalizePalette();
        }

        private void NormalizePalette()
        {
            if (palette != null && palette.Length == PaletteSize)
            {
                return;
            }

            Color32[] normalized = new Color32[PaletteSize];
            if (palette != null)
            {
                Array.Copy(palette, normalized, Math.Min(palette.Length, PaletteSize));
            }

            palette = normalized;
        }
    }
}
