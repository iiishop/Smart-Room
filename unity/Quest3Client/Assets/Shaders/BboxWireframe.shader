Shader "SmartRoom/BboxWireframe"
{
    Properties
    {
        _LineAlpha ("Line Alpha", Range(0, 1)) = 0.6
    }

    SubShader
    {
        Tags
        {
            "RenderType" = "Transparent"
            "Queue" = "Transparent"
            "RenderPipeline" = "UniversalPipeline"
            "IgnoreProjector" = "True"
        }
        Blend SrcAlpha OneMinusSrcAlpha
        ZWrite Off
        ZTest LEqual
        Cull Off

        Pass
        {
            Name "Forward"
            Tags { "LightMode" = "UniversalForward" }

            HLSLPROGRAM
            #pragma target 4.5
            #pragma vertex vert
            #pragma fragment frag

            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"

            StructuredBuffer<float3> _BboxCornerBuffer;
            StructuredBuffer<float4> _BboxColorBuffer;
            int _BboxCount;
            half _LineAlpha;

            static const uint CORNER_LUT[24] = {
                0, 1,   1, 2,   2, 3,   3, 0,
                4, 5,   5, 6,   6, 7,   7, 4,
                0, 4,   1, 5,   2, 6,   3, 7
            };

            struct Attributes
            {
                uint vertexID : SV_VertexID;
            };

            struct Varyings
            {
                float4 positionCS : SV_POSITION;
                float4 color : COLOR;
            };

            Varyings vert(Attributes input)
            {
                Varyings output;
                uint instanceIdx = input.vertexID / 24;
                uint localVertex = input.vertexID % 24;
                uint cornerIdx = CORNER_LUT[localVertex];
                float3 worldPos = _BboxCornerBuffer[instanceIdx * 8 + cornerIdx];
                output.positionCS = TransformWorldToHClip(worldPos);
                output.color = _BboxColorBuffer[instanceIdx];
                output.color.a *= _LineAlpha;
                return output;
            }

            half4 frag(Varyings input) : SV_Target
            {
                return half4(input.color.rgb, input.color.a);
            }
            ENDHLSL
        }
    }
}
