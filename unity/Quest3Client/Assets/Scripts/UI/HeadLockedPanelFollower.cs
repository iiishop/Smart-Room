using UnityEngine;

namespace SmartRoom.UI
{
    public class HeadLockedPanelFollower : MonoBehaviour
    {
        [SerializeField] private Transform headTarget;
        [SerializeField] private Vector3 localOffset = new Vector3(0f, -0.08f, 0.8f);
        [SerializeField] private float positionSmoothTime = 0.12f;
        [SerializeField] private float rotationLerpSpeed = 10f;

        private Vector3 _positionVelocity;

        private void Awake()
        {
            if (headTarget == null && Camera.main != null)
            {
                headTarget = Camera.main.transform;
            }
        }

        private void LateUpdate()
        {
            if (headTarget == null)
            {
                return;
            }

            Vector3 targetPosition = headTarget.TransformPoint(localOffset);
            transform.position = Vector3.SmoothDamp(
                transform.position,
                targetPosition,
                ref _positionVelocity,
                positionSmoothTime
            );

            Vector3 toHead = headTarget.position - transform.position;
            if (toHead.sqrMagnitude < 0.0001f)
            {
                return;
            }

            Quaternion targetRotation = Quaternion.LookRotation(toHead.normalized, Vector3.up);
            transform.rotation = Quaternion.Slerp(
                transform.rotation,
                targetRotation,
                rotationLerpSpeed * Time.deltaTime
            );
        }
    }
}
