# Quest 3 -> Python Dashboard 技术方案报告（前期调研）

## 1. 目标与范围

当前阶段目标是验证一条可落地链路：

1. Quest 3 Unity 端获取 RGB 与深度相关数据。
2. 通过网络传给 PC 端 Python 后端（FastAPI）。
3. 后续接入 PySide6 + QML Dashboard。

本报告不包含具体业务代码，只给出可行组件、路径、步骤和风险控制。

## 2. 任务拆解

### 2.1 Unity + Quest 3 SDK 与教程调研

#### 2.1.1 环境初始化（官方路径）

- Unity OpenXR Meta 项目配置：
  - https://docs.unity3d.com/Packages/com.unity.xr.meta-openxr@1.0/manual/project-setup.html
- Unity OpenXR Meta 设备配置：
  - https://docs.unity3d.com/Packages/com.unity.xr.meta-openxr@1.0/manual/device-setup.html
- Meta Unity 文档总入口：
  - https://developers.meta.com/horizon/documentation/unity/

#### 2.1.2 关键 SDK/功能文档

- Camera (Passthrough)（OpenXR Meta）：
  - https://docs.unity3d.com/Packages/com.unity.xr.meta-openxr@2.2/manual/features/camera.html
- Depth API（Meta Unity）：
  - https://developers.meta.com/horizon/documentation/unity/unity-depthapi-overview/
- Unity Depth API 示例：
  - https://github.com/oculus-samples/Unity-DepthAPI
- 空间权限（Spatial Data Permission）：
  - https://developers.meta.com/horizon/documentation/unity/unity-spatial-data-perm/

### 2.2 SDK 功能需求澄清

#### A) RGB 回传

- 方案入口：Meta OpenXR Camera/Passthrough 与 AR Foundation Camera 子系统。
- 注意：不同 Unity/Meta OpenXR 版本对 CPU 图像访问能力、权限要求、编辑器调试能力不同。
- 建议把“RGB 原始帧可否稳定取到”作为 P0 可行性验证项（见第 5 节）。

#### B) 深度回传与点云

- 方案入口：Meta Depth API / OpenXR Meta Depth 能力。
- 深度数据用于点云需满足：
  1) 拿到深度图（每像素深度值）
  2) 拿到相机内参（fx/fy/cx/cy）
  3) 使用反投影生成 3D 点（可附带 RGB）
- 工程上常见做法：先回传降采样深度图 + 相机参数，再在 PC 端做点云重建和可视化。

## 3. 后端与 Dashboard 技术栈建议

## 3.1 通讯与服务层

- FastAPI（HTTP + WebSocket）：
  - Context7: `/websites/fastapi_tiangolo`
  - 可直接支持二进制消息 `receive_bytes()` / `send_bytes()`，适合帧流。

## 3.2 Dashboard（QML）

- 首选：PySide6（Qt for Python 官方绑定，QML 生态完整）
  - Context7: `/websites/doc_qt_io_qtforpython-6`
- 统一采用：PySide6（Qt for Python 官方绑定，QML 生态完整）

QML 应用启动参考（Context7）：
- `QGuiApplication + QQmlApplicationEngine + loadFromModule(...)`

## 3.3 点云处理与显示

- Open3D（Python 点云处理/显示）
  - Context7: `/isl-org/open3d`
  - 支持由 RGBD 转点云（`create_from_rgbd_image`）

## 3.4 推荐组件清单（最小可用）

1. Unity 端：OpenXR Meta + AR Foundation（RGB 路线） + Depth API（深度路线）
2. Python 后端：FastAPI + WebSocket
3. Dashboard：PySide6 + QML
4. 点云：Open3D
5. 协议：二进制帧包 + JSON 元数据

## 4. 实现路径（按阶段）

### Phase 0 - 环境与权限确认

1. Quest 3 开发者模式、ADB、Unity Android 构建链就绪。
2. Unity 中启用 OpenXR + Meta Quest Feature Group。
3. 完成 Space Setup 与必要权限（Spatial Data/Camera）。

交付物：
- 设备可部署运行；基础场景可进头显。

### Phase 1 - 通讯打通（不含图像）

1. Unity -> FastAPI WebSocket 建连。
2. 心跳包（设备 ID、时间戳、帧计数）。

交付物：
- 30 秒稳定心跳；断线重连可用。

### Phase 2 - RGB 数据回传

1. 采集 RGB 帧（先低分辨率）。
2. 编码（JPEG）+ 元数据打包。
3. FastAPI 接收并落盘抽样帧。

交付物：
- 后端可连续收到 RGB 帧并可回放验证。

### Phase 3 - 深度数据回传

1. 获取深度图和相机参数。
2. 低频率/降采样回传（先保证稳定）。
3. 后端验证深度格式和时序。

交付物：
- 后端可解析深度帧并记录统计。

### Phase 4 - 点云与 Dashboard 联调

1. 用 Open3D 在 PC 端从 RGBD 生成点云。
2. Dashboard 显示状态、帧率、延迟、丢包、点云开关。
3. 跑 5 分钟稳定性测试。

交付物：
- MVP 演示链路：Quest 数据 -> FastAPI -> Dashboard/点云。

## 5. 关键技术风险与验证策略

### 风险 R1：RGB 原始帧访问能力与版本差异

- 现象：不同 Unity/Meta OpenXR 版本在 CPU Camera Image 可用性、权限流程、Link 调试能力上差异明显。
- 策略：先做 1 天 Spike，输出“当前版本矩阵是否可取到可回传 RGB 帧”。

### 风险 R2：深度数据格式不稳定/成本高

- 现象：深度更偏 MR 能力，直接导出可回传格式可能需额外转换。
- 策略：先回传低分辨率与低频深度，确认格式再优化吞吐。

### 风险 R3：实时吞吐压力

- 策略：先做分层采样（例如 RGB 10-15 FPS，Depth 3-5 FPS），再逐步提高。

## 6. 数据协议建议（草案）

统一信封：

```json
{
  "type": "heartbeat|rgb|depth|calib",
  "device_id": "quest3-001",
  "frame_id": 12345,
  "timestamp_ms": 1739580000000,
  "width": 640,
  "height": 360,
  "encoding": "jpeg|depth16|json",
  "payload": "..."
}
```

说明：
- `rgb` 可先用 JPEG 字节。
- `depth` 建议先传 `uint16`（或压缩后）+ `scale`。
- `calib` 单独通道发送相机内参，用于点云重建。

## 7. 近期执行清单（你现在就可以做）

1. 固定技术基线版本（Unity、OpenXR Meta、AR Foundation、Quest OS）。
2. 做 P0 Spike：验证 RGB CPU 帧是否可读可传。
3. FastAPI 先做 WebSocket 接收服务（仅收心跳与测试包）。
4. 再加深度链路，最后接入 Dashboard。

## 8. Context7 参考（本次）

- FastAPI（WebSocket 接口/二进制消息）: `/websites/fastapi_tiangolo`
- Qt for Python（QML 引擎启动）: `/websites/doc_qt_io_qtforpython-6`
- Open3D（RGBD -> 点云）: `/isl-org/open3d`
