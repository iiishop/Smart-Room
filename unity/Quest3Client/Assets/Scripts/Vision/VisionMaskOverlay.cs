using System;
using Meta.XR;
using UnityEngine;

namespace SmartRoom.Vision
{
    public sealed class VisionMaskOverlay : MonoBehaviour
    {
        [SerializeField] private Shader maskShader;
        [SerializeField] private PassthroughCameraAccess passthroughCameraAccess;
        [SerializeField] [Range(0, 1)] private float alpha = 0.35f;

        private Material _sharedMaterial;
        private MaskQuad[] _pool = Array.Empty<MaskQuad>();
        private int _activeCount;
        private float _focalPixels; // cached PCA focal length in pixels (static, read once)

        private void Awake()
        {
            Shader shader = maskShader != null ? maskShader : Shader.Find("SmartRoom/Vision/VisionMaskOverlay");
            if (shader != null)
            {
                _sharedMaterial = new Material(shader)
                {
                    hideFlags = HideFlags.HideAndDontSave
                };
            }

            if (passthroughCameraAccess == null)
                passthroughCameraAccess = FindFirstObjectByType<PassthroughCameraAccess>();

            CacheIntrinsics();
        }

        private void CacheIntrinsics()
        {
            if (passthroughCameraAccess != null)
            {
                try
                {
                    var intrinsics = passthroughCameraAccess.Intrinsics;
                    _focalPixels = intrinsics.FocalLength.x; // fx in pixels
                    Debug.Log($"[VisionDiag] PCA intrinsics: focalPixels={_focalPixels:F1}");
                }
                catch
                {
                    _focalPixels = 640f; // fallback: assume ~90° HFOV for 640px
                    Debug.LogWarning("[VisionDiag] PCA intrinsics unavailable, using fallback focalPixels=640");
                }
            }
            else
            {
                _focalPixels = 640f;
            }
        }

        private void OnDestroy()
        {
            Clear();
            if (_sharedMaterial != null) Destroy(_sharedMaterial);
        }

        public void Clear()
        {
            for (int i = 0; i < _activeCount; i++)
            {
                if (_pool[i].Texture != null) Destroy(_pool[i].Texture);
                if (_pool[i].Material != null) Destroy(_pool[i].Material);
                if (_pool[i].Quad != null) Destroy(_pool[i].Quad);
            }
            _activeCount = 0;
        }

        public void SyncObjects(VisionObjectProcessedData[] objects)
        {
            EnsurePool(objects != null ? objects.Length : 0);

            for (int i = 0; i < _activeCount; i++)
            {
                if (_pool[i].Quad != null) _pool[i].Quad.SetActive(false);
            }

            if (objects == null || objects.Length == 0)
            {
                _activeCount = 0;
                return;
            }

            int count = Mathf.Min(objects.Length, _pool.Length);
            for (int i = 0; i < count; i++)
            {
                VisionObjectProcessedData obj = objects[i];
                ref MaskQuad quad = ref _pool[i];

                Texture2D tex = DecodeMaskRle(obj.MaskHeight, obj.MaskWidth, obj.MaskCounts, obj.ObjectId);
                if (tex == null) continue;

                if (quad.Texture != null && quad.Texture != tex) Destroy(quad.Texture);
                quad.Texture = tex;

                if (quad.Material == null)
                {
                    quad.Material = new Material(_sharedMaterial);
                    quad.Material.SetTexture("_MaskTex", tex);
                    quad.Material.SetFloat("_Alpha", alpha);
                }
                else
                {
                    quad.Material.SetTexture("_MaskTex", tex);
                }

                if (quad.Quad == null)
                {
                    quad.Quad = CreateQuad($"mask_{obj.ObjectId}", quad.Material);
                }

                // Dynamic worldScale: pixels → meters at this object's depth
                float depth = obj.DepthMeters > 0.01f ? obj.DepthMeters : 0.5f;
                float scale = depth / _focalPixels;
                float w = obj.MaskWidth * scale;
                float h = obj.MaskHeight * scale;

                quad.Quad.transform.position = obj.Center3D + Vector3.up * 0.05f;
                quad.Quad.transform.localScale = new Vector3(w, h, 1f);
                quad.Quad.SetActive(true);
            }

            _activeCount = count;
        }

        public void UpdateBillboards(Camera camera)
        {
            if (camera == null) return;
            for (int i = 0; i < _activeCount; i++)
            {
                Transform t = _pool[i].Quad?.transform;
                if (t != null)
                {
                    t.LookAt(t.position + camera.transform.forward, camera.transform.up);
                }
            }
        }

        private void EnsurePool(int needed)
        {
            int capacity = Mathf.Max(needed, 8);
            if (_pool.Length >= capacity) return;

            var newPool = new MaskQuad[capacity];
            Array.Copy(_pool, newPool, _pool.Length);
            _pool = newPool;
        }

        private static Texture2D DecodeMaskRle(int height, int width, int[] counts, int objectId)
        {
            if (height <= 0 || width <= 0 || counts == null || counts.Length < 2)
                return null;

            int total = width * height;

            // COCO RLE is column-major: decode into flat array in column-major order,
            // then transpose to row-major for Texture2D.
            var cm = new bool[total];
            int flat = 0;
            bool fg = false;
            for (int r = 0; r < counts.Length && flat < total; r++)
            {
                int run = counts[r];
                if (run < 0) return null;
                int end = Mathf.Min(flat + run, total);
                if (fg)
                {
                    for (int i = flat; i < end; i++) cm[i] = true;
                }
                flat = end;
                fg = !fg;
            }

            // Transpose column-major → row-major for Texture2D
            Color32 color = VisionObjectColorTable.GetColor(objectId);
            var pixels = new Color32[total];
            for (int y = 0; y < height; y++)
            {
                for (int x = 0; x < width; x++)
                {
                    if (cm[x * height + y])
                        pixels[y * width + x] = color;
                }
            }

            var tex = new Texture2D(width, height, TextureFormat.RGBA32, false);
            tex.SetPixels32(pixels);
            tex.Apply();
            return tex;
        }

        private static GameObject CreateQuad(string name, Material material)
        {
            var go = GameObject.CreatePrimitive(PrimitiveType.Quad);
            go.name = name;
            go.hideFlags = HideFlags.HideAndDontSave;
            var mr = go.GetComponent<MeshRenderer>();
            mr.sharedMaterial = material;
            mr.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
            mr.receiveShadows = false;
            var col = go.GetComponent<Collider>();
            if (col != null) Destroy(col);
            return go;
        }

        private struct MaskQuad
        {
            public GameObject Quad;
            public Material Material;
            public Texture2D Texture;
        }
    }
}
