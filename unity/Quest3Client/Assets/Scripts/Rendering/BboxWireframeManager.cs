using System.Collections.Generic;
using System.Runtime.InteropServices;
using UnityEngine;

namespace SmartRoom.Rendering
{
    [StructLayout(LayoutKind.Sequential, Size = 112)]
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
        public ushort cr;
        public ushort cg;
        public ushort cb;
        public ushort ca;
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
        private bool _dirty;

        public ComputeBuffer BboxBuffer => _bboxBuffer;
        public int ActiveCount => _activeCount;

        private void Awake()
        {
            _bboxBuffer = new ComputeBuffer(MaxInstances, StrideBytes, ComputeBufferType.Structured);
            _data = new BboxWireframeInstance[MaxInstances];
            _objectIdToSlot = new Dictionary<int, int>(MaxInstances);
            _slotToObjectId = new int[MaxInstances];
            for (int i = 0; i < MaxInstances; i++)
            {
                _slotToObjectId[i] = -1;
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
            if (corners == null || corners.Length < CornerCount)
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
                cr = Mathf.FloatToHalf(color.r),
                cg = Mathf.FloatToHalf(color.g),
                cb = Mathf.FloatToHalf(color.b),
                ca = Mathf.FloatToHalf(color.a)
            };
            _dirty = true;
        }

        public void RemoveBbox(int objectId)
        {
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
            _dirty = true;
        }

        private void LateUpdate()
        {
            if (!_dirty || _activeCount == 0)
            {
                return;
            }

            _bboxBuffer.SetData(_data, 0, 0, _activeCount);
            _dirty = false;
        }
    }
}
