using SmartRoom.Networking;
using UnityEngine;

namespace SmartRoom.Vision
{
    public sealed class VisionOverlayManager : MonoBehaviour
    {
        [SerializeField] private VisionReceiverModule receiverModule;
        [SerializeField] private VisionBboxRenderer bboxRenderer;
        [SerializeField] private VisionMaskRenderer maskRenderer;
        [SerializeField] private VisionLabelPool labelPool;
        [SerializeField] private Camera labelCamera;

        private void Awake()
        {
            receiverModule ??= FindFirstObjectByType<VisionReceiverModule>();
            bboxRenderer ??= GetComponent<VisionBboxRenderer>();
            maskRenderer ??= GetComponent<VisionMaskRenderer>();
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

            bboxRenderer?.Clear();
            maskRenderer?.Clear();
            labelPool?.Clear();
        }

        private void LateUpdate()
        {
            labelPool?.UpdateBillboards();
        }

        private void HandleFrameProcessed(VisionFrameProcessedData frame)
        {
            VisionObjectProcessedData[] objects = frame != null && frame.Objects != null
                ? frame.Objects
                : System.Array.Empty<VisionObjectProcessedData>();

            bboxRenderer?.UpdateBuffers(objects);
            maskRenderer?.UpdateContours(objects);
            labelPool?.SyncObjects(objects);
        }
    }
}
