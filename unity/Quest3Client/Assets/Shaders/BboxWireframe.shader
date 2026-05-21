Shader "SmartRoom/BboxWireframe"
{
    Properties
    {
        _LineAlpha ("Line Alpha", Range(0, 1)) = 0.8
    }

    SubShader
    {
        Tags
        {
            "RenderType" = "Transparent"
            "Queue" = "Transparent"
            "IgnoreProjector" = "True"
            "RenderPipeline" = "UniversalPipeline"
        }

        Blend SrcAlpha OneMinusSrcAlpha
        ZWrite Off
        ZTest LEqual
        Cull Off

        Pass
        {
            Name "BboxWireframe"
            Tags { "LightMode" = "UniversalForward" }

            HLSLPROGRAM
            #pragma target 4.5
            #pragma vertex vert
            #pragma fragment frag
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
                half4 color;
            };

            StructuredBuffer<BboxWireframeInstance> _BboxBuffer;
            half _LineAlpha;

            static const int kEdgePairs[24] =
            {
                0, 1,
                0, 2,
                1, 3,
                2, 3,
                4, 5,
                4, 6,
                5, 7,
                6, 7,
                0, 4,
                1, 5,
                2, 6,
                3, 7
            };

            float3 GetCorner(BboxWireframeInstance inst, int idx)
            {
                if (idx <= 0) return inst.corner0;
                if (idx == 1) return inst.corner1;
                if (idx == 2) return inst.corner2;
                if (idx == 3) return inst.corner3;
                if (idx == 4) return inst.corner4;
                if (idx == 5) return inst.corner5;
                if (idx == 6) return inst.corner6;
                return inst.corner7;
            }

            struct Attributes
            {
                uint vertexID : SV_VertexID;
                uint instanceID : SV_InstanceID;
            };

            struct Varyings
            {
                float4 positionCS : SV_POSITION;
                half4 color : COLOR;
            };

            void setup()
            {
            }

            Varyings vert(Attributes input)
            {
                Varyings output;
                BboxWireframeInstance inst = _BboxBuffer[input.instanceID];
                int cornerIdx = kEdgePairs[input.vertexID];
                float3 worldPos = GetCorner(inst, cornerIdx);
                output.positionCS = TransformWorldToHClip(worldPos);
                output.color = half4(inst.color.rgb, _LineAlpha);
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
