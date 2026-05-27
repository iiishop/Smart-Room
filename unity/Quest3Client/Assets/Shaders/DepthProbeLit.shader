Shader "SmartRoom/Scanning/DepthProbeLit"
{
    Properties
    {
        _PointSize ("Point Size", Float) = 0.01
        _LightDir ("Light Direction", Vector) = (0.5, 0.8, 0.3, 0)
        _Ambient ("Ambient", Float) = 0.3
    }
    SubShader
    {
        Tags { "RenderType"="Transparent" "Queue"="Transparent" "RenderPipeline"="UniversalPipeline" }
        Blend SrcAlpha OneMinusSrcAlpha
        ZWrite Off
        Cull Off

        Pass
        {
            Name "DepthProbeLit"
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
            float3 _LightDir;
            float _Ambient;

            struct Attributes
            {
                uint vertexID : SV_VertexID;
                uint instanceID : SV_InstanceID;
            };

            struct Varyings
            {
                float4 positionCS : SV_POSITION;
                float4 color : COLOR;
                float3 worldNormal : TEXCOORD0;
                float3 worldPos : TEXCOORD1;
            };

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
                ProbePoint pt = _ProbePoints[input.instanceID];
                float3 worldPos = pt.data.xyz;
                float4 col = UnpackColor(pt.data.w);

                // Simple flat quad (no billboard) — reliable on mobile
                // InstanceID -> corner offset
                float2 offsets[4] = {
                    float2(-1, -1), float2(1, -1), float2(1, 1), float2(-1, 1)
                };
                // vertexID % 4 gives us the corner
                uint corner = input.vertexID % 4;
                float2 offset = offsets[corner] * _PointSize;

                // Use camera right & up for consistent orientation
                float3 camRight = UNITY_MATRIX_V._m00_m10_m20;
                float3 camUp    = UNITY_MATRIX_V._m01_m11_m21;

                float3 billboardPos = worldPos
                    + camRight * offset.x
                    + camUp    * offset.y;

                Varyings o;
                o.positionCS = TransformWorldToHClip(billboardPos);
                o.color = col;

                // Reconstruct approximate world normal from color (normal-map encoding)
                // R = normal.x*0.5+0.5, G = normal.y*0.5+0.5, B = normal.z*0.5+0.5
                float3 worldNormal = float3(col.r * 2.0 - 1.0, col.g * 2.0 - 1.0, col.b * 2.0 - 1.0);
                o.worldNormal = normalize(worldNormal);
                o.worldPos = worldPos;

                return o;
            }

            float4 frag(Varyings i) : SV_Target
            {
                // Diffuse lighting using world normal
                float3 lightDir = normalize(_LightDir);
                float NdotL = saturate(dot(i.worldNormal, lightDir));
                float3 lit = _Ambient + NdotL * (1.0 - _Ambient);

                // Base color from normal map (RGB = normal * 0.5 + 0.5)
                float3 baseColor = i.worldNormal * 0.5 + 0.5;
                float3 shaded = baseColor * lit;

                // EDL-style luminance: alpha encodes brightness (1.0=center, 0.2=edge)
                // Multiply RGB by alpha for darkening, keep alpha=1 for full opacity
                return float4(shaded * i.color.a, 1.0);
            }
            ENDHLSL
        }
    }
}
