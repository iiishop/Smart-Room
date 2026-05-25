using System;
using System.Collections.Concurrent;
using UnityEngine;

#nullable enable

namespace SmartRoom.Networking
{
    public sealed class VisionMessageParser
    {
        private readonly ConcurrentQueue<string> _pendingVisionMessages = new ConcurrentQueue<string>();

        public void Enqueue(string json)
        {
            if (string.IsNullOrWhiteSpace(json))
            {
                return;
            }

            _pendingVisionMessages.Enqueue(json);
        }

        public void Clear()
        {
            while (_pendingVisionMessages.TryDequeue(out _))
            {
            }
        }

        public bool TryDequeueLatest(out VisionFramePayload? frame, out string? errorMessage)
        {
            frame = null;
            errorMessage = null;

            string? latestJson = null;
            while (_pendingVisionMessages.TryDequeue(out string json))
            {
                latestJson = json;
            }

            if (latestJson == null)
            {
                return false;
            }

            try
            {
                frame = JsonUtility.FromJson<VisionFramePayload>(latestJson);
                return true;
            }
            catch (Exception ex)
            {
                errorMessage = ex.Message;
                return false;
            }
        }
    }
}
