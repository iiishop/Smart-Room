Shader "SmartRoom/VisionLineUnlit"
{
    Properties
    {
        _TintColor ("Tint Color", Color) = (1, 1, 1, 1)
    }

    SubShader
    {
        Tags
        {
            "RenderPipeline" = "UniversalPipeline"
            "Queue" = "Transparent"
            "RenderType" = "Transparent"
            "IgnoreProjector" = "True"
        }

        Blend SrcAlpha OneMinusSrcAlpha
        Cull Off
        ZWrite Off
        ZTest Always

        Pass
        {
            Name "Forward"

            HLSLPROGRAM
            #pragma target 4.5
            #pragma vertex vert
            #pragma fragment frag

            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"

            struct LineVertexData
            {
                float3 positionWS;
                float4 color;
            };

            StructuredBuffer<LineVertexData> _LineVertices;

            CBUFFER_START(UnityPerMaterial)
                float4 _TintColor;
            CBUFFER_END

            struct Varyings
            {
                float4 positionCS : SV_POSITION;
                float4 color : COLOR0;
            };

            Varyings vert(uint vertexID : SV_VertexID)
            {
                LineVertexData vertex = _LineVertices[vertexID];

                Varyings output;
                output.positionCS = TransformWorldToHClip(vertex.positionWS);
                output.color = vertex.color * _TintColor;
                return output;
            }

            half4 frag(Varyings input) : SV_Target
            {
                return input.color;
            }
            ENDHLSL
        }
    }
}
