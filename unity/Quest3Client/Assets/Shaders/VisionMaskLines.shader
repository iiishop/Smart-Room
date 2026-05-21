Shader "SmartRoom/Vision/VisionMaskLines"
{
    Properties
    {
        _LineWidth ("Line Width (Pixels)", Float) = 2
        _Alpha ("Alpha", Range(0, 1)) = 0.85
        _DepthOffsetMeters ("Depth Offset Meters", Float) = 0.01
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
            Name "VisionMaskLines"
            Tags { "LightMode" = "UniversalForward" }

            Blend SrcAlpha OneMinusSrcAlpha
            Cull Off
            ZWrite Off
            ZTest LEqual

            HLSLPROGRAM
            #pragma target 4.5
            #pragma vertex Vert
            #pragma fragment Frag

            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"

            struct LineVertexData
            {
                float3 position;
                uint color;
            };

            StructuredBuffer<LineVertexData> _LineVertices;
            float _LineWidth;
            float _Alpha;
            float _DepthOffsetMeters;

            struct Attributes
            {
                uint vertexID : SV_VertexID;
            };

            struct Varyings
            {
                float4 positionCS : SV_POSITION;
                half4 color : COLOR0;
            };

            half4 DecodeColor32(uint packedColor)
            {
                half4 color;
                color.r = (packedColor & 255u) / 255.0h;
                color.g = ((packedColor >> 8u) & 255u) / 255.0h;
                color.b = ((packedColor >> 16u) & 255u) / 255.0h;
                color.a = ((packedColor >> 24u) & 255u) / 255.0h;
                return color;
            }

            float2 SafeNormalize(float2 value)
            {
                float lengthSquared = max(dot(value, value), 1e-8);
                return value * rsqrt(lengthSquared);
            }

            float3 SafeNormalize(float3 value)
            {
                float lengthSquared = max(dot(value, value), 1e-8);
                return value * rsqrt(lengthSquared);
            }

            Varyings Vert(Attributes input)
            {
                LineVertexData currentVertex = _LineVertices[input.vertexID];
                uint pairBase = input.vertexID & ~1u;
                uint otherIndex = pairBase + ((input.vertexID & 1u) == 0u ? 1u : 0u);
                LineVertexData otherVertex = _LineVertices[otherIndex];

                float3 viewDirection = SafeNormalize(_WorldSpaceCameraPos.xyz - currentVertex.position);
                float3 worldPosition = currentVertex.position + (viewDirection * _DepthOffsetMeters);
                float4 currentClip = TransformWorldToHClip(worldPosition);
                float4 otherClip = TransformWorldToHClip(otherVertex.position + (SafeNormalize(_WorldSpaceCameraPos.xyz - otherVertex.position) * _DepthOffsetMeters));

                float2 currentNdc = currentClip.xy / max(abs(currentClip.w), 1e-5);
                float2 otherNdc = otherClip.xy / max(abs(otherClip.w), 1e-5);
                float2 perpendicular = float2(
                    -SafeNormalize(otherNdc - currentNdc).y,
                    SafeNormalize(otherNdc - currentNdc).x
                );
                float2 clipOffset = perpendicular * (_LineWidth * 2.0 / _ScreenParams.xy) * currentClip.w;

                Varyings output;
                output.positionCS = currentClip;
                output.positionCS.xy += clipOffset;
                output.color = DecodeColor32(currentVertex.color);
                output.color.a *= _Alpha;
                return output;
            }

            half4 Frag(Varyings input) : SV_Target
            {
                return input.color;
            }
            ENDHLSL
        }
    }
}
