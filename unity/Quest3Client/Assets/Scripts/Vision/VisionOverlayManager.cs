using SmartRoom.Networking;
using UnityEngine;

namespace SmartRoom.Vision
{
    public sealed class VisionOverlayManager : MonoBehaviour
    {
        [SerializeField] private VisionReceiverModule receiverModule;
        [SerializeField] private VisionMaskOverlay maskOverlay;
        [SerializeField] private VisionLabelPool labelPool;
        [SerializeField] private Camera labelCamera;

        private void Awake()
        {
            receiverModule ??= FindFirstObjectByType<VisionReceiverModule>();
            maskOverlay ??= GetComponent<VisionMaskOverlay>();
            labelPool ??= GetComponent<VisionLabelPool>();
            labelCamera ??= Camera.main;
            labelPool?.SetLabelCamera(labelCamera);
        }

        private void OnEnable()
        {
            if (receiverModule != null)
            {
                receiverModule.OnFrameProcessed += HandleFrameProcessed;
            }
        }

        private void OnDisable()
        {
            if (receiverModule != null)
            {
                receiverModule.OnFrameProcessed -= HandleFrameProcessed;
            }

            maskOverlay?.Clear();
            labelPool?.Clear();
        }

        private void LateUpdate()
        {
            if (labelCamera == null) return;
            maskOverlay?.UpdateBillboards(labelCamera);
            labelPool?.UpdateBillboards(labelCamera);
        }

        private void HandleFrameProcessed(VisionFrameProcessedData frame)
        {
            VisionObjectProcessedData[] objects = frame != null && frame.Objects != null
                ? frame.Objects
                : System.Array.Empty<VisionObjectProcessedData>();

            maskOverlay?.SyncObjects(objects);
            labelPool?.SyncObjects(objects);
        }
    }
}
