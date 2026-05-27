using UnityEngine;

namespace SmartRoom.Networking
{
    /// <summary>
    /// 从 DepthStreamModule 的 _latestDepthMeters 中按 UV/像素坐标采样深度值。
    /// 轻量查询层——不做射线投射，纯深度查表。
    /// 挂载：跟 DepthStreamModule 同一 GameObject
    /// </summary>
    public sealed class DepthFrameSampler : MonoBehaviour
    {
        [SerializeField] private DepthStreamModule depthStreamModule;

        public int LayoutWidth { get; private set; }
        public int LayoutHeight { get; private set; }
        public bool HasFrame => _latestDepth != null && _latestDepth.Length > 0;

        private float[] _latestDepth;
        private bool _loggedFirstFrame;

        private void Awake()
        {
            if (depthStreamModule == null)
                depthStreamModule = GetComponent<DepthStreamModule>();

            if (depthStreamModule == null)
                depthStreamModule = FindFirstObjectByType<DepthStreamModule>();
        }

        private void LateUpdate()
        {
            if (depthStreamModule != null)
            {
                _latestDepth = depthStreamModule.LatestDepthMeters;
                LayoutWidth = depthStreamModule.LatestDepthWidth;
                LayoutHeight = depthStreamModule.LatestDepthHeight;

                if (!_loggedFirstFrame && HasFrame)
                {
                    _loggedFirstFrame = true;
                    Debug.Log($"[DepthFrameSampler] First depth frame received: {LayoutWidth}x{LayoutHeight}, {_latestDepth.Length} floats");
                }
            }
        }

        /// <summary>
        /// 按归一化 UV 坐标采样深度（米）
        /// </summary>
        /// <param name="u">[0, 1] 水平坐标，0=左边缘</param>
        /// <param name="v">[0, 1] 垂直坐标，0=下边缘</param>
        /// <returns>深度（米），无效返回 -1</returns>
        public float Sample(float u, float v)
        {
            if (!HasFrame || LayoutWidth <= 0 || LayoutHeight <= 0)
                return -1f;

            u = Mathf.Clamp01(u);
            v = Mathf.Clamp01(v);

            int x = Mathf.Clamp((int)(u * LayoutWidth), 0, LayoutWidth - 1);
            int y = Mathf.Clamp((int)(v * LayoutHeight), 0, LayoutHeight - 1);

            return Sample(x, y);
        }

        /// <summary>
        /// 按像素坐标采样深度（米）
        /// </summary>
        public float Sample(int x, int y)
        {
            if (!HasFrame || _latestDepth == null)
                return -1f;

            if (x < 0 || x >= LayoutWidth || y < 0 || y >= LayoutHeight)
                return -1f;

            int idx = y * LayoutWidth + x;
            if (idx < 0 || idx >= _latestDepth.Length)
                return -1f;

            return _latestDepth[idx];
        }

        /// <summary>
        /// 批量采样 UV 坐标，返回对应深度数组。null 输入返回空数组。
        /// </summary>
        public float[] SampleBatch(Vector2[] uvs)
        {
            if (uvs == null || uvs.Length == 0)
                return System.Array.Empty<float>();

            var results = new float[uvs.Length];
            for (int i = 0; i < uvs.Length; i++)
                results[i] = Sample(uvs[i].x, uvs[i].y);

            return results;
        }

        /// <summary>
        /// 直接获取底层深度数组（只读引用）。高性能场景用，注意帧时序。
        /// </summary>
        public System.ReadOnlySpan<float> GetDepthSpan()
        {
            if (!HasFrame) return System.ReadOnlySpan<float>.Empty;
            return new System.ReadOnlySpan<float>(_latestDepth);
        }
    }
}
