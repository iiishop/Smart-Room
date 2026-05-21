Shader "SmartRoom/BboxWireframe"
{
    Properties
    {
        _LineAlpha ("Line Alpha", Range(0, 1)) = 0.5
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
            Name "BboxWireframe"
            Tags { "LightMode" = "UniversalForward" }

            Blend SrcAlpha OneMinusSrcAlpha
            Cull Off
            ZWrite Off
            ZTest LEqual

            HLSLPROGRAM
            #pragma target 4.5
            #pragma vertex vert
            #pragma fragment frag
            #pragma enable_d3d11_debug_symbols
            #pragma instancing_options procedural:setup

            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"

            struct BboxWireframeInstance
            {
                float3 corner0;
                float3 corner1;
                float3 corner2;
                float3 corner3;
                float3 corner4;
                float3 corner5;
                float3 corner6;
                float3 corner7;
                float4 color;
            };

            StructuredBuffer<BboxWireframeInstance> _InstanceData;
            half _LineAlpha;

            static const uint2 _CornerPairLUT[12] = {
                uint2(0, 1), uint2(1, 2), uint2(2, 3), uint2(3, 0),
                uint2(4, 5), uint2(5, 6), uint2(6, 7), uint2(7, 4),
                uint2(0, 4), uint2(1, 5), uint2(2, 6), uint2(3, 7),
            };

            struct Attributes
            {
                uint vertexID : SV_VertexID;
                uint instanceID : SV_InstanceID;
            };

            struct Varyings
            {
                float4 positionCS : SV_POSITION;
                half4 color : COLOR0;
            };

            void setup() {}

            float3 GetCornerPosition(BboxWireframeInstance instance, uint cornerIndex)
            {
                switch (cornerIndex)
                {
                    case 0: return instance.corner0;
                    case 1: return instance.corner1;
                    case 2: return instance.corner2;
                    case 3: return instance.corner3;
                    case 4: return instance.corner4;
                    case 5: return instance.corner5;
                    case 6: return instance.corner6;
                    case 7: return instance.corner7;
                    default: return instance.corner0;
                }
            }

            Varyings vert(Attributes input)
            {
                BboxWireframeInstance instance = _InstanceData[input.instanceID];

                uint segmentIndex = input.vertexID / 2u;
                uint2 cornerPair = _CornerPairLUT[segmentIndex];
                uint cornerIndex = (input.vertexID & 1u) == 0u ? cornerPair.x : cornerPair.y;

                float3 worldPos = GetCornerPosition(instance, cornerIndex);

                Varyings output;
                output.positionCS = TransformWorldToHClip(worldPos);
                output.color = half4(instance.color.rgb, _LineAlpha);
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
