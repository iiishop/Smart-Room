using System.Collections.Generic;
using System.Runtime.InteropServices;
using UnityEngine;

namespace SmartRoom.Rendering
{
    [StructLayout(LayoutKind.Sequential)]
    internal struct BboxWireframeInstance
    {
        public Vector3 corner0;
        public Vector3 corner1;
        public Vector3 corner2;
        public Vector3 corner3;
        public Vector3 corner4;
        public Vector3 corner5;
        public Vector3 corner6;
        public Vector3 corner7;
        public Vector4 color;
    }

    public sealed class BboxWireframeManager : MonoBehaviour
    {
        private const int MaxInstances = 64;
        private const int StrideBytes = 112;
        private const int CornerCount = 8;

        private ComputeBuffer _bboxBuffer;
        private BboxWireframeInstance[] _data;
        private Dictionary<int, int> _objectIdToSlot;
        private int[] _slotToObjectId;
        private int _activeCount;
        private int _renderableCount;
        private bool _dirty;
        private bool _gpuRenderingDisabled;

        public ComputeBuffer BboxBuffer => _bboxBuffer;
        public int ActiveCount => _renderableCount;

        private void Awake()
        {
            _data = new BboxWireframeInstance[MaxInstances];
            _objectIdToSlot = new Dictionary<int, int>(MaxInstances);
            _slotToObjectId = new int[MaxInstances];
            for (int i = 0; i < MaxInstances; i++)
            {
                _slotToObjectId[i] = -1;
            }

            try
            {
                _bboxBuffer = new ComputeBuffer(MaxInstances, StrideBytes, ComputeBufferType.Structured);
            }
            catch (System.Exception ex)
            {
                Debug.LogError($"Failed to allocate bbox wireframe buffer: {ex}");
                _gpuRenderingDisabled = true;
            }
        }

        private void OnDestroy()
        {
            if (_bboxBuffer != null)
            {
                _bboxBuffer.Release();
                _bboxBuffer = null;
            }
        }

        public void SetBboxData(int objectId, Vector3[] corners, Color color)
        {
            if (_gpuRenderingDisabled || corners == null || corners.Length < CornerCount)
            {
                return;
            }

            int slot;
            if (!_objectIdToSlot.TryGetValue(objectId, out slot))
            {
                if (_activeCount >= MaxInstances)
                {
                    return;
                }

                slot = _activeCount;
                _objectIdToSlot[objectId] = slot;
                _slotToObjectId[slot] = objectId;
                _activeCount++;
            }

            _data[slot] = new BboxWireframeInstance
            {
                corner0 = corners[0],
                corner1 = corners[1],
                corner2 = corners[2],
                corner3 = corners[3],
                corner4 = corners[4],
                corner5 = corners[5],
                corner6 = corners[6],
                corner7 = corners[7],
                color = new Vector4(color.r, color.g, color.b, color.a)
            };
            _dirty = true;
        }

        public void RemoveBbox(int objectId)
        {
            if (_gpuRenderingDisabled)
            {
                return;
            }

            int slot;
            if (!_objectIdToSlot.TryGetValue(objectId, out slot))
            {
                return;
            }

            int lastSlot = _activeCount - 1;
            if (slot != lastSlot)
            {
                int lastObjectId = _slotToObjectId[lastSlot];
                _data[slot] = _data[lastSlot];
                _objectIdToSlot[lastObjectId] = slot;
                _slotToObjectId[slot] = lastObjectId;
            }

            _objectIdToSlot.Remove(objectId);
            _slotToObjectId[lastSlot] = -1;
            _activeCount--;
            _dirty = true;
        }

        public void ClearAll()
        {
            _objectIdToSlot.Clear();
            for (int i = 0; i < MaxInstances; i++)
            {
                _slotToObjectId[i] = -1;
            }

            _activeCount = 0;
            _renderableCount = 0;
            _dirty = true;
        }

        private void LateUpdate()
        {
            if (_gpuRenderingDisabled || !_dirty)
            {
                return;
            }

            if (_activeCount == 0)
            {
                _renderableCount = 0;
                _dirty = false;
                return;
            }

            try
            {
                _bboxBuffer.SetData(_data, 0, 0, _activeCount);
                _renderableCount = _activeCount;
                _dirty = false;
            }
            catch (System.Exception ex)
            {
                Debug.LogError($"Failed to upload bbox wireframe data for this frame: {ex}");
                _renderableCount = 0;
                _dirty = false;
            }
        }
    }
}
