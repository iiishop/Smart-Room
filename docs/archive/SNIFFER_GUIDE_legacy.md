# MQTT 设备嗅探器测试指南

## 功能说明

这个工具可以**被动监听**网络上的 MQTT 流量，自动发现 IoT 设备，无需知道 Broker 地址！

## 它能做什么？

✅ **自动发现设备**：监听网络流量，找到所有 MQTT 设备
✅ **提取设备信息**：
  - 设备 IP 地址
  - Broker 地址和端口
  - Client ID
  - 用户名（如果有）
  - MQTT 协议版本

✅ **分析通信内容**：
  - 设备订阅/发布的 Topics
  - 实时消息内容
  - QoS 级别

✅ **完全被动**：不发送任何数据包，不干扰设备运行

## 前置条件

### Windows

1. **以管理员身份运行**
   - 右键点击终端 → "以管理员身份运行"
   
2. **安装 Npcap**（Scapy 在 Windows 上需要）
   - 下载：https://npcap.com/#download
   - 安装时勾选 "WinPcap API-compatible mode"

### Linux/Mac

```bash
# 使用 sudo 运行
sudo python backend/test_sniffer.py
```

## 快速开始

### 1. 安装依赖

```bash
cd backend
uv pip install scapy
```

### 2. 运行嗅探器

```bash
# Windows（以管理员身份）
cd backend
uv run python test_sniffer.py

# Linux/Mac
cd backend
sudo uv run python test_sniffer.py
```

### 3. 触发设备流量

让你的 IoT 设备发送一些数据：
- 打开设备电源
- 触发传感器（移动、按按钮等）
- 等待设备自动上报数据

### 4. 查看结果

程序会实时显示：

```
🔍 开始监听网络 MQTT 流量...
   监听端口: 1883, 8883, 1884

🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉
发现新的 MQTT 设备！
  设备 IP: 192.168.1.100
  Broker IP: mqtt.example.com:1883
  Client ID: ESP32-Sensor-001
  Username: iot_user
🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉

📨 MQTT PUBLISH: 192.168.1.100 [outgoing] topic=sensor/temperature message={"value": 25.3, "unit": "C"}

📊 统计信息:
   发现 1 个设备
   连接到 1 个 Broker
   捕获 1 个连接

   📱 设备: 192.168.1.100
      Client ID: ESP32-Sensor-001
      Broker: mqtt.example.com:1883
      数据包数: 15
      Topics (3): sensor/temperature, sensor/humidity, sensor/status
```

## 输出信息说明

### 设备信息

- **Device IP**: 设备的 IP 地址（你的 IoT 设备）
- **Broker IP**: MQTT Broker 的地址（可能是云服务器）
- **Client ID**: MQTT 客户端标识符
- **Username**: MQTT 认证用户名
- **Protocol Version**: MQTT 协议版本（3=3.1, 4=3.1.1, 5=5.0）

### 消息类型

- **🔌 CONNECT**: 设备连接到 Broker
- **📨 PUBLISH**: 发布消息到 Topic
- **📢 SUBSCRIBE**: 订阅 Topic

### Topics

设备通信的主题，通常能看出设备功能：
- `sensor/temperature` → 温度传感器
- `home/living-room/light` → 客厅灯
- `device/123/power` → 设备电源控制

## 故障排除

### 问题：没有发现任何设备

**可能原因 1: 权限不足**
```bash
# Windows: 确保以管理员身份运行
# Linux/Mac: 使用 sudo
sudo python test_sniffer.py
```

**可能原因 2: 需要安装 Npcap（Windows）**
- 下载：https://npcap.com/#download
- 安装并重启

**可能原因 3: 设备未发送数据**
- 确保设备已开机并连接到同一网络
- 触发设备发送数据（按按钮、移动传感器等）
- 检查设备是否真的使用 MQTT（可能使用其他协议）

**可能原因 4: 使用了加密连接（MQTTS）**
- 如果设备使用端口 8883（MQTT over TLS），数据会被加密
- 嗅探器仍能看到连接，但无法解析消息内容
- 可以看到设备 IP 和 Broker IP，但看不到 Topic 和消息

### 问题：看到很多错误信息

这是正常的！网络上有很多非 MQTT 流量，解析器会忽略它们。只要能看到 MQTT 消息就说明工作正常。

### 问题：只看到部分信息

- **只看到 IP，没有 Client ID**: 设备还没发送 CONNECT 包，等待更长时间
- **没有消息内容**: 可能使用了 SSL/TLS 加密
- **Topics 不完整**: 只捕获到部分流量，继续等待

## 安全说明

⚠️ **这个工具仅用于分析你自己的设备！**

- 不要用于监听他人设备
- 仅在你有权限的网络上使用
- 用于学习、调试、研究目的

## 下一步

发现设备后，你可以：

1. **记录 Broker 地址**：用于后续连接
2. **分析 Topic 结构**：了解设备通信协议
3. **查看消息格式**：知道数据结构（JSON, 纯文本等）
4. **编写控制程序**：连接到同一个 Broker 控制设备

## 技术细节

### 工作原理

```
1. Scapy 捕获网络数据包
   ↓
2. 过滤 TCP 端口 1883/8883/1884
   ↓
3. 提取 TCP payload
   ↓
4. 解析 MQTT 协议（Fixed Header + Variable Header + Payload）
   ↓
5. 提取信息（Client ID, Topic, Message）
   ↓
6. 显示给用户
```

### 支持的 MQTT 版本

- MQTT 3.1
- MQTT 3.1.1
- MQTT 5.0

### 监听端口

- **1883**: MQTT (non-SSL)
- **8883**: MQTTS (SSL/TLS) - 只能看到连接，无法解析内容
- **1884**: MQTT (alternative port)

### 限制

- ❌ 无法解密 SSL/TLS 加密的流量
- ❌ 只能监听本地网络（不能监听 VPN 或远程网络）
- ⚠️ 需要管理员/root 权限
- ⚠️ Windows 需要 Npcap

## 常见问题

**Q: 会不会干扰设备正常工作？**
A: 不会！这是完全被动的监听，不发送任何数据包。

**Q: 能看到加密的 MQTT 消息吗？**
A: 不能。如果设备使用 MQTTS（端口 8883），消息内容会被加密。但仍能看到设备 IP 和 Broker IP。

**Q: 能控制设备吗？**
A: 这个工具只监听，不发送。但你可以用发现的信息（Broker 地址、Topics）编写控制程序。

**Q: 为什么看不到所有消息？**
A: 可能因为：
  1. 刚启动，错过了之前的消息
  2. 设备发送频率低，等待更长时间
  3. 使用了加密连接

**Q: 能在生产环境使用吗？**
A: 不推荐。这是调试工具，性能和稳定性不适合生产环境。建议用于开发和调试。
