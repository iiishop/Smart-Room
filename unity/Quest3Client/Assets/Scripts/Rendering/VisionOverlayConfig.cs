using System;
using UnityEngine.Serialization;
using UnityEngine;

namespace SmartRoom.Rendering
{
    [CreateAssetMenu(fileName = "VisionOverlayConfig", menuName = "Smart Room/Vision Overlay Config")]
    public sealed class VisionOverlayConfig : ScriptableObject
    {
        private const int PaletteSize = 8;

        [SerializeField] private bool enabled = true;
        [SerializeField] private bool showBoundingBoxes = true;
        [SerializeField] private bool showLabels = true;
        [SerializeField] private bool showAnchors;
        [SerializeField, Min(0f)] private float boxLineWidth = 0.01f;
        [SerializeField, Min(0f)] private float labelHeightOffset = 0.03f;
        [FormerlySerializedAs("palette")]
        [SerializeField] private Color32[] objectColors = new Color32[PaletteSize]
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

        public bool Enabled => enabled;
        public bool ShowBoundingBoxes => showBoundingBoxes;
        public bool ShowLabels => showLabels;
        public bool ShowAnchors => showAnchors;
        public float BoxLineWidth => boxLineWidth;
        public float LabelHeightOffset => labelHeightOffset;
        public Color32[] ObjectColors => GetObjectColorsCopy();
        public int MaxObjects => maxObjects;
        public int LabelFontSize => labelFontSize;

        public Color32 GetObjectColor(int index)
        {
            if (objectColors == null || objectColors.Length == 0)
            {
                return new Color32(255, 255, 255, 255);
            }

            int safeIndex = Mathf.Abs(index % objectColors.Length);
            return objectColors[safeIndex];
        }

        private void OnValidate()
        {
            maxObjects = Mathf.Max(1, maxObjects);
            labelFontSize = Mathf.Max(1, labelFontSize);
            boxLineWidth = Mathf.Max(0f, boxLineWidth);
            labelHeightOffset = Mathf.Max(0f, labelHeightOffset);
            NormalizeObjectColors();
        }

        private Color32[] GetObjectColorsCopy()
        {
            if (objectColors == null || objectColors.Length == 0)
            {
                return Array.Empty<Color32>();
            }

            Color32[] copy = new Color32[objectColors.Length];
            Array.Copy(objectColors, copy, objectColors.Length);
            return copy;
        }

        private void NormalizeObjectColors()
        {
            if (objectColors != null && objectColors.Length == PaletteSize)
            {
                return;
            }

            Color32[] normalized = new Color32[PaletteSize];
            if (objectColors != null)
            {
                Array.Copy(objectColors, normalized, Math.Min(objectColors.Length, PaletteSize));
            }

            objectColors = normalized;
        }
    }
}
