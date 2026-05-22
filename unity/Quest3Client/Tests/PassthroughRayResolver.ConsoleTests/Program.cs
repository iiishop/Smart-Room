using Meta.XR;
using SmartRoom.Networking;
using UnityEngine;

VerifyPassthroughVector2MethodIsUsed();
VerifyCameraFallbackIncludesReason();
Console.WriteLine("PassthroughRayResolver console tests passed.");

static void VerifyPassthroughVector2MethodIsUsed()
{
    var passthrough = new PassthroughCameraAccess
    {
        enabled = true,
        IsPlaying = true,
        transform = new Transform(),
    };
    var camera = new Camera
    {
        name = "Main Camera",
        transform = new Transform(),
    };

    bool success = PassthroughRayResolver.TryGetViewportRay(
        passthrough,
        camera,
        0.25f,
        0.75f,
        out Ray ray,
        out Transform rayTransform,
        out string source,
        out string warningMessage,
        "vision receiver");

    AssertTrue(success, "passthrough ray should resolve");
    AssertEqual("PassthroughCameraAccess.ViewportPointToRay", source, "passthrough source");
    AssertEqual(string.Empty, warningMessage, "passthrough warning message");
    AssertSame(passthrough.transform, rayTransform, "passthrough transform");
    AssertEqual(0.25f, ray.origin.x, "passthrough origin x");
    AssertEqual(0.75f, ray.origin.y, "passthrough origin y");
    AssertEqual(2f, ray.direction.z, "passthrough direction z");
}

static void VerifyCameraFallbackIncludesReason()
{
    var passthrough = new PassthroughCameraAccess
    {
        enabled = false,
        IsPlaying = false,
        transform = new Transform(),
    };
    var camera = new Camera
    {
        name = "Fallback Camera",
        transform = new Transform(),
    };

    bool success = PassthroughRayResolver.TryGetViewportRay(
        passthrough,
        camera,
        0.4f,
        0.6f,
        out Ray ray,
        out Transform rayTransform,
        out string source,
        out string warningMessage,
        "raycast queries");

    AssertTrue(success, "camera fallback should resolve");
    AssertEqual("Camera.ViewportPointToRay(Fallback Camera)", source, "camera source");
    AssertEqual("PassthroughCameraAccess.ViewportPointToRay unavailable; falling back to Camera.ViewportPointToRay for raycast queries.", warningMessage, "camera warning message");
    AssertSame(camera.transform, rayTransform, "camera transform");
    AssertEqual(0.4f, ray.origin.x, "camera origin x");
    AssertEqual(0.6f, ray.origin.y, "camera origin y");
    AssertEqual(1f, ray.direction.z, "camera direction z");
}

static void AssertTrue(bool condition, string message)
{
    if (!condition)
    {
        throw new Exception(message);
    }
}

static void AssertEqual<T>(T expected, T actual, string message)
{
    if (!Equals(expected, actual))
    {
        throw new Exception($"{message}. Expected={expected}, Actual={actual}");
    }
}

static void AssertSame(object expected, object actual, string message)
{
    if (!ReferenceEquals(expected, actual))
    {
        throw new Exception(message);
    }
}

namespace UnityEngine
{
    public class Object
    {
        public string name;
    }

    public class Behaviour : Object
    {
        public bool enabled;
    }

    public class Transform : Object
    {
    }

    public class Camera : Behaviour
    {
        public Transform transform;

        public Ray ViewportPointToRay(Vector3 point)
        {
            return new Ray(point, new Vector3(0f, 0f, 1f));
        }
    }

    public struct Vector2
    {
        public Vector2(float x, float y)
        {
            this.x = x;
            this.y = y;
        }

        public float x;
        public float y;
    }

    public struct Vector3
    {
        public Vector3(float x, float y, float z)
        {
            this.x = x;
            this.y = y;
            this.z = z;
        }

        public float x;
        public float y;
        public float z;
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
}

namespace Meta.XR
{
    using UnityEngine;

    public class PassthroughCameraAccess : Behaviour
    {
        public bool IsPlaying { get; set; }
        public Transform transform;

        public Ray ViewportPointToRay(Vector2 point)
        {
            return new Ray(new Vector3(point.x, point.y, 0f), new Vector3(0f, 0f, 2f));
        }
    }
}
