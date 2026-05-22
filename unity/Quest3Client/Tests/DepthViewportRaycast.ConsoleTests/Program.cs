using SmartRoom.Networking;
using UnityEngine;

VerifyEnvironmentRaycastHitWinsOverManualProjection();
VerifyUnavailableEnvironmentRaycastFallsBackToDepthProjection();
VerifyAvailableEnvironmentRaycastDoesNotFallbackOnMiss();
Console.WriteLine("DepthViewportRaycast console tests passed.");

static void VerifyEnvironmentRaycastHitWinsOverManualProjection()
{
    var provider = new FakeEnvironmentRaycastProvider(
        isAvailable: true,
        willHit: true,
        hitPoint: new Vector3(10f, 0f, 0f));

    bool success = DepthViewportRaycast.TryResolve(
        latestDepthMeters: new[] { 2f, 3f, 4f, 5f },
        latestDepthWidth: 2,
        latestDepthHeight: 2,
        u: 0.25f,
        v: 0.25f,
        ray: new Ray(new Vector3(1f, 0f, 0f), new Vector3(0f, 0f, 3f)),
        environmentRaycastProvider: provider,
        usedFallback: out bool usedFallback,
        depthMeters: out float depthMeters,
        worldPoint: out Vector3 worldPoint,
        cameraPoint: out Vector3 cameraPoint);

    AssertTrue(success, "environment raycast should succeed");
    AssertFalse(usedFallback, "environment raycast should not fallback");
    AssertEqual(9f, depthMeters, "depth should come from hit point distance");
    AssertVector(new Vector3(10f, 0f, 0f), worldPoint, "world point should come from provider");
    AssertVector(new Vector3(0f, 0f, 9f), cameraPoint, "camera point should be aligned to ray direction");
}

static void VerifyUnavailableEnvironmentRaycastFallsBackToDepthProjection()
{
    var provider = new FakeEnvironmentRaycastProvider(
        isAvailable: false,
        willHit: false,
        hitPoint: Vector3.zero);

    bool success = DepthViewportRaycast.TryResolve(
        latestDepthMeters: new[] { 0f, 6f, 7f, 8f },
        latestDepthWidth: 2,
        latestDepthHeight: 2,
        u: 0.75f,
        v: 0.25f,
        ray: new Ray(new Vector3(1f, 2f, 3f), new Vector3(0f, 4f, 0f)),
        environmentRaycastProvider: provider,
        usedFallback: out bool usedFallback,
        depthMeters: out float depthMeters,
        worldPoint: out Vector3 worldPoint,
        cameraPoint: out Vector3 cameraPoint);

    AssertTrue(success, "fallback projection should succeed");
    AssertTrue(usedFallback, "fallback should be reported");
    AssertEqual(6f, depthMeters, "depth should come from sampled depth map");
    AssertVector(new Vector3(1f, 8f, 3f), worldPoint, "world point should use manual projection");
    AssertVector(new Vector3(0f, 6f, 0f), cameraPoint, "camera point should use normalized ray direction");
}

static void VerifyAvailableEnvironmentRaycastDoesNotFallbackOnMiss()
{
    var provider = new FakeEnvironmentRaycastProvider(
        isAvailable: true,
        willHit: false,
        hitPoint: Vector3.zero);

    bool success = DepthViewportRaycast.TryResolve(
        latestDepthMeters: new[] { 1f },
        latestDepthWidth: 1,
        latestDepthHeight: 1,
        u: 0.5f,
        v: 0.5f,
        ray: new Ray(new Vector3(0f, 0f, 0f), new Vector3(1f, 0f, 0f)),
        environmentRaycastProvider: provider,
        usedFallback: out bool usedFallback,
        depthMeters: out float depthMeters,
        worldPoint: out Vector3 worldPoint,
        cameraPoint: out Vector3 cameraPoint);

    AssertFalse(success, "available environment raycast miss should fail");
    AssertFalse(usedFallback, "available environment raycast miss should not fallback");
    AssertEqual(-1f, depthMeters, "depth should stay unset");
    AssertVector(Vector3.zero, worldPoint, "world point should stay unset");
    AssertVector(Vector3.zero, cameraPoint, "camera point should stay unset");
}

static void AssertTrue(bool condition, string message)
{
    if (!condition)
    {
        throw new Exception(message);
    }
}

static void AssertFalse(bool condition, string message)
{
    if (condition)
    {
        throw new Exception(message);
    }
}

static void AssertEqual(float expected, float actual, string message)
{
    if (Math.Abs(expected - actual) > 0.0001f)
    {
        throw new Exception($"{message}. Expected={expected}, Actual={actual}");
    }
}

static void AssertVector(Vector3 expected, Vector3 actual, string message)
{
    AssertEqual(expected.x, actual.x, $"{message} (x)");
    AssertEqual(expected.y, actual.y, $"{message} (y)");
    AssertEqual(expected.z, actual.z, $"{message} (z)");
}

namespace SmartRoom.Networking
{
    internal sealed class FakeEnvironmentRaycastProvider : IEnvironmentRaycastProvider
    {
        private readonly bool _willHit;
        private readonly Vector3 _hitPoint;

        public FakeEnvironmentRaycastProvider(bool isAvailable, bool willHit, Vector3 hitPoint)
        {
            IsAvailable = isAvailable;
            _willHit = willHit;
            _hitPoint = hitPoint;
        }

        public bool IsAvailable { get; }

        public bool TryRaycast(Ray ray, out Vector3 worldPoint)
        {
            worldPoint = _hitPoint;
            return _willHit;
        }
    }
}

namespace UnityEngine
{
    public struct Vector3
    {
        public static Vector3 zero => new Vector3(0f, 0f, 0f);

        public Vector3(float x, float y, float z)
        {
            this.x = x;
            this.y = y;
            this.z = z;
        }

        public float x;
        public float y;
        public float z;

        public float magnitude => MathF.Sqrt((x * x) + (y * y) + (z * z));

        public Vector3 normalized
        {
            get
            {
                float mag = magnitude;
                return mag > 0f ? new Vector3(x / mag, y / mag, z / mag) : zero;
            }
        }

        public static Vector3 operator +(Vector3 left, Vector3 right)
        {
            return new Vector3(left.x + right.x, left.y + right.y, left.z + right.z);
        }

        public static Vector3 operator -(Vector3 left, Vector3 right)
        {
            return new Vector3(left.x - right.x, left.y - right.y, left.z - right.z);
        }

        public static Vector3 operator *(Vector3 value, float scalar)
        {
            return new Vector3(value.x * scalar, value.y * scalar, value.z * scalar);
        }
    }

    public struct Ray
    {
        public Ray(Vector3 origin, Vector3 direction)
        {
            this.origin = origin;
            this.direction = direction;
        }

        public Vector3 origin;
        public Vector3 direction;
    }

    public static class Mathf
    {
        public static float Clamp01(float value)
        {
            return Clamp(value, 0f, 1f);
        }

        public static int Clamp(int value, int min, int max)
        {
            if (value < min)
            {
                return min;
            }

            if (value > max)
            {
                return max;
            }

            return value;
        }

        public static float Clamp(float value, float min, float max)
        {
            if (value < min)
            {
                return min;
            }

            if (value > max)
            {
                return max;
            }

            return value;
        }
    }
}
