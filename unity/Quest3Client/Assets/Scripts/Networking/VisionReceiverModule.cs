using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Text;
using Meta.XR;
using SmartRoom.Vision;
using UnityEngine;

namespace SmartRoom.Networking
{
    public readonly struct VisionWorldObject
    {
        public VisionWorldObject(int objectId, string label, float score, Vector3 worldPosition, int hitCount)
        {
            ObjectId = objectId;
            Label = label ?? string.Empty;
            Score = score;
            WorldPosition = worldPosition;
            HitCount = hitCount;
        }

        public int ObjectId { get; }
        public string Label { get; }
        public float Score { get; }
        public Vector3 WorldPosition { get; }
        public int HitCount { get; }
    }

    public class VisionReceiverModule : MonoBehaviour
    {
        public event Action<WorldPosition[]> ObjectsProcessed;
        public event Action<VisionFrameProcessedData> OnFrameProcessed;

        [SerializeField] private BackendCommunicationManager manager;
        [SerializeField] private DepthStreamModule depthStreamModule;
        [SerializeField] private BboxWireframeManager wireframeManager;
        [SerializeField] private Camera rayCamera;
        [SerializeField] private PassthroughCameraAccess passthroughCameraAccess;
        [SerializeField] private int samplesPerObject = 3;
        [SerializeField] private int contourSamplesPerObject = 12;
        [SerializeField] private int maxObjectsPerFrame = 8;
        [SerializeField] private float bboxDepthOffsetMeters = 0.05f;

        private bool _loggedRaySource;
        private bool _loggedPassthroughFallback;
        private readonly ConcurrentQueue<string> _pendingVisionMessages = new ConcurrentQueue<string>();
        private readonly List<VisionWorldObject> _worldObjectsBuffer = new List<VisionWorldObject>(16);
        private VisionWorldObject[] _latestWorldObjects = Array.Empty<VisionWorldObject>();

        public event Action<VisionWorldObject[]> WorldObjectsUpdated;
        public VisionWorldObject[] LatestWorldObjects => _latestWorldObjects;

        private void Awake()
        {
            if (manager == null)
            {
                manager = FindFirstObjectByType<BackendCommunicationManager>();
            }

            if (depthStreamModule == null)
            {
                depthStreamModule = FindFirstObjectByType<DepthStreamModule>();
            }

            if (rayCamera == null)
            {
                rayCamera = Camera.main;
            }

            if (passthroughCameraAccess == null)
            {
                passthroughCameraAccess = FindFirstObjectByType<PassthroughCameraAccess>();
            }

            if (wireframeManager == null)
            {
                wireframeManager = FindFirstObjectByType<BboxWireframeManager>();
            }

            samplesPerObject = Mathf.Clamp(samplesPerObject, 1, 16);
            contourSamplesPerObject = Mathf.Clamp(contourSamplesPerObject, 4, 32);
            maxObjectsPerFrame = Mathf.Clamp(maxObjectsPerFrame, 1, 64);
        }

        private void OnEnable()
        {
            if (manager != null)
            {
                manager.VisionMessageReceived += OnVisionMessage;
            }
        }

        private void OnDisable()
        {
            if (manager != null)
            {
                manager.VisionMessageReceived -= OnVisionMessage;
            }

            while (_pendingVisionMessages.TryDequeue(out _))
            {
            }

            PublishWorldObjects(Array.Empty<VisionWorldObject>());
        }

        private void Update()
        {
            ProcessLatestVisionMessage();
        }

        private void OnVisionMessage(string json)
        {
            if (string.IsNullOrWhiteSpace(json))
            {
                return;
            }

            _pendingVisionMessages.Enqueue(json);
        }

        private void ProcessLatestVisionMessage()
        {
            if (manager == null || depthStreamModule == null)
            {
                if (_latestWorldObjects.Length > 0)
                {
                    PublishWorldObjects(Array.Empty<VisionWorldObject>());
                }

                return;
            }

            string latestJson = null;
            while (_pendingVisionMessages.TryDequeue(out string json))
            {
                latestJson = json;
            }

            if (latestJson == null)
            {
                return;
            }

            VisionFramePayload frame;
            try
            {
                frame = JsonUtility.FromJson<VisionFramePayload>(latestJson);
            }
            catch (Exception ex)
            {
                manager.QueueUnityLog("WARNING", $"Vision payload parse failed: {ex.Message}");
                return;
            }

            if (frame?.objects == null || frame.objects.Length == 0)
            {
                PublishWorldObjects(Array.Empty<VisionWorldObject>());
                EmitProcessedFrame(frame, Array.Empty<VisionWorldObject>());
                return;
            }

            _worldObjectsBuffer.Clear();
            int objectCount = Mathf.Min(frame.objects.Length, maxObjectsPerFrame);
            var processedObjects = new WorldPosition[objectCount * samplesPerObject];
            int processedCount = 0;
            for (int objectIndex = 0; objectIndex < objectCount; objectIndex++)
            {
                VisionTrackedMaskPayload trackedMask = frame.objects[objectIndex];
                if (trackedMask?.mask_rle == null)
                {
                    continue;
                }

                VisionMaskSamplePoint[] samples = VisionMaskSampling.SampleMaskPixels(frame, trackedMask, samplesPerObject);
                if (samples.Length == 0)
                {
                    manager.QueueUnityLog("WARNING", $"Vision object_id={trackedMask.object_id} has no foreground pixels to sample.");
                    continue;
                }

                var successful = new StringBuilder();
                int hitCount = 0;
                Vector3 accumulatedWorldPoint = Vector3.zero;
                for (int sampleIndex = 0; sampleIndex < samples.Length; sampleIndex++)
                {
                    VisionMaskSamplePoint sample = samples[sampleIndex];
                    if (!TryGetViewportRay(sample.ViewportU, sample.ViewportV, out Ray ray, out Transform rayTransform))
                    {
                        continue;
                    }

                    if (!depthStreamModule.TryRaycastViewport(sample.ViewportU, sample.ViewportV, ray, rayTransform, out float depthM, out Vector3 worldPoint, out _))
                    {
                        continue;
                    }

                    if (successful.Length > 0)
                    {
                        successful.Append("; ");
                    }

                    successful.AppendFormat(
                        "pixel=({0},{1}) viewport=({2:F3},{3:F3}) world=({4:F3},{5:F3},{6:F3}) depth={7:F3}",
                        sample.PixelX,
                        sample.PixelY,
                        sample.ViewportU,
                        sample.ViewportV,
                        worldPoint.x,
                        worldPoint.y,
                        worldPoint.z,
                        depthM
                    );
                    accumulatedWorldPoint += worldPoint;
                    processedObjects[processedCount++] = VisionWorldPositionFactory.Create(
                        trackedMask.object_id,
                        trackedMask.label,
                        trackedMask.score,
                        worldPoint.x,
                        worldPoint.y,
                        worldPoint.z,
                        depthM);
                    hitCount++;
                }

                if (hitCount > 0)
                {
                    string label = string.IsNullOrWhiteSpace(trackedMask.label) ? "unknown" : trackedMask.label;
                    Vector3 averagedWorldPoint = accumulatedWorldPoint / hitCount;
                    _worldObjectsBuffer.Add(
                        new VisionWorldObject(
                            trackedMask.object_id,
                            label,
                            trackedMask.score,
                            averagedWorldPoint,
                            hitCount));
                    manager.QueueUnityLog(
                        "INFO",
                        $"Vision object_id={trackedMask.object_id} label={label} hits={hitCount}/{samples.Length} samples=[{successful}]"
                    );
                }
                else
                {
                    manager.QueueUnityLog(
                        "WARNING",
                        $"Vision object_id={trackedMask.object_id} produced no 3D hits from {samples.Length} sampled mask pixels."
                    );
                }
            }

            var publishedObjects = new WorldPosition[processedCount];
            Array.Copy(processedObjects, publishedObjects, processedCount);
            ObjectsProcessed?.Invoke(publishedObjects);

            if (wireframeManager != null)
            {
                wireframeManager.ClearAll();
                for (int wfIdx = 0; wfIdx < objectCount; wfIdx++)
                {
                    VisionTrackedMaskPayload wfMask = frame.objects[wfIdx];
                    if (wfMask?.box_xyxy == null || wfMask.box_xyxy.Length != 4)
                    {
                        continue;
                    }

                    TryBuildBboxCorners(frame, wfMask, out Vector3[] eightCorners);
                    if (eightCorners == null)
                    {
                        continue;
                    }

                    Color color = VisionObjectColorTable.GetColor(wfMask.object_id);
                    wireframeManager.SetBboxData(wfMask.object_id, eightCorners, color);
                }

            }

            VisionWorldObject[] worldObjects = _worldObjectsBuffer.ToArray();
            PublishWorldObjects(worldObjects);
            EmitProcessedFrame(frame, worldObjects);
        }

        private void EmitProcessedFrame(VisionFramePayload frame, VisionWorldObject[] worldObjects)
        {
            if (frame == null)
            {
                return;
            }

            var processedFrame = new VisionFrameProcessedData
            {
                FrameId = frame.frame_id,
                TimestampMs = frame.timestamp_ms,
                Objects = Array.Empty<VisionObjectProcessedData>()
            };

            if (worldObjects == null || worldObjects.Length == 0 || frame.objects == null || frame.objects.Length == 0)
            {
                OnFrameProcessed?.Invoke(processedFrame);
                return;
            }

            var trackedObjectMap = new Dictionary<int, VisionTrackedMaskPayload>(frame.objects.Length);
            for (int index = 0; index < frame.objects.Length; index++)
            {
                VisionTrackedMaskPayload trackedMask = frame.objects[index];
                if (trackedMask != null)
                {
                    trackedObjectMap[trackedMask.object_id] = trackedMask;
                }
            }

            var processedObjects = new VisionObjectProcessedData[worldObjects.Length];
            for (int index = 0; index < worldObjects.Length; index++)
            {
                VisionWorldObject worldObject = worldObjects[index];
                trackedObjectMap.TryGetValue(worldObject.ObjectId, out VisionTrackedMaskPayload trackedMask);

                Vector3[] corners = Array.Empty<Vector3>();
                bool cornersValid = trackedMask != null && TryBuildBboxCorners(frame, trackedMask, out corners);

                Vector3[] contour = Array.Empty<Vector3>();
                if (trackedMask != null)
                {
                    contour = BuildContourWorldPoints(frame, trackedMask);
                }

                processedObjects[index] = new VisionObjectProcessedData
                {
                    ObjectId = worldObject.ObjectId,
                    Label = worldObject.Label,
                    Score = worldObject.Score,
                    Corners3D = corners,
                    Center3D = worldObject.WorldPosition,
                    Contour3D = contour,
                    CornersValid = cornersValid,
                    CenterValid = true
                };
            }

            processedFrame.Objects = processedObjects;
            OnFrameProcessed?.Invoke(processedFrame);
        }

        private Vector3[] BuildContourWorldPoints(VisionFramePayload frame, VisionTrackedMaskPayload trackedMask)
        {
            Vector2[] contourPixels = DecodeMaskContourPixels(trackedMask);
            if (contourPixels.Length < 2)
            {
                return Array.Empty<Vector3>();
            }

            int frameWidth = frame.frame_width > 0 ? frame.frame_width : trackedMask.mask_rle.size[1];
            int frameHeight = frame.frame_height > 0 ? frame.frame_height : trackedMask.mask_rle.size[0];
            var contourPoints = new List<Vector3>(contourPixels.Length);
            for (int index = 0; index < contourPixels.Length; index++)
            {
                Vector2 pixel = contourPixels[index];
                float u = (pixel.x + 0.5f) / frameWidth;
                float v = 1f - ((pixel.y + 0.5f) / frameHeight);
                if (!TryGetViewportRay(u, v, out Ray ray, out Transform rayTransform))
                {
                    continue;
                }

                if (!depthStreamModule.TryRaycastViewport(u, v, ray, rayTransform, out _, out Vector3 worldPoint, out _))
                {
                    continue;
                }

                contourPoints.Add(worldPoint);
            }

            return contourPoints.Count >= 2 ? contourPoints.ToArray() : Array.Empty<Vector3>();
        }

        private Vector2[] DecodeMaskContourPixels(VisionTrackedMaskPayload trackedMask)
        {
            if (trackedMask?.mask_rle?.size == null || trackedMask.mask_rle.size.Length != 2 || trackedMask.mask_rle.counts == null)
            {
                return Array.Empty<Vector2>();
            }

            int maskHeight = trackedMask.mask_rle.size[0];
            int maskWidth = trackedMask.mask_rle.size[1];
            if (maskHeight <= 0 || maskWidth <= 0)
            {
                return Array.Empty<Vector2>();
            }

            int[] counts = trackedMask.mask_rle.counts;
            var mask = new bool[maskWidth * maskHeight];
            int flatIndex = 0;
            bool isForegroundRun = false;
            for (int runIndex = 0; runIndex < counts.Length; runIndex++)
            {
                int runLength = counts[runIndex];
                if (runLength < 0 || flatIndex + runLength > mask.Length)
                {
                    return Array.Empty<Vector2>();
                }

                if (isForegroundRun)
                {
                    for (int offset = 0; offset < runLength; offset++)
                    {
                        mask[flatIndex + offset] = true;
                    }
                }

                flatIndex += runLength;
                isForegroundRun = !isForegroundRun;
            }

            if (flatIndex != mask.Length)
            {
                return Array.Empty<Vector2>();
            }

            var boundaryPixels = new List<Vector2>();
            for (int y = 0; y < maskHeight; y++)
            {
                for (int x = 0; x < maskWidth; x++)
                {
                    int pixelIndex = (y * maskWidth) + x;
                    if (!mask[pixelIndex])
                    {
                        continue;
                    }

                    bool touchesBackground =
                        x == 0 || !mask[pixelIndex - 1] ||
                        x == maskWidth - 1 || !mask[pixelIndex + 1] ||
                        y == 0 || !mask[pixelIndex - maskWidth] ||
                        y == maskHeight - 1 || !mask[pixelIndex + maskWidth];

                    if (touchesBackground)
                    {
                        boundaryPixels.Add(new Vector2(x, y));
                    }
                }
            }

            if (boundaryPixels.Count < 2)
            {
                return Array.Empty<Vector2>();
            }

            int sampleCount = Mathf.Min(contourSamplesPerObject, boundaryPixels.Count);
            var sampledPixels = new List<Vector2>(sampleCount);
            if (sampleCount == 1)
            {
                sampledPixels.Add(boundaryPixels[0]);
            }
            else
            {
                for (int index = 0; index < sampleCount; index++)
                {
                    float t = sampleCount == 1 ? 0f : (float)index / (sampleCount - 1);
                    int pixelIndex = Mathf.Clamp(Mathf.RoundToInt(t * (boundaryPixels.Count - 1)), 0, boundaryPixels.Count - 1);
                    sampledPixels.Add(boundaryPixels[pixelIndex]);
                }
            }

            Vector2 centroid = Vector2.zero;
            for (int index = 0; index < sampledPixels.Count; index++)
            {
                centroid += sampledPixels[index];
            }

            centroid /= sampledPixels.Count;
            sampledPixels.Sort((left, right) =>
            {
                float leftAngle = Mathf.Atan2(left.y - centroid.y, left.x - centroid.x);
                float rightAngle = Mathf.Atan2(right.y - centroid.y, right.x - centroid.x);
                return leftAngle.CompareTo(rightAngle);
            });

            return sampledPixels.ToArray();
        }

        private bool TryGetViewportRay(float u, float v, out Ray ray, out Transform rayTransform)
        {
            if (PassthroughRayResolver.TryGetViewportRay(
                passthroughCameraAccess,
                rayCamera,
                u,
                v,
                out ray,
                out rayTransform,
                out string source,
                out string warningMessage,
                "vision receiver"))
            {
                if (!_loggedPassthroughFallback && !string.IsNullOrEmpty(warningMessage))
                {
                    _loggedPassthroughFallback = true;
                    manager?.QueueUnityLog("WARNING", warningMessage);
                }

                LogRaySourceOnce(source);
                return true;
            }

            if (!_loggedPassthroughFallback && !string.IsNullOrEmpty(warningMessage))
            {
                _loggedPassthroughFallback = true;
                manager?.QueueUnityLog("WARNING", warningMessage);
            }

            ray = default;
            rayTransform = null;
            return false;
        }

        private void LogRaySourceOnce(string source)
        {
            if (_loggedRaySource)
            {
                return;
            }

            _loggedRaySource = true;
            manager?.QueueUnityLog("INFO", $"VisionReceiverModule ray source: {source}");
        }

        private void PublishWorldObjects(VisionWorldObject[] worldObjects)
        {
            _latestWorldObjects = worldObjects ?? Array.Empty<VisionWorldObject>();
            WorldObjectsUpdated?.Invoke(_latestWorldObjects);
        }

        private bool TryBuildBboxCorners(VisionFramePayload frame, VisionTrackedMaskPayload trackedMask, out Vector3[] eightCorners)
        {
            eightCorners = null;

            int[] box = trackedMask.box_xyxy;
            if (box == null || box.Length != 4)
            {
                return false;
            }

            int x1 = box[0], y1 = box[1], x2 = box[2], y2 = box[3];
            int frameW = frame.frame_width > 0 ? frame.frame_width : Math.Max(x2, 640);
            int frameH = frame.frame_height > 0 ? frame.frame_height : Math.Max(y2, 480);

            int[][] pixelCorners = new int[][]
            {
                new[] { x1, y1 },
                new[] { x2, y1 },
                new[] { x2, y2 },
                new[] { x1, y2 },
            };

            Vector3[] frontCorners = new Vector3[4];
            bool[] cornerHit = new bool[4];

            for (int i = 0; i < 4; i++)
            {
                float u = (pixelCorners[i][0] + 0.5f) / frameW;
                float v = 1f - ((pixelCorners[i][1] + 0.5f) / frameH);

                if (!TryGetViewportRay(u, v, out Ray ray, out Transform rayTransform))
                {
                    continue;
                }

                if (depthStreamModule.TryRaycastViewport(u, v, ray, rayTransform, out float depthM, out Vector3 worldPt, out _))
                {
                    frontCorners[i] = worldPt;
                    cornerHit[i] = true;
                }
            }

            int hitCount = 0;
            Vector3 sum = Vector3.zero;
            for (int i = 0; i < 4; i++)
            {
                if (cornerHit[i])
                {
                    sum += frontCorners[i];
                    hitCount++;
                }
            }

            if (hitCount < 2)
            {
                return false;
            }

            Vector3 avg = sum / hitCount;
            for (int i = 0; i < 4; i++)
            {
                if (!cornerHit[i])
                {
                    frontCorners[i] = avg;
                }
            }

            Vector3 cameraPos = rayCamera != null
                ? rayCamera.transform.position
                : (passthroughCameraAccess != null ? passthroughCameraAccess.transform.position : Vector3.zero);

            float depthOffset = bboxDepthOffsetMeters;
            eightCorners = new Vector3[8];
            for (int i = 0; i < 4; i++)
            {
                eightCorners[i] = frontCorners[i];
                Vector3 dirToCamera = (frontCorners[i] - cameraPos).normalized;
                eightCorners[i + 4] = frontCorners[i] + dirToCamera * depthOffset;
            }

            return true;
        }
    }
}
