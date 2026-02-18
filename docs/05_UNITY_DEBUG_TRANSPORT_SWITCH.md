# Unity 调试传输切换（USB 调试 / 无线运行）

## 目标

在同一套 Unity 工程中实现：

1. 编辑器调试时自动走有线 USB 调试链路。
2. 真机安装后的正常运行默认走无线局域网链路。

## 已添加脚本

- `unity/Quest3Client/Assets/Scripts/Networking/StreamTransportSwitcher.cs`

该脚本提供：

- `TransportMode.Auto`（默认）
- `TransportMode.WiredUsbDebug`
- `TransportMode.WirelessLan`

并在 `Auto` 模式下使用编译条件：

- `#if UNITY_EDITOR` -> `WiredUsbDebug`
- `#else` -> `WirelessLan`

## Unity 使用步骤

1. 在场景里创建一个空对象，例如 `NetworkBootstrap`。
2. 挂载 `StreamTransportSwitcher`。
3. 设置两个端点：
   - Wired（推荐）：`127.0.0.1:8000`
   - Wireless（示例）：`192.168.x.x:8000`（PC 局域网 IP）
4. 你的发送脚本通过 `BuildWebSocketUrl()` 获取连接地址。

## USB 调试（推荐先打通）

在 PC 上执行端口反向映射（ADB）：

```bash
adb reverse tcp:8000 tcp:8000
```

这样 Quest 端访问 `127.0.0.1:8000` 会映射到 PC 的 8000 端口。

## 无线运行（安装后）

1. Quest 3 连接到与电脑同一局域网。
2. `StreamTransportSwitcher` 使用 `WirelessLan` 端点（PC 的局域网 IP）。
3. 启动 PC 后端并监听对应端口。

## 切换建议

1. 开发调试阶段：保持 `Auto`，配合 `adb reverse`。
2. 现场运行阶段：保持 `Auto`（非 Editor 自动走 Wireless），或显式切到 `WirelessLan`。
3. 若网络环境变化，只改 `wirelessEndpoint.host` 即可。
