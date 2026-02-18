# Quest 3 Unity SDK Reference (for current MVP)

This list keeps only links relevant to Quest 3 Unity development for RGB/depth/interaction and scene data.

## Core

- Meta Unity docs root:
  - https://developers.meta.com/horizon/documentation/unity/
- Meta XR Interaction SDK (overview/get started):
  - https://developers.meta.com/horizon/documentation/unity/unity-isdk-getting-started/

## Scene and Spatial Data

- Scene overview:
  - https://developers.meta.com/horizon/documentation/unity/unity-scene-overview/
- Spatial data permission:
  - https://developers.meta.com/horizon/documentation/unity/unity-spatial-data-perm/
- MRUK overview:
  - https://developers.meta.com/horizon/documentation/unity/unity-mr-utility-kit-overview/
- MRUK scene data:
  - https://developers.meta.com/horizon/documentation/unity/unity-mr-utility-kit-features-working-with-scene-data
- Migration note (OVRSceneManager -> MRUK):
  - https://developers.meta.com/horizon/documentation/unity/unity-scene-migrate-mruk/

## Depth and Passthrough

- Depth API overview:
  - https://developers.meta.com/horizon/documentation/unity/unity-depthapi-overview/
- Depth API sample repo:
  - https://github.com/oculus-samples/Unity-DepthAPI
- Passthrough overview:
  - https://developers.meta.com/horizon/documentation/unity/unity-passthrough/

## OpenXR route (optional alternative)

- Unity OpenXR Meta package docs:
  - https://docs.unity3d.com/Packages/com.unity.xr.meta-openxr@2.3/manual/
- Camera (Passthrough) feature:
  - https://docs.unity3d.com/Packages/com.unity.xr.meta-openxr@2.3/manual/features/camera.html
- Meshing feature:
  - https://docs.unity3d.com/Packages/com.unity.xr.meta-openxr@2.0/manual/features/meshing.html

## Notes for this project

- MRUK is an official Meta Unity toolkit package, not a built-in Unity default module.
- For lamp-level object recognition, MRUK alone is insufficient. Add CV model later if needed.
