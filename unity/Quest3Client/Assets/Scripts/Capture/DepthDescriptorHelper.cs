using System;
using System.Reflection;
using Meta.XR.EnvironmentDepth;
using UnityEngine;

namespace SmartRoom.Capture
{
    /// <summary>
    /// Static helper that extracts depth descriptor data via reflection,
    /// shared by Quest3RgbdCaptureFinal and DepthStreamModule.
    /// </summary>
    public static class DepthDescriptorHelper
    {
        [Serializable]
        public class DescriptorData
        {
            public float pose_position_x, pose_position_y, pose_position_z;
            public float pose_rotation_x, pose_rotation_y, pose_rotation_z, pose_rotation_w;
            public float fov_left, fov_right, fov_top, fov_bottom;
            public float near_z, far_z;
            public float zbuffer_x, zbuffer_y, zbuffer_z, zbuffer_w;
            public int selected_eye;
            public int depth_texture_width, depth_texture_height;
        }

        /// <summary>
        /// Try to extract depth descriptor from EnvironmentDepthManager.frameDescriptors[selectedEye].
        /// Returns null on failure (catches all reflection exceptions).
        /// </summary>
        public static DescriptorData TryGetDescriptor(
            EnvironmentDepthManager depthManager,
            int selectedEye,
            int depthTextureWidth,
            int depthTextureHeight)
        {
            try
            {
                if (depthManager == null)
                    return null;

                FieldInfo field = typeof(EnvironmentDepthManager).GetField(
                    "frameDescriptors",
                    BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public);
                if (field == null)
                    return null;

                object value = field.GetValue(depthManager);
                Array descriptors = value as Array;
                if (descriptors == null || descriptors.Length <= selectedEye)
                    return null;

                object desc = descriptors.GetValue(selectedEye);
                if (desc == null)
                    return null;

                Type type = desc.GetType();
                var posField = type.GetField("createPoseLocation",
                    BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                var rotField = type.GetField("createPoseRotation",
                    BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                if (posField == null || rotField == null)
                    return null;

                Vector3 pos = (Vector3)posField.GetValue(desc);
                Quaternion rot = (Quaternion)rotField.GetValue(desc);

                Vector4 zbp = Shader.GetGlobalVector("_EnvironmentDepthZBufferParams");

                return new DescriptorData
                {
                    pose_position_x = pos.x,
                    pose_position_y = pos.y,
                    pose_position_z = pos.z,
                    pose_rotation_x = rot.x,
                    pose_rotation_y = rot.y,
                    pose_rotation_z = rot.z,
                    pose_rotation_w = rot.w,
                    fov_left = ReadFloatField(type, desc, "fovLeftAngleTangent"),
                    fov_right = ReadFloatField(type, desc, "fovRightAngleTangent"),
                    fov_top = ReadFloatField(type, desc, "fovTopAngleTangent"),
                    fov_bottom = ReadFloatField(type, desc, "fovDownAngleTangent"),
                    near_z = ReadFloatField(type, desc, "nearZ"),
                    far_z = ReadFloatField(type, desc, "farZ"),
                    zbuffer_x = zbp.x,
                    zbuffer_y = zbp.y,
                    zbuffer_z = zbp.z,
                    zbuffer_w = zbp.w,
                    selected_eye = selectedEye,
                    depth_texture_width = depthTextureWidth,
                    depth_texture_height = depthTextureHeight,
                };
            }
            catch
            {
                return null;
            }
        }

        private static float ReadFloatField(Type type, object obj, string fieldName)
        {
            FieldInfo field = type.GetField(fieldName,
                BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            return field != null ? Convert.ToSingle(field.GetValue(obj)) : 0f;
        }
    }
}
