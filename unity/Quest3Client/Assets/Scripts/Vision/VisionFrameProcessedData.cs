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
            Corners3D = Array.Empty<Vector3>();
            Contour3D = Array.Empty<Vector3>();
        }

        public int ObjectId;
        public string Label;
        public float Score;
        public Vector3[] Corners3D = Array.Empty<Vector3>();
        public Vector3 Center3D;
        public Vector3[] Contour3D = Array.Empty<Vector3>();
        public bool CornersValid;
        public bool CenterValid;
    }
}
