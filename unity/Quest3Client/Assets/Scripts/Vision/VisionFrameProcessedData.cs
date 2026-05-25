using System;
using UnityEngine;

namespace SmartRoom.Vision
{
    [Serializable]
    public sealed class VisionFrameProcessedData
    {
        public VisionFrameProcessedData()
        {
            Objects = Array.Empty<VisionObjectProcessedData>();
        }

        public int FrameId;
        public long TimestampMs;
        public VisionObjectProcessedData[] Objects = Array.Empty<VisionObjectProcessedData>();
    }

    [Serializable]
    public sealed class VisionObjectProcessedData
    {
        public VisionObjectProcessedData()
        {
            Label = string.Empty;
        }

        public int ObjectId;
        public string Label;
        public float Score;
        public Vector3 Center3D;
        public bool CenterValid;
        public float DepthMeters;   // distance from camera for dynamic worldScale

        // Mask overlay data: size = [height, width], counts = RLE run lengths
        public int MaskHeight;
        public int MaskWidth;
        public int[] MaskCounts;
    }
}
