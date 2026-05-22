using System;
using SmartRoom.Vision;
using UnityEngine.Serialization;
using UnityEngine;

namespace SmartRoom.Rendering
{
    [CreateAssetMenu(fileName = "VisionOverlayConfig", menuName = "Smart Room/Vision Overlay Config")]
    public sealed class VisionOverlayConfig : ScriptableObject
    {
        private const int PaletteSize = VisionObjectColorTable.PaletteSize;

        [SerializeField] private bool enabled = true;
        [SerializeField] private bool showBoundingBoxes = true;
        [SerializeField] private bool showLabels = true;
        [SerializeField] private bool showAnchors;
        [SerializeField, Min(0f)] private float boxLineWidth = 0.01f;
        [SerializeField, Min(0f)] private float labelHeightOffset = 0.03f;
        [FormerlySerializedAs("palette")]
        [SerializeField] private Color32[] objectColors = VisionObjectColorTable.CreateDefaultPalette();

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
            return VisionObjectColorTable.GetColor(index, objectColors);
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

            Color32[] normalized = VisionObjectColorTable.CreateDefaultPalette();
            if (objectColors != null)
            {
                Array.Copy(objectColors, normalized, Math.Min(objectColors.Length, PaletteSize));
            }

            objectColors = normalized;
        }
    }
}
