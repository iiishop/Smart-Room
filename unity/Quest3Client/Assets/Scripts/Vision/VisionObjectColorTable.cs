using System;
using UnityEngine;

#nullable enable

namespace SmartRoom.Vision
{
    public static class VisionObjectColorTable
    {
        public const int PaletteSize = 16;

        private static readonly Color32[] DefaultColors =
        {
            new(0xFF, 0x33, 0x33, 0xFF),
            new(0x33, 0xFF, 0x33, 0xFF),
            new(0x33, 0x66, 0xFF, 0xFF),
            new(0xFF, 0xFF, 0x1A, 0xFF),
            new(0xFF, 0x66, 0x1A, 0xFF),
            new(0x1A, 0xFF, 0xFF, 0xFF),
            new(0xFF, 0x1A, 0xFF, 0xFF),
            new(0x80, 0xFF, 0x33, 0xFF),
            new(0x33, 0x99, 0xFF, 0xFF),
            new(0xFF, 0x4D, 0x99, 0xFF),
            new(0x99, 0xCC, 0xFF, 0xFF),
            new(0xE6, 0xE6, 0x33, 0xFF),
            new(0x4D, 0xFF, 0x80, 0xFF),
            new(0xFF, 0x99, 0x1A, 0xFF),
            new(0x80, 0x66, 0xFF, 0xFF),
            new(0x1A, 0xE6, 0x4D, 0xFF),
        };

        public static Color32[] CreateDefaultPalette()
        {
            var copy = new Color32[DefaultColors.Length];
            Array.Copy(DefaultColors, copy, DefaultColors.Length);
            return copy;
        }

        public static Color32 GetColor(int objectId, Color32[]? palette = null)
        {
            Color32[] colors = palette != null && palette.Length > 0 ? palette : DefaultColors;
            int safeIndex = Mod(objectId, colors.Length);
            return colors[safeIndex];
        }

        private static int Mod(int value, int divisor)
        {
            int remainder = value % divisor;
            return remainder < 0 ? remainder + divisor : remainder;
        }
    }
}
