Shader "SmartRoom/Scanning/DepthProbeGaussian"
{
    Properties
    {
        _PointSize ("Point Size", Float) = 0.01
    }
    SubShader
    {
        Tags { "RenderType"="Transparent" "Queue"="Transparent" "RenderPipeline"="UniversalPipeline" }
        Blend SrcAlpha OneMinusSrcAlpha
        ZWrite Off
        Cull Off

        Pass
        {
            Name "DepthProbeGaussian"
            HLSLPROGRAM
            #pragma target 4.5
            #pragma vertex vert
            #pragma fragment frag
            #pragma multi_compile_instancing

            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"

            struct ProbePoint
            {
                float4 data; // xyz = world pos, w = packed RGBA color
            };

            StructuredBuffer<ProbePoint> _ProbePoints;
            float _PointSize;

            struct Attributes
            {
                uint vertexID : SV_VertexID;
                uint instanceID : SV_InstanceID;
            };

            struct Varyings
            {
                float4 positionCS : SV_POSITION;
                float2 uv : TEXCOORD0;
                float4 color : COLOR;
            };

            // Unpack uint32 RGBA from float
            float4 UnpackColor(float packed)
            {
                uint bits = asuint(packed);
                float r = ((bits >> 24) & 0xFF) / 255.0;
                float g = ((bits >> 16) & 0xFF) / 255.0;
                float b = ((bits >> 8)  & 0xFF) / 255.0;
                float a = ((bits)       & 0xFF) / 255.0;
                return float4(r, g, b, a);
            }

            Varyings vert(Attributes input)
            {
                // 6 vertices per point: 2 triangles = 1 quad
                uint pointIdx = input.vertexID / 6;
                uint triVertex = input.vertexID % 6;

                ProbePoint pt = _ProbePoints[pointIdx];
                float3 worldPos = pt.data.xyz;
                float4 col = UnpackColor(pt.data.w);

                // Billboard quad: 2 triangles covering [-1,1]^2 in camera-local space
                // Triangle 1: (-1,-1) (1,-1) (1,1)
                // Triangle 2: (-1,-1) (1,1)  (-1,1)
                float2 corner;
                switch (triVertex)
                {
                    case 0: corner = float2(-1, -1); break;
                    case 1: corner = float2( 1, -1); break;
                    case 2: corner = float2( 1,  1); break;
                    case 3: corner = float2(-1, -1); break;
                    case 4: corner = float2( 1,  1); break;
                    default: corner = float2(-1, 1); break;
                }

                // Camera right & up from view matrix columns
                float3 camRight = UNITY_MATRIX_V._m00_m10_m20;
                float3 camUp    = UNITY_MATRIX_V._m01_m11_m21;

                float3 billboardPos = worldPos
                    + camRight * corner.x * _PointSize
                    + camUp    * corner.y * _PointSize;

                Varyings o;
                o.positionCS = TransformWorldToHClip(billboardPos);
                o.uv = corner * 0.5 + 0.5; // [0,1] range, center = (0.5, 0.5)
                o.color = col;
                return o;
            }

            float4 frag(Varyings i) : SV_Target
            {
                // 2D isotropic Gaussian centered at (0.5, 0.5)
                // sigma = 0.2 → soft falloff, overlapping points blend smoothly
                float2 d = i.uv - 0.5;
                float sigma = 0.2;
                float g = exp(-dot(d, d) / (2.0 * sigma * sigma));

                // Edge-crop: zero alpha outside circle (prevents square artifacts)
                float dist = length(d) * 2.0; // 0 at center, 1 at edge of quad
                float edgeMask = 1.0 - smoothstep(0.7, 1.0, dist);

                float alpha = i.color.a * g * edgeMask;
                return float4(i.color.rgb * alpha, alpha);
            }
            ENDHLSL
        }
    }
}
