using UnityEngine;

namespace SmartRoom.Networking
{
    internal interface IEnvironmentRaycastProvider
    {
        bool IsAvailable { get; }
        bool TryRaycast(Ray ray, out Vector3 worldPoint);
    }

    internal static class DepthViewportRaycast
    {
        public static bool TryResolve(
            float[] latestDepthMeters,
            int latestDepthWidth,
            int latestDepthHeight,
            float u,
            float v,
            Ray ray,
            IEnvironmentRaycastProvider environmentRaycastProvider,
            out bool usedFallback,
            out float depthMeters,
            out Vector3 worldPoint,
            out Vector3 cameraPoint)
        {
            usedFallback = false;
            depthMeters = -1f;
            worldPoint = Vector3.zero;
            cameraPoint = Vector3.zero;

            if (latestDepthMeters == null || latestDepthWidth <= 0 || latestDepthHeight <= 0)
            {
                return false;
            }

            u = Mathf.Clamp01(u);
            v = Mathf.Clamp01(v);

            int x = Mathf.Clamp((int)(u * latestDepthWidth), 0, latestDepthWidth - 1);
            int y = Mathf.Clamp((int)(v * latestDepthHeight), 0, latestDepthHeight - 1);

            int idx = y * latestDepthWidth + x;
            if (idx < 0 || idx >= latestDepthMeters.Length)
            {
                return false;
            }

            float sampledDepthMeters = latestDepthMeters[idx];
            if (!float.IsFinite(sampledDepthMeters) || sampledDepthMeters <= 0f)
            {
                return false;
            }

            if (environmentRaycastProvider != null && environmentRaycastProvider.IsAvailable)
            {
                if (!environmentRaycastProvider.TryRaycast(ray, out Vector3 hitPoint))
                {
                    return false;
                }

                Vector3 delta = hitPoint - ray.origin;
                float resolvedDepthMeters = delta.magnitude;
                if (!float.IsFinite(resolvedDepthMeters) || resolvedDepthMeters <= 0f)
                {
                    return false;
                }

                depthMeters = resolvedDepthMeters;
                worldPoint = hitPoint;
                cameraPoint = ray.direction.normalized * resolvedDepthMeters;
                return true;
            }

            usedFallback = true;
            Vector3 fallbackPoint = ray.origin + (ray.direction.normalized * sampledDepthMeters);
            depthMeters = sampledDepthMeters;
            worldPoint = fallbackPoint;
            cameraPoint = ray.direction.normalized * sampledDepthMeters;
            return true;
        }
    }
}
