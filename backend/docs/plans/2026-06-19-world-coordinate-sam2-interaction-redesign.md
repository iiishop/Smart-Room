# Smart Room: World-Coordinate SAM2 Interaction Redesign

> **日期**: 2026-06-19  
> **基线 commit**: `23cdf6a` (SAM2.1-small segmentation pipeline with RGB-D depth-guided prompts)  
> **决策**: 回退 VR 锚点、轮廓渲染、双阶段膨胀等后续 commit，从干净基线重新设计

---

## 1. 当前基线状态

`23cdf6a` 包含：
- `viewer/cursor_prompt_projector.py` — 世界坐标 → 像素投影
- `viewer/depth_prompt_builder.py` — 深度组件分割提示构建
- `viewer/sam2_device_segment.py` — SAM2.1-small 封装（set_image / re_predict）
- `viewer/quest3_rgbd_align_viewer.py` — HTTP server（8500端口），multipart RGB+深度接收 + 对齐显示 + Device 标签页

**已丢弃**（不回退恢复）：VR 锚点 `TrackingManager.cs`、轮廓线渲染、`pose_projection.py`、两阶段膨胀、Device 标签页 UI。

---

## 2. Meta XR 世界坐标系统——完整调研

### 2.1 Stationary Tracking Origin

**类型**: 实验性 `OriginType.Stationary`

关键行为（直接引自 Meta XR Core SDK 文档 v85）：

> *"Stationary tracks the position and orientation relative to a fixed location in the real world. It stays at the same fixed location as long as its ID stays the same, even across multiple application sessions."*

**API 调用**:
```csharp
// 获取当前 tracking origin 的 UUID
var id = OVRPlugin.GetStationaryReferenceSpaceId();
// 可以与本地存储的 UUID 比较，相同则原点一致
```

**底层 OpenXR 扩展**: `XR_EXT_stationary_reference_space`
```cpp
// C++ 层通过 xrGetStationaryReferenceSpaceGenerationIdEXT 函数获取 UUID
// 若 generation ID 一致，则原点位置相同
```

**注意事项**:
- 仅与 Meta Core SDK 组件兼容（`OVRCameraRig`），不可用 Unity 通用 `XR Origin / Tracked Pose Driver`
- 若 tracking lost 且恢复后 origin 变化，触发 `OVRManager.TrackingOriginChangePending` 事件
- SDK 中标注为实验性，API 可能在未来版本调整

### 2.2 单位

Meta Quest SDK 全部使用**米**作为空间单位。`OVRManager.boundary.GetDimensions()` 返回的 `Vector3` 各分量就是米。深度 API 返回的深度值也是米。（来源：`OVRBoundary.GetDimensions()` 文档注释明确说明 height/width/depth 为 "tracking space units"，Unity 默认对应米。）

### 2.3 Passthrough Camera API 的世界坐标桥梁

`PassthroughCameraAccess` 类（Meta XR MRUK SDK v83+）提供三个核心方法：

| 方法 | 输入 | 输出 | 用途 |
|---|---|---|---|
| `GetCameraPose()` | — | `Pose` (世界空间) | 获取 RGB 摄像头在**世界空间**的当前位姿 |
| `ViewportPointToRay(Vector2, Pose?)` | 归一化视口坐标 [0,1] + 可选缓存位姿 | `Ray` (世界空间) | 从摄像头原点穿过图像像素的世界空间射线 |
| `WorldToViewportPoint(Vector3, Pose?)` | 世界空间坐标 + 可选缓存位姿 | `Vector2` [0,1] | 世界坐标 → 图像视口坐标 |

**关键设计——可选的缓存位姿参数**（v83 新增，v85 完整文档化）：

> *"Optional camera pose that should be used for calculation. For example, you can cache GetCameraPose, do a long-running image processing, then use the cached camera pose with this method."*

这意味着可以把截图瞬间的相机位姿存下来，**之后任意时间拿到 3D 坐标，都能准确算出它在当时那张截图里的像素位置。**

来源：
- [PassthroughCameraAccess v85 API Reference](https://developers.meta.com/horizon/reference/mruk/v85/class_meta_x_r_passthrough_camera_access/)
- [Mapping camera image to world space](https://developers.meta.com/horizon/documentation/unity/unity-pca-documentation/)
- [CameraToWorld 官方样例](https://developers.meta.com/horizon/documentation/unity/unity-sample-camera-to-world/)
- [Migration from WebCamTexture](https://developers.meta.com/horizon/documentation/unity/unity-pca-migration-from-webcamtexture/)

### 2.4 深度 API 的坐标空间

`EnvironmentDepthFrameDesc` 结构体包含：
- `createPoseLocation` / `createPoseRotation` — 深度帧创建时的世界空间位姿
- `fovLeftAngle` / `fovRightAngle` / `fovTopAngle` / `fovDownAngle` — 深度帧 FOV
- `nearZ` / `farZ` — 近远裁剪面

深度纹理本身是摄像头相对坐标，但结合 `createPose` 和 FOV 可以转换为世界空间。

`EnvironmentDepthManager` 全局设置以下 shader 属性：
- `_EnvironmentDepthTexture` — 环境深度图（实时，实时深度 API，非 Scene）
- `_EnvironmentDepthReprojectionMatrices` — 重投影矩阵（depth camera → world）
- `_EnvironmentDepthZBufferParams` — 深度值线性化参数

Shader 中可以直接使用 `META_DEPTH_GET_OCCLUSION_VALUE_WORLDPOS(posWorld, zBias)` 宏从世界位置查询深度遮挡值。

来源：
- [Depth API Overview](https://developers.meta.com/horizon/documentation/unity/unity-depthapi-overview/)
- [Depth API Shader Reference](https://latest.developers.meta.com/horizon/documentation/unity/unity-depthapi-api-reference/)
- [EnvironmentDepthManager v77 API](https://developers.meta.com/horizon/documentation/unity/v77/class_meta_x_r_environment_depth_environment_depth_manager/)

### 2.5 跨 Session 持久化

`OVRSpatialAnchor` 提供完整的 save/load 生命周期：
- 创建锚点 → 等待 `localized` 状态 → `Save()` 写入持久存储
- 下次启动用 UUID list `LoadUnboundAnchors()` → 等待 `localized`
- 若 tracking origin generation ID 一致 + 锚点 localized 成功，物理位置正确恢复

结合 Stationary origin，可以实现"重启后绿色小球还在原地"。

### 2.5.1 多房间 / 多场景 reference space 管理

Stationary origin 的直接目的不是替代业务层场景管理，而是为每个真实空间提供一个可验证的参考系 ID。项目目标不止一个房间，因此实现时不要把所有锚点默认写进单一全局坐标空间，而应保留"场景 / 房间 / reference space"这一层抽象。

建议数据模型：

```json
{
  "room_id": "ucl_lab_room_a",
  "room_label": "UCL Lab Room A",
  "stationary_reference_space_id": "uuid-or-generation-id",
  "created_at": "2026-06-21T00:00:00Z",
  "updated_at": "2026-06-21T00:00:00Z"
}
```

每个 capture session、设备、截图、世界锚点都应关联到当前 `room_id` 和 `stationary_reference_space_id`。启动时读取当前 `OVRPlugin.GetStationaryReferenceSpaceId()`：

- 若 ID 匹配已有 room，则进入该 room 的坐标系，允许加载历史设备/锚点。
- 若 ID 不匹配，则提示选择已有 room、创建新 room，或进入未绑定临时 session。
- 若用户明确切换房间，必须开始新的 room/session，避免把不同物理空间的世界坐标混到一起。

因此 Phase 1 需要至少保留接口和日志：记录当前 Stationary reference space ID，并把它随 capture metadata 一起传给后端；完整 UI 选择房间可以后置。

### 2.6 关于实时深度（无 Scene setup）的限制

本项目使用**实时深度 API**（`EnvironmentDepthManager` + `USE_SCENE` 权限），不依赖 Scene Model / Scene API（`ROOM_SETUP` / 用户扫描）。

实时深度是逐帧从深度传感器生成的，不要求用户扫描房间。优势是动态元素（人、移动物体）能正确遮挡。劣势是精度低于 Scene Model 重建——文档提到 "limited accuracy beyond 4 meters" 和对细小物体的边缘捕捉不足。

**决策**: 因为展厅场景（房间内小传感器）深度范围远小于 4m，且用户排斥扫描流程，保持实时深度，不做 Scene setup 要求。

---

## 3. 交互设计 — Plan A: 3D 世界锚点方案（主方案）

### 3.1 世界观

- 世界原点 = Stationary tracking origin
- XYZ 单位 = 米
- 右手拿控制器，扳机放置锚点，A 键切换正/负模式
- 深度表面绿色小球始终跟随右手射线，显示该点的世界坐标 (X, Y, Z)
- 当前 `TriggerDepthProbeRoot` 的点云探针可以与锚点交互共存；它作为调试/辅助可视化保留，不视为与扳机放置锚点冲突。

### 3.2 正负锚点放置

1. 右手射线 hit 到环境深度表面 → 绿色小球显示，文字叠加显示世界坐标 (e.g., "X:1.23 Y:0.89 Z:2.45")
2. 放置第一个锚点（绿/红）时 → **立即截图**（RGB + 深度 + 相机位姿），传输到后端
3. 后续锚点 → 先判断是否在已有截图可见范围内（见 §4 截取判断逻辑）
4. A 键切换正/负 → 小球颜色同步变为绿/红
5. B 键：短按删除最后一个锚点，长按清空全部锚点

### 3.3 轮廓线

**不要轮廓线**。仅保留正负锚点小球在当前和截图上的显示。

### 3.4 完成设备

按下 Grip → 当前设备 capture 完毕：
- 所有本设备截图 + 所有锚点坐标（世界空间）→ 打包送入后续分割管线
- 进入"下一个设备"的新 capture 阶段

### 3.5 左手显示器

Quest 内左手显示一个虚拟显示器面板，内容：
- 当前设备所有已截图的缩略图（横向排列，可左右翻页）
- 每张图上叠加正负锚点的 2D 投影位置
- 后端 SAM2 返回的 mask 实时叠加在对应截图上
- Viewer 的 Device 标签页预览逻辑搬到这里

---

## 4. 技术深度: 截取判断逻辑

### 4.1 核心问题

放置新锚点时，如何判断是否需要截新图？
- 太严格 → 用户稍微转头就截新图，白费带宽和推理资源
- 太松 → 锚点在画面外或被遮挡后仍沿用旧图，分割无意义

### 4.2 判断流程

当用户放置第 N 个锚点（世界坐标 `P_world`）时，对每张已有截图执行：

```
Input:  P_world (新锚点3D坐标)
        截图列表 [{rgb, depth, cameraPose}]

For each 截图:
    // Step 1: 投影到视口（使用缓存位姿）
    uv = WorldToViewportPoint(P_world, cachedPose=截图.cameraPose)

    // Step 2: 视口边界检查
    if uv.x < margin OR uv.x > (1 - margin) OR 
       uv.y < margin OR uv.y > (1 - margin):
        → 返回 INVALID (点在画面外)

    // Step 3: 深度遮挡检查
    pixel_xy = (uv.x * width, uv.y * height)
    sceneDepth = 截图.depth.readPixel(pixel_xy)
    anchorDepth = P_world在相机坐标系下的Z分量
    
    if anchorDepth > sceneDepth + depthTolerance:
        → 返回 OCCLUDED (被其他物体遮挡)

    // Step 4: 通过
    return VALID(截图, uv)

如果所有截图都返回 INVALID 或 OCCLUDED:
    → 需要截取新图
否则:
    → 共用已有截图，不截新图
```

### 4.3 容差参数

| 参数 | 建议值 | 依据 |
|---|---|---|
| 视口边缘 margin | 0.10（即 uv ∈ [0.1, 0.9]） | 边缘镜头畸变大，留 10% 安全区 |
| 深度一致性容差 | 0.10 米 | Meta 环境深度默认 bias = 0.06m（z-fighting 用途），0.10m 略大于此值，覆盖 ML 抖动 |

### 4.4 为什么 0.10m 不会误判

场景中的遮挡通常涉及几十厘米以上的深度差（墙面到面前物体 ≈ 0.3m+，桌面到地面 ≈ 0.7m+）。10cm 容差只覆盖传感器噪声和 ML 抖动，不足以跨越真实遮挡间隙。

### 4.5 深度采样精度

已知限制：实时深度对细小物体边缘（如传感器边角）可能不准。对此问题：
- 在锚点放置时，用户手指指向深度表面，Quest 的 `OVRHand` mesh 已被 `RemoveHands` 选项从深度图中扣除
- 可以在 anchor 投影位置周围取 3x3 深度中值，提升稳定性

---

## 5. SAM2 多锚点策略

### 5.1 SAM2 prompt 行为差异

官方 issue [#526](https://github.com/facebookresearch/sam2/issues/526) 确认：**逐个加点和一次性全加，结果不同。**

原因：SAM2 内部，逐个添加为每个点创建独立的 prompt token；一次性全加则将多个点合并成一个 prompt token 进行处理。合并 token 的方式在语义上更接近"这些点属于同一个对象"。

### 5.2 策略

```
截图级（同一张图内）:
  - 第一个锚点 → set_image() → 获取 image embedding
  - 后续同图的锚点 → add_new_points_or_box() 增量添加
  - 注意: 逐个加 vs 全加结果不同，但交互反馈接受微小差异

跨截图（新截图）:
  - 把设备所有已有锚点投影到新图 → 一次性全加（批量 prompt）
  - 一次性全加优于逐个加，因为批量 token 语义更一致
```

### 5.3 后端 prompt 格式

```json
{
  "image_ref": "capture_001",
  "positive_points": [[u1, v1], [u2, v2], ...],
  "negative_points": [[u3, v3], ...],
  "is_new_image": true
}
```

后端：
- `is_new_image=true` → `set_image()` + prompt 一次性全加
- `is_new_image=false` → `add_new_points_or_box()` 增量

---

## 6. 参考: ObjectCarver 论文

### 6.1 引用

> Hassena, G., Moon, J., Fujii, R., Yuen, A., Snavely, N., Marschner, S., & Hariharan, B. (2024). *ObjectCarver: Semi-automatic segmentation, reconstruction and separation of 3D objects.* arXiv:2407.19108.

项目页: https://objectcarver.github.io/

### 6.2 与本方案的相似性

ObjectCarver 的流程与本方案高度一致：

1. 用户在一张图上点击几个点 → SAM 生成 anchor mask
2. 把 mask 像素用深度 unproject 到 3D 点云
3. 把 3D 点云投影到**所有其他视角**的图片上 → **检查遮挡**
4. 投影后的点作为 SAM 在其他视角的 seeds → 生成新 mask
5. 迭代：所有视角都有 mask 后，互相传播覆盖未见过区域

Key insight：他们的遮挡检查是核心——用深度来区分"点在当前视角画面内但被挡"vs"点确实可见"。我们不需要多视图重建，只需要"这个 3D 锚点在旧截图里是否可见"——本质上是同一问题的简化版。

### 6.3 与本方案的区别

| 维度 | ObjectCarver | 本方案 |
|---|---|---|
| 输入 | 预录多视角图像 | 实时 AR 交互截图 |
| 输出目标 | 完整 3D 对象表面重建 | 2D mask（供后续用途） |
| 锚点来源 | 鼠标点击 | Quest 控制器空间射线 |
| 多视图 | 离线所有 | 按需增量 |
| 深度来源 | NeRF/SfM 重建深度 | 实时深度传感器 |

---

## 7. 架构概览

```
┌─ Quest 3 (Unity) ─────────────────────────────┐
│                                                │
│  Stationary Tracking Space                     │
│  ├── 右手射线 → 绿色小球 (世界坐标 XYZ 叠加)    │
│  ├── A 键 → 正/负模式切换 (绿/红)               │
│  ├── B 键 → 撤销最后 / 清空全部                 │
│  ├── Grip → 完成当前设备                         │
│  │                                             │
│  ├── 截取管理器                                 │
│  │   ├── 判断是否需要新截图                      │
│  │   ├── 获取相机纹理 + 深度图 + 相机位姿        │
│  │   └── 打包发送到后端                         │
│  │                                             │
│  └── 左手显示器                                 │
│      ├── 设备截图缩略图（可翻页）                │
│      ├── 叠加锚点投影 + SAM2 mask               │
│      └── 内容从后端 WebSocket 推流               │
└────────────────────────────────────────────────┘
         │
         ▼ HTTP multipart (RGB + depth + camera pose + anchors)
┌─ 后端 (Python viewer) ─────────────────────────┐
│                                                │
│  HTTP Handler                                  │
│  ├── 接收截图 → 缓存 [rgb, depth, cameraPose]  │
│  ├── 世界坐标 → 视口投影 (复用 cachedPose)     │
│  ├── 深度遮挡检查                              │
│  └── 调用 SAM2 segmenter                      │
│                                                │
│  SAM2 Device Segmenter                        │
│  ├── set_image() → new screenshot             │
│  ├── add_new_points_or_box() → 增量锚点       │
│  └── 返回 mask → WebSocket 推送到 Quest       │
│                                                │
│  WebSocket Server                              │
│  └── 推送 mask preview 到左手显示器            │
└────────────────────────────────────────────────┘
```

---

## 8. 备选方案: Plan B — 左手面板交互（简化方案）

如果 Plan A 的截取判断逻辑实现复杂度过高，备选方案：

### 8.1 交互描述

- 左手面板上显示当前 RGB 相机画面（最后一次截图）
- **左手控制器**: Trigger 放大、Grip 缩小、摇杆滚动/平移
- **右手控制器**: 在左手面板上放置正负锚点（射线命中面板上的图片像素）
- 右手射线被面板拦截 = 小球始终在面板图片上，而不是房间深度
- B 键删除锚点，Grip 完成设备

### 8.2 优势

- 零截取判断逻辑——始终使用用户主动在面板上看到的那张图
- 更接近传统"桌面应用"的标注体验
- 用户主动缩放/移动面板来探索设备细节

### 8.3 劣势

- 失去了"指物即见"的空间直观性——所有操作回到 2D 面板
- 不展示世界坐标 → 不满足"XYZ 坐标以米为单位在 Preview 显示"的需求
- 左手面板的空间映射需要额外开发（世界空间面板 → 图片空间的双向变换）

### 8.4 比较矩阵

| 维度 | Plan A (3D 锚点) | Plan B (面板) |
|---|---|---|
| 空间直观性 | 高——直接指着物体 | 低——在面板上操作 |
| XYZ 世界坐标 | 自然支持 | 需要额外计算 |
| 实现复杂度 | 截取判断逻辑 | 面板交互 + 空间变换 |
| 依赖 Meta XR 特性 | 深度 API, WorldToViewport(缓存位姿) | 深度 API |
| 用户学习成本 | 低 | 中 |
| 论文支持 | ObjectCarver 验证 | 无直接参考 |

**建议**: 优先实现 Plan A；若截取判断实现中遇到不可逾越的技术障碍，降级到 Plan B。

---

## 9. 实现路线图

### Phase 1: 世界坐标基础 (Quest 3 侧)

1. 切换 tracking origin 到 Stationary
2. 验证 `GetStationaryReferenceSpaceId()` 跨 session ID 一致性
3. 深度表面小球上叠加 XYZ 世界坐标文字
4. 为多房间保留 reference space 接口：capture/session metadata 记录当前 `stationary_reference_space_id`，后端按 room/session 维度保存，完整房间选择 UI 后置

### Phase 2: 锚点 + 截图

4. 实现正负锚点放置（A 键切换、颜色同步）
5. B 键撤销逻辑
6. 首次锚点 → 截图 + 相机位姿缓存 + 传输后端
7. 截取判断逻辑（WorldToViewportPoint + 缓存位姿 + 深度遮挡检查）

### Phase 3: 后端 SAM2

8. 后端接收截图 + 锚点 → SAM2 set_image / add_new_points
9. 跨截图锚点投影 → 批量 prompt → SAM2

### Phase 4: 左手显示器 + WebSocket

10. 左手虚拟显示器 UI（截图缩略图、翻页、锚点叠加、mask 叠加）
11. WebSocket 推送后端 mask 结果到 Quest

### Phase 5: 完成设备 + 后续管线

12. Grip → 打包所有截图 + 锚点 + mask → 送入下游管线
13. 空间锚点持久化（OVRSpatialAnchor）+ 跨 session 恢复

---

## 10. 未解决的问题 / 待验证

1. **Stationary origin 的 Unity 兼容性**: 是否真的只与 `OVRCameraRig` 兼容？（当前项目是否用 `OVRCameraRig`？需要确认。）

2. **实时深度的锚点 Z 精度**: 如果用户在距离 3m 以上放置锚点，ML 深度误差可能影响投影精度。（建议展厅场景限制交互范围 < 3m。）

3. **SAM2 image embedding 生命周期**: 截图缓存在内存中的图像嵌入占用多少 VRAM？多个截图同时持有 embedding 会不会 OOM？（考虑 LRU 淘汰策略。）

4. **`WorldToViewportPoint` + `cachedPose` 的准确性**: 理论上应当精确，但实际 OpenXR runtime 的实现可能有精度差异，需实测验证。

5. **截图传输延迟**: RGB + 深度 + 相机位姿的 multipart 传输延迟是多少？会不会影响交互节奏？可能需要 JPEG 压缩 RGB 纹理。
