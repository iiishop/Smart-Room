Shader "SmartRoom/Scanning/DepthProbePoint"
{
    Properties
    {
        _PointSize ("Point Size", Range(0.001, 0.1)) = 0.005
    }

    SubShader
    {
        Tags
        {
            "RenderPipeline" = "UniversalPipeline"
            "RenderType" = "Transparent"
            "Queue" = "Transparent"
        }

        Pass
        {
            Name "DepthProbePoint"
            Tags { "LightMode" = "UniversalForward" }

            Blend SrcAlpha OneMinusSrcAlpha
            Cull Off
            ZWrite Off
            ZTest LEqual

            HLSLPROGRAM
            #pragma target 4.5
            #pragma vertex Vert
            #pragma fragment Frag
            #pragma multi_compile_instancing

            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"

            float _PointSize;

            // Structured buffer: each entry is { worldPos.xyz, packedColor }
            // packedColor: R = byte 0, G = byte 1, B = byte 2, A = byte 3
            StructuredBuffer<float4> _ProbePoints;

            struct Attributes
            {
                float4 vertex : POSITION;
                float2 uv : TEXCOORD0;
                uint instanceID : SV_InstanceID;
            };

            struct Varyings
            {
                float4 positionCS : SV_POSITION;
                float2 uv : TEXCOORD0;
                float4 color : COLOR;
            };

            // Unpack uint → float4 color (0-1 range)
            float4 UnpackColor(uint packed)
            {
                float4 c;
                c.r = ((packed >> 24) & 0xFF) / 255.0;
                c.g = ((packed >> 16) & 0xFF) / 255.0;
                c.b = ((packed >> 8)  & 0xFF) / 255.0;
                c.a = ((packed)        & 0xFF) / 255.0;
                return c;
            }

            Varyings Vert(Attributes input)
            {
                Varyings output;

                float4 probeData = _ProbePoints[input.instanceID];
                float3 worldPos = probeData.xyz;
                uint packedColor = asuint(probeData.w);

                // Billboard: always face camera
                // Compute the quad in world space centered at worldPos
                float3 camRight = mul((float3x3)UNITY_MATRIX_V, float3(1, 0, 0));
                float3 camUp    = mul((float3x3)UNITY_MATRIX_V, float3(0, 1, 0));

                float2 uv = input.uv * 2.0 - 1.0; // [-1, 1] from center
                float3 worldVertex = worldPos + camRight * uv.x * _PointSize + camUp * uv.y * _PointSize;

                output.positionCS = TransformWorldToHClip(worldVertex);
                output.uv = input.uv;
                output.color = UnpackColor(packedColor);
                return output;
            }

            half4 Frag(Varyings input) : SV_Target
            {
                // Circular falloff for soft points
                float2 center = input.uv - 0.5;
                float dist = length(center);
                float alpha = 1.0 - smoothstep(0.0, 0.5, dist);
                alpha *= input.color.a;

                return half4(input.color.rgb, alpha);
            }
            ENDHLSL
        }
    }
}
