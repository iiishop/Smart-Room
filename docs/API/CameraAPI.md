## Required Permissions and Versions

| Item | Requirement |
|---|---|
| Horizon OS | v74+ |
| MRUK | v81+ |
| RGB camera permission | `horizonos.permission.HEADSET_CAMERA` |
| Scene / depth permission | `com.oculus.permission.USE_SCENE` |

---

## RGB Camera

### PassthroughCameraAccess

MRUK v81+ 组件，挂到 Unity GameObject 上使用。需要 `horizonos.permission.HEADSET_CAMERA` 权限，Horizon OS v74+。 

### CameraPositionType

| Value |      |      
| ------- | ----- |     
| Left | 左眼摄像头 |      
| Right | 右眼摄像头 |


---

### Properties

| Property | Type | Access | Description | 
|----------|------|--------|-------------|
| `CameraPosition` | `CameraPositionType` | RW | 使用左眼还是右眼摄像头 |
| `RequestedResolution` | `Vector2Int` | RW | 请求的图像分辨率。不支持时自动降为最接近的更低分辨率 |
| `MaxFramerate` | `int` | RW | 最大帧率。默认 60 FPS |
| `TargetMaterial` | `Material` | RW | 自动更新的材质（用来显示摄像头画面） |
| `TexturePropertyName` | `string` | RW | 材质上要更新的纹理属性名。默认 `"_MainTex"` | 
| `Intrinsics` | `CameraIntrinsics` | R | **静态内参。组件 enabled 后立即可用，不会变** |
| `IsPlaying` | `bool` | R | 摄像头是否正在运行。只有 `true` 时 `GetCameraPose()` 和 `ViewportPointToRay()` 才有效 |
| `CurrentResolution` | `Vector2Int` | R | 当前实际分辨率 |
| `Timestamp` | `DateTime` | R | 当前帧的时间戳 |

---

### CameraIntrinsics Struct

| Field | Type | Description | 
|-------|------|-------------| 
| `FocalLength` | `Vector2` | 像素焦距 `(fx, fy)` | 
| `PrincipalPoint` | `Vector2` | 主点 `(cx, cy)` |
| `SensorResolution` | `Vector2Int` | 传感器原生分辨率 `(width, height)` |
| `LensOffset` | `Pose` | 摄像头传感器相对于头显的 translation + rotation | 

---

### Methods

| Method | Returns | Description | 
|--------|---------|-------------| 
| `GetTexture()` | `Texture` | 当前摄像头纹理（RenderTexture）。enabled 后立即可用，前几帧可能全黑 |
| `GetColors()` | `Color32[]` | CPU 端获取当前帧颜色数组（快照模式） |
| `GetCameraPose()` | `Pose` | **RGB 摄像头的世界空间位姿。使用当前时间戳返回精确 pose** | 
| `ViewportPointToRay(Vector2 viewportPoint)` | `Ray` | 从摄像头出发、穿过归一化视口点的世界空间射线。视口空间归一化：左下 (0,0)，右上 (1,1) |
| `GetSupportedResolutions(CameraPositionType)` | `Vector2Int[]` | **静态方法**。返回指定位置摄像头所有支持的 resolution 列表。可在创建组件前调用 |

---

## Depth Sensor

### EnvironmentDepthFrameDesc


1. 通过 `Utils.GetEnvironmentalDepthFrameDesc(int eye)` 获取（命名空间 `Unity.XR.Oculus.Utils`）。需要 Scene 权限 `com.oculus.permission.USE_SCENE`。

   `eye` 参数对应左/右眼深度帧。使用前确认当前 SDK 版本中 `0 = left, 1 = right` 的映射关系——这会影响与左/右 RGB camera 的对应。

   ### Fields

   | Field | Type | Description |
   |-------|------|-------------| 
   | `isValid` | `bool` | 深度帧是否有效。`false` 时其他字段可能无效 | 
   | `createTime` | `double` | 深度帧创建时间戳 |
   | `predictedDisplayTime` | `double` | 深度帧预测显示时间 |
   | `swapchainIndex` | `int` | 当前深度帧在 swapchain 中的索引 |
   | `createPoseLocation` | `Vector3` | **深度帧创建时的摄像头位置（世界空间）** | 
   | `createPoseRotation` | `Vector4` | **深度帧创建时的摄像头朝向（四元数）** |
   | `width` | `int` | 深度帧宽度（像素）。⚠️ 官方 API 文档未列出此字段，来自 SDK 源码 `EnvironmentDepthApi.cs`，使用前在 v85 实测确认 |
   | `height` | `int` | 深度帧高度（像素）。⚠️ 官方 API 文档未列出，同上，在 v85 实测确认 |
   | `fovLeftAngle` | `float` | 光轴到左边缘的视场角**半角正切值**（tangent，⚠️ 非弧度）。值域通常在 0.8–1.5，对应 ~40–55° 半角。已通过数学验证（tangent假设误差0°，弧度假设误差41°）和 t-34400/QuestRealityCapture CSV 列名 `fov_left_angle_tangent` 双重确认。**不要对其调 `tan()`** |
   | `fovRightAngle` | `float` | 光轴到右边缘的半角正切值（同上） |
   | `fovTopAngle` | `float` | 光轴到上边缘的半角正切值（同上） | 
   | `fovDownAngle` | `float` | 光轴到下边缘的半角正切值（同上） |
   | `nearZ` | `float` | 深度相机近裁剪面（米） |
   | `farZ` | `float` | 深度相机远裁剪面（米） |
   | `minDepth` | `float` | 当前帧最小深度值（米） |
   | `maxDepth` | `float` | 当前帧最大深度值（米） | 
   
   ---

   ### Depth Texture Access (`_EnvironmentDepthTexture`)

      深度图本身通过 `EnvironmentDepthManager` 自动设置的全局 shader property 获取（v67+ SDK）：

      | Property | Type | Description |
      |----------|------|-------------|
      | `Shader.GetGlobalTexture("_EnvironmentDepthTexture")` | `Texture` | 当前深度纹理。`EnvironmentDepthManager` 每帧自动更新。在 shader 或 compute shader 中通过 `sampler2D _EnvironmentDepthTexture` 访问 |
   | 分辨率 | — | `EnvironmentDepthFrameDesc.width × height`。⚠️ 分辨率字段未被官方文档列出，v85 实测确认 |
   | 值格式 | `float` (0–1) | **NDC 深度（非线性）**，类似 OpenGL Z-buffer，非直接线性值。`0.0 = nearZ`, `1.0 = farZ`，但中间值不是均匀分布。需通过 `_EnvironmentDepthZBufferParams` 线性化后才能得到米制距离 |
   | 无效像素 | `0.0` | 超出深度传感器范围或遮挡区域的像素值为 0 |

   **线性化公式（shader 端）：**
   ```hlsl
   float ndc = depthValue * 2.0 - 1.0;
   float linearDepth = (1.0 / (ndc + _EnvironmentDepthZBufferParams.y)) * _EnvironmentDepthZBufferParams.x;
   ```
   其中 `_EnvironmentDepthZBufferParams` 是 `EnvironmentDepthManager` 自动设置的全局 `float4`，由 `nearZ` / `farZ` 计算得出。

   **CPU 端等效（无需 `_EnvironmentDepthZBufferParams`）：**
   ```python
   # nearZ, farZ 来自 EnvironmentDepthFrameDesc
   linear_depth = nearZ * farZ / (farZ - depth_value * (farZ - nearZ))
   ```
   这是标准 OpenGL depth linearization 公式，与 Meta shader 公式等价。

> **确认来源：** Meta XR Core SDK 源码 `EnvironmentOcclusion.cginc`（`SampleEnvironmentDepthLinear_Internal` 函数）+ Meta 官方博客（"convert data from NDC to linear depth using `_EnvironmentDepthZBufferParams`"）。已实测验证公式等价性。不再需要"需实测确认"——此格式已从 Meta 源码确认。

若需 CPU 端读取深度值，通过 `AsyncGPUReadback.RequestIntoNativeArray` 将纹理读回 `NativeArray<float>`，再用上述 CPU 公式线性化。

   ---

   ### 从 FOV Tangents 推算 K_depth

   这些字段存储的是半角正切值（tangent，⚠️ 非弧度）。反算内参时直接使用原始值，不要对其调 `tan()`。

   ```python
   # fovLeftAngle 等字段已经是 tangent 值，直接使用
   tan_right = fovRightAngle    # 已经是 tan(half_angle_right)
   tan_left  = fovLeftAngle     # 已经是 tan(half_angle_left)
   tan_top   = fovTopAngle      # 已经是 tan(half_angle_top)
   tan_down  = fovDownAngle     # 已经是 tan(half_angle_down)
   
   K_depth:
     fx = depth_width  / (tan_right + tan_left)
     fy = depth_height / (tan_top + tan_down)
     cx = depth_width  * tan_right / (tan_right + tan_left)
     cy = depth_height * tan_top  / (tan_top + tan_down)
   ```

   \---

   ### 额外：Android Camera2 获取深度相机内参

开源 `UGX.QuestCamera` 插件直接通过 Android Camera2 API 的 JNI 桥接获取深度相机完整参数。返回的是 `[fx, fy, cx, cy, s]` 五元素 intrinsics 数组 + 6 元素畸变系数数组 + `[tx, ty, tz, qx, qy, qz, qw]` pose。这意味着深度相机底层也暴露了 Camera2 接口，拿到的内参是直接值，不需要从 FOV Angle 反算。这条路径走 JNI，不走 Unity C#，部署复杂一些但拿到的内参是完整的。

**Camera2 畸变系数数组（6 元素）：**

| 索引 | 名称 | 说明 |
|------|------|------|
| 0 | `k1` | 径向畸变一阶系数 |
| 1 | `k2` | 径向畸变二阶系数 |
| 2 | `p1` | 切向畸变一阶系数 |
| 3 | `p2` | 切向畸变二阶系数 |
| 4 | `k3` | 径向畸变三阶系数 |
| 5 | `k4` | 径向畸变四阶系数 |

适用于鱼眼畸变模型（`CameraCharacteristics.LENS_DISTORTION`）。配合 `Camera2.fisheye.initUndistortRectifyMap()` 可生成去畸变映射表。

---

   **关于对齐的结论：关键参数都能拿到。** 
   | 参数 | 来源 | 路径 | 
   |------|------|------| 
   | K_rgb | `CameraIntrinsics.FocalLength` + `PrincipalPoint` | Unity C# |
   | K_depth | FOV Angle 反算（针孔近似），或 Camera2 JNI 直读（含畸变，更精确） | Unity C#，或 Android JNI | 
   | T_world_rgb | `GetCameraPose()` | Unity C# |
   | T_world_depth | `createPoseLocation` + `createPoseRotation` | Unity C# |
   | R, t（外参） | `T_world_depth⁻¹ × T_world_rgb` 计算得出 | 数学推导 |

   其中 K_depth 从 FOV Angle 反算得到的是**针孔模型近似**，若宽视场/鱼眼畸变导致对齐误差不可接受，可走 Camera2 JNI 路径获取完整的 intrinsics + distortion coefficients。

   ---

   ### 坐标空间说明

   **像素坐标系原点差异：**

   | 空间 | 原点 | Y 轴方向 | 来源 |
   |------|------|----------|------|
   | Unity viewport（`ViewportPointToRay`） | 左下 `(0,0)` | 向上 | `PassthroughCameraAccess` |
   | 图像/掩码像素数组 | 左上 `(0,0)` | 向下 | SAM2 mask、`Texture2D.GetPixels()` |

   **y-flip：** 后端 SAM2 返回的 mask 像素坐标是左上原点。在使用 `ViewportPointToRay` 或投影计算前，必须将 `y_pixel` 翻转为 `y_viewport = height - 1 - y_pixel`。未翻转将导致 3D 锚点映射到错误方向。

   **世界坐标系：** Unity 左手坐标系。RGB 相机 forward = +Z（穿过镜头的方向）。深度相机 forward 同为 +Z。

   **Timestamp 同步：** RGB 帧时间戳（`PassthroughCameraAccess.Timestamp`）与深度帧时间戳（`EnvironmentDepthFrameDesc.createTime` / `predictedDisplayTime`）来自不同传感器，应取差值最小的帧对。帧间时间差过大（>50ms）且头显运动显著时，3D 锚点将偏移。

---

## `_EnvironmentDepthReprojectionMatrices`

**定义（来自 Meta SDK 源码 `EnvironmentOcclusion.cginc`）：**
```hlsl
uniform float4x4 _EnvironmentDepthReprojectionMatrices[2];
```
`float4x4[2]` — 2 个 4×4 矩阵，一个左眼一个右眼，索引为 `unity_StereoEyeIndex`。`EnvironmentDepthManager` 每帧自动更新。

**用途：** 将世界坐标投影到深度纹理 UV 空间，用于遮挡判断（shader 中比较虚拟物体深度与真实环境深度）。**注意方向：世界 → 深度 UV**，不是像素 → 世界。

**来源确认：**
- Meta SDK 源码：`EnvironmentOcclusion.cginc` 第 2 行
- v85 C# 等效：`DepthTextureAccess.DepthFrameData.ViewProjectionMatrix`（`Matrix4x4[]`，同样 `[2]`）
- Meta 官方博客：*"_EnvironmentDepthReprojectionMatrices (view-projection matrices of the depth cameras)"*

---

### Shader 端用法（来自 Meta SDK 源码）

```hlsl
// 世界坐标 → 深度纹理 UV（用于遮挡判断）
const float4 depthSpace = mul(_EnvironmentDepthReprojectionMatrices[unity_StereoEyeIndex], float4(worldCoords, 1.0));
const float2 uvCoords = (depthSpace.xy / depthSpace.w + 1.0f) * 0.5f;

// 线性化该 UV 处的环境深度值
float linearSceneDepth = (1.0f / ((depthSpace.z / depthSpace.w) + _EnvironmentDepthZBufferParams.y)) * _EnvironmentDepthZBufferParams.x;
```

---

### C# 端获取（v85 `DepthTextureAccess`）

```csharp
// DepthTextureAccess.DepthFrameData 暴露 C# 端的 ViewProjectionMatrix
depthTextureAccess.OnDepthTextureUpdateCPU += (depthFrameData) =>
{
    Matrix4x4[] viewProjectionMatrices = depthFrameData.ViewProjectionMatrix; // Matrix4x4[2]
    Pose cameraPose = depthFrameData.CameraPose;
    NativeArray<float> depthPixels = depthFrameData.DepthTexturePixels;
};
```

---

### 对 RGB-D 对齐的意义

`_EnvironmentDepthReprojectionMatrices` 的方向是**世界 → 深度 UV**，而 RGB-D 对齐需要的是**深度像素 → 世界**。两个方向相反。因此：

- **不要尝试在此矩阵上做方向推导**——它不是为 RGB-D 对齐设计的。
- **正确做法（C# 端）：** 使用 `EnvironmentDepthFrameDesc` 的 FOV Angle 推 K_depth + `createPose` 做反投影。这是文档上文 [从 FOV Angle 推算 K_depth](#从-fov-angle-推算-k_depth) 一节描述的路径。
- **如需更精确的对齐：** 走 Camera2 JNI 路径获取完整深度相机内参 + 畸变系数。

---

## Minimal API Checklist for RGB-D Anchoring

| Need | API / Field |
|---|---|
| RGB image | `PassthroughCameraAccess.GetTexture()` or `GetColors()` |
| RGB intrinsics | `PassthroughCameraAccess.Intrinsics` |
| RGB pose | `PassthroughCameraAccess.GetCameraPose()` |
| RGB timestamp | `PassthroughCameraAccess.Timestamp` |
| Depth frame desc | `Utils.GetEnvironmentalDepthFrameDesc(eye)` |
| Depth texture | `Shader.GetGlobalTexture("_EnvironmentDepthTexture")`（v67+ SDK） |
| Depth resolution | `EnvironmentDepthFrameDesc.width`, `height`（v85 实测确认） |
| Depth pose | `createPoseLocation`, `createPoseRotation` |
| Depth FOV | `fovLeftAngle`, `fovRightAngle`, `fovTopAngle`, `fovDownAngle` |
| GPU reprojection | `_EnvironmentDepthReprojectionMatrices` |
| Depth linearization params | `_EnvironmentDepthZBufferParams`（NDC → 米制） |
| CPU depth readback | `AsyncGPUReadback.RequestIntoNativeArray`（读回后需 CPU 线性化） |