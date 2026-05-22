using System;
using System.Reflection;
using Meta.XR;
using UnityEngine;

namespace SmartRoom.Networking
{
    public static class PassthroughRayResolver
    {
        private static MethodInfo s_passthroughViewportRayMethod;
        private static bool s_passthroughViewportRayMethodResolved;

        public static bool TryGetViewportRay(
            PassthroughCameraAccess passthroughCameraAccess,
            Camera rayCamera,
            float u,
            float v,
            out Ray ray,
            out Transform rayTransform,
            out string source,
            out string warningMessage,
            string fallbackContext)
        {
            if (TryGetPassthroughViewportRay(passthroughCameraAccess, u, v, out ray, out string failureReason))
            {
                rayTransform = passthroughCameraAccess != null ? passthroughCameraAccess.transform : null;
                source = "PassthroughCameraAccess.ViewportPointToRay";
                warningMessage = string.Empty;
                return true;
            }

            if (rayCamera != null)
            {
                ray = rayCamera.ViewportPointToRay(new Vector3(u, v, 0f));
                rayTransform = rayCamera.transform;
                source = $"Camera.ViewportPointToRay({rayCamera.name})";
                warningMessage = !string.IsNullOrEmpty(failureReason)
                    ? failureReason
                    : passthroughCameraAccess != null
                        ? $"PassthroughCameraAccess.ViewportPointToRay unavailable; falling back to Camera.ViewportPointToRay for {fallbackContext}."
                        : string.Empty;
                return true;
            }

            ray = default;
            rayTransform = null;
            source = string.Empty;
            warningMessage = failureReason ?? string.Empty;
            return false;
        }

        public static bool TryGetPassthroughViewportRay(
            PassthroughCameraAccess passthroughCameraAccess,
            float u,
            float v,
            out Ray ray,
            out string failureReason)
        {
            ray = default;
            failureReason = null;

            if (passthroughCameraAccess == null || !passthroughCameraAccess.enabled || !passthroughCameraAccess.IsPlaying)
            {
                return false;
            }

            MethodInfo method = ResolvePassthroughViewportRayMethod();
            if (method == null)
            {
                return false;
            }

            object arg = method.GetParameters()[0].ParameterType == typeof(Vector2)
                ? new Vector2(u, v)
                : new Vector3(u, v, 0f);

            object target = method.IsStatic ? null : passthroughCameraAccess;
            try
            {
                object result = method.Invoke(target, new[] { arg });
                if (result is Ray castRay)
                {
                    ray = castRay;
                    return true;
                }
            }
            catch (TargetInvocationException ex)
            {
                failureReason = $"PassthroughCameraAccess.ViewportPointToRay failed: {ex.InnerException?.Message ?? ex.Message}";
            }
            catch (Exception ex)
            {
                failureReason = $"PassthroughCameraAccess.ViewportPointToRay failed: {ex.Message}";
            }

            return false;
        }

        private static MethodInfo ResolvePassthroughViewportRayMethod()
        {
            if (s_passthroughViewportRayMethodResolved)
            {
                return s_passthroughViewportRayMethod;
            }

            s_passthroughViewportRayMethodResolved = true;
            foreach (MethodInfo method in typeof(PassthroughCameraAccess).GetMethods(BindingFlags.Public | BindingFlags.Instance | BindingFlags.Static))
            {
                if (method.Name != "ViewportPointToRay" || method.ReturnType != typeof(Ray))
                {
                    continue;
                }

                ParameterInfo[] parameters = method.GetParameters();
                if (parameters.Length != 1)
                {
                    continue;
                }

                Type parameterType = parameters[0].ParameterType;
                if (parameterType == typeof(Vector2) || parameterType == typeof(Vector3))
                {
                    s_passthroughViewportRayMethod = method;
                    break;
                }
            }

            return s_passthroughViewportRayMethod;
        }
    }
}
