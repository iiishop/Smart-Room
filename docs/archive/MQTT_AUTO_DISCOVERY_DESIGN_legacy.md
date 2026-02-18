# MQTT自动发现与设备接管系统设计

## 1. 概述

构建一个自动发现本地MQTT设备的系统，用户无需手动配置，系统自动识别和管理所有局域网内的MQTT设备。

### 1.1 核心目标
- **自动发现**: 在笔记本的多个网络接口（有线、热点）上发现MQTT设备
- **自动连接**: 识别MQTT Broker并建立连接
- **设备接管**: 完全接管设备控制，替代官方应用
- **快速演示**: 实现最小可行产品（MVP），可立即演示

### 1.2 设计约束
- 不需要手动配置设备IP或Broker地址
- 支持多个网络接口同时运行
- MQTT Broker可在本地或远程，但设备必须在局域网内
- 系统能自动推断设备的控制能力

---

## 2. 系统架构

### 2.1 高层架构

```
┌─────────────────────────────────────┐
│    VR Application / Web UI          │
│    (WebSocket / REST API)           │
└──────────────┬──────────────────────┘
               │
┌──────────────▼──────────────────────┐
│   Smart Room Backend (Python)       │
│                                     │
│  ┌────────────────────────────────┐ │
│  │ MQTT Discovery Manager         │ │
│  │ ├─ Network Interface Monitor   │ │
│  │ ├─ MQTT Broker Detector       │ │
│  │ └─ Device Registry            │ │
│  └────────────────────────────────┘ │
│                                     │
│  ┌────────────────────────────────┐ │
│  │ MQTT Adapter                   │ │
│  │ ├─ Connection Pool            │ │
│  │ ├─ Topic Listener             │ │
│  │ └─ Capability Resolver        │ │
│  └────────────────────────────────┘ │
│                                     │
│  ┌────────────────────────────────┐ │
│  │ Device Manager                 │ │
│  │ ├─ Device Registry            │ │
│  │ ├─ Control Executor           │ │
│  │ └─ State Synchronizer         │ │
│  └────────────────────────────────┘ │
│                                     │
│  ┌────────────────────────────────┐ │
│  │ API Layer                      │ │
│  │ ├─ REST Endpoints             │ │
│  │ └─ WebSocket Broadcast        │ │
│  └────────────────────────────────┘ │
└─────────────────────────────────────┘
       ↓              ↓
   MQTT Broker   MQTT Devices
  (Local/Remote)
```

### 2.2 数据流

```
启动系统
   ↓
扫描本地网络接口 (eth0, wlan0, ...)
   ↓
对每个接口的子网进行扫描:
   ├─ mDNS 扫描 (_mqtt._tcp)
   └─ IP 范围扫描 (1883 端口)
   ↓
发现 MQTT Broker
   ↓
连接 Broker，订阅 #（所有主题）
   ↓
收集设备信息:
   ├─ 分析 Topic 结构
   ├─ 识别设备类型和功能
   └─ 注册设备到本地注册表
   ↓
向 VR/Web UI 推送设备列表
   ↓
用户选择设备→执行控制指令
   ↓
通过 MQTT 发布命令到设备
```

---

## 3. 核心模块设计

### 3.1 网络接口监听器 (Network Interface Monitor)

**职责**: 检测系统上的所有网络接口，监听接口变化

**输入**: 系统启动事件
**输出**: 活跃网络接口列表（IP、掩码、广播地址）

**算法**:
```
GetActiveInterfaces():
  interfaces = []
  for iface in os.listdir('/sys/class/net/'):
    if is_up(iface):
      interfaces.append(get_interface_info(iface))
  return interfaces
```

**关键数据**:
```python
@dataclass
class NetworkInterface:
    name: str              # eth0, wlan0, ...
    ip_address: str        # 192.168.1.100
    netmask: str          # 255.255.255.0
    broadcast: str        # 192.168.1.255
    is_up: bool
```

---

### 3.2 MQTT Broker 探测器 (MQTT Broker Detector)

**职责**: 在给定的网络接口上发现MQTT Broker

**输入**: NetworkInterface 列表
**输出**: 发现的MQTT Broker 列表

**方法1: mDNS 扫描** (快速，如果Broker配置了)
```
使用 zeroconf / avahi 库
搜索 _mqtt._tcp 服务
超时: 3-5 秒
```

**方法2: 端口扫描** (全面覆盖)
```
对 IP 范围进行 TCP 连接扫描，目标端口: 1883 (MQTT)
使用并发连接（asyncio），最多10个并发任务
超时: 1883 端口连接超时 2 秒
```

**方法3: 智能猜测** (快速优化)
```
首先尝试本地网关 (192.168.1.1, 192.168.0.1)
然后尝试 255 地址（广播） - 某些MQTT broker会响应
最后全范围扫描
```

**关键数据**:
```python
@dataclass
class MQTTBrokerInfo:
    host: str              # 192.168.1.100 or 192.168.1.255
    port: int              # 1883
    interface: str         # eth0
    discovery_method: str  # mdns, port_scan
    is_local: bool         # True if on same interface
```

---

### 3.3 MQTT 适配器 (MQTT Adapter)

**职责**: 连接MQTT Broker，接收消息，执行设备控制

**核心功能**:

#### 3.3.1 Broker 连接管理
```
为每个 Broker 创建一个连接池：
  - 自动重连机制
  - 订阅恢复
  - 连接状态监控
```

#### 3.3.2 Topic 监听和设备发现
```
订阅通配符主题: #
收集所有 Topic 消息:
  - 记录 Topic 名称和结构
  - 采样消息内容
  - 推断数据类型（bool, int, float, enum）

分析 Topic 层次结构，推断设备组织:
  示例:
    home/living-room/fan/power → 客厅风扇电源
    home/living-room/fan/speed → 客厅风扇速度
    home/bedroom/light/state  → 卧室灯状态

设备识别算法:
  1. 从 Topic 路径提取设备标识 (fan, light, ...)
  2. 聚合相关 Topic：同一设备的所有控制项
  3. 根据 Topic 名称和值推断能力类型
```

#### 3.3.3 设备能力推断 (Capability Inference)
```
根据收集的 Topic 和消息内容推断设备能力：

规则基础:
  - Topic 名包含 "power", "switch", "on_off" → 开关能力
  - Topic 名包含 "brightness", "level" → 亮度调节
  - Topic 值为 "on"/"off" → 布尔值
  - Topic 值为 0-100 → 范围值
  - Topic 值为 "high"/"medium"/"low" → 枚举值

推断输出:
  Device:
    id: "living-room-fan"
    name: "客厅风扇"
    capabilities:
      - action: "power"
        type: "bool"
        readable: true
        writable: true
      - action: "speed"
        type: "enum"
        values: [1, 2, 3]
        readable: true
        writable: true
```

**关键数据结构**:
```python
@dataclass
class MQTTDevice:
    device_id: str           # unique identifier
    name: str                # 显示名称
    type: str                # fan, light, sensor, etc.
    broker_host: str
    broker_port: int
    topics: Dict[str, str]   # action_name → topic_path
    capabilities: List[Capability]

@dataclass
class Capability:
    action: str              # "power", "speed", "brightness"
    type: str                # "bool", "int", "float", "enum"
    readable: bool
    writable: bool
    current_value: Any       # 当前值
    values: List[Any]        # 仅对 enum 和 range
```

---

### 3.4 设备管理器 (Device Manager)

**职责**: 维护已发现设备的生命周期

**功能**:
- 设备注册: 添加新发现的设备
- 设备更新: 定期同步设备状态
- 设备删除: 在线状态超时后移除
- 控制执行: 向MQTT设备发送命令

**关键方法**:
```
RegisterDevice(device: MQTTDevice) → device_id
UpdateDeviceState(device_id: str, capability: str, value: Any)
GetDevices() → List[MQTTDevice]
ControlDevice(device_id: str, action: str, value: Any)
```

---

### 3.5 API 层 (API Layer)

#### REST Endpoints:
```
GET /api/devices
  返回所有已发现的设备列表

GET /api/devices/{device_id}
  返回设备详细信息（包括所有能力）

POST /api/devices/{device_id}/control
  请求体: {"action": "power", "value": true}
  执行设备控制命令

GET /api/status
  返回系统状态（发现进度、Broker连接状态等）

POST /api/scan
  手动触发一次网络扫描
```

#### WebSocket Events:
```
device:discovered
  新设备被发现时广播

device:removed
  设备离线时广播

device:state_changed
  设备状态变化时广播
  {device_id, capability, old_value, new_value}

scan:progress
  扫描进度更新
```

---

## 4. 工作流程

### 4.1 初始化流程

```
┌─────────────────────┐
│   System Startup    │
└──────────┬──────────┘
           ↓
┌──────────────────────────────┐
│ 1. 获取活跃网络接口          │
│    (eth0, wlan0, ...)        │
└──────────┬───────────────────┘
           ↓
┌──────────────────────────────┐
│ 2. 对每个接口扫描MQTT Broker │
│    - mDNS (3s)               │
│    - 端口扫描 (20s)          │
└──────────┬───────────────────┘
           ↓
┌──────────────────────────────┐
│ 3. 连接所有发现的Broker      │
│    创建连接池                │
└──────────┬───────────────────┘
           ↓
┌──────────────────────────────┐
│ 4. 订阅 # (所有主题)         │
│    开始收集Topic和消息       │
└──────────┬───────────────────┘
           ↓
┌──────────────────────────────┐
│ 5. 后台分析收集的数据        │
│    推断设备能力              │
│    (持续运行，每5-10s更新)   │
└──────────┬───────────────────┘
           ↓
┌──────────────────────────────┐
│ 6. 定期广播设备列表到UI      │
│    WebSocket推送             │
└──────────────────────────────┘
```

### 4.2 用户控制流程

```
用户在 VR 中选择设备并执行操作
         ↓
WebSocket 消息到达后端: 
{
  "action": "control",
  "device_id": "living-room-fan",
  "capability": "power",
  "value": true
}
         ↓
设备管理器查询设备的 Topic 映射:
  "power" → "home/living-room/fan/power"
         ↓
MQTT 适配器发布消息:
  Topic: "home/living-room/fan/power"
  Payload: "on" (or "1", 根据设备期望的格式)
         ↓
MQTT 设备接收并执行命令
         ↓
设备发布状态更新:
  Topic: "home/living-room/fan/power"
  Payload: "on"
         ↓
后端接收状态更新
         ↓
通过 WebSocket 广播给 VR 应用:
{
  "event": "device:state_changed",
  "device_id": "living-room-fan",
  "capability": "power",
  "value": true
}
```

---

## 5. 实现细节

### 5.1 Topic 结构识别

**常见的 Topic 组织方式**:

```
方式1: home/{location}/{device}/{property}
  home/living-room/fan/power
  home/living-room/fan/speed

方式2: {device_id}/{property}
  fan-001/power
  fan-001/speed

方式3: {building}/{floor}/{room}/{device}/{property}
  home/1/living-room/fan/power

方式4: devices/{device_id}/state 或 .../command
  devices/fan-001/state
  devices/fan-001/command

策略: 使用启发式规则和频率分析来识别最可能的结构
```

### 5.2 设备去重

```
为每个 Topic 分配一个"设备签名":
  1. 提取共同前缀 (home/living-room/fan)
  2. 使用前缀作为设备 ID
  3. 余下部分作为能力名称

例:
  home/living-room/fan/power → device_id: home/living-room/fan, action: power
  home/living-room/fan/speed → device_id: home/living-room/fan, action: speed
```

### 5.3 消息格式推断

```
采样每个 Topic 的最后 N 条消息，推断数据类型:

值: "on", "off", "true", "false", "1", "0"
  → 布尔类型

值: "25", "75", "100"
  → 数值类型，范围推断为 [0-100]

值: "high", "medium", "low" 或 "1", "2", "3"
  → 枚举类型，值列表从样本推断

值: JSON 对象
  → 复杂数据，暂时忽略（MVP不处理）
```

### 5.4 发送命令时的格式转换

```
用户在 VR 中: 设置风扇速度 = 2

后端转换:
  Capability: speed (type: enum, values: [1,2,3])
  Value: 2
  
查询设备的 speed 能力学到的格式:
  如果采样值是 "1", "2", "3" → 发送 "2"
  如果采样值是 "low", "medium", "high" → 发送 "medium"
  如果采样值是 JSON → 构造相应的 JSON

发送到 MQTT:
  Topic: home/living-room/fan/speed
  Payload: "2"
```

---

## 6. 系统配置

### 6.1 扫描参数 (可配置)

```python
# 发现配置
MDNS_SCAN_TIMEOUT = 3  # 秒
PORT_SCAN_TIMEOUT = 1  # 秒/端口
PORT_SCAN_CONCURRENCY = 10  # 并发连接数
MQTT_CONNECT_TIMEOUT = 5  # 秒

# 设备管理配置
DEVICE_OFFLINE_TIMEOUT = 300  # 5分钟无消息视为离线
CAPABILITY_UPDATE_INTERVAL = 10  # 秒，重新分析能力
DEVICE_STATE_SYNC_INTERVAL = 5  # 秒，同步设备状态

# Topic 订阅配置
TOPIC_BUFFER_SIZE = 1000  # 保留最后N条消息
TOPIC_SAMPLE_SIZE = 10  # 推断能力时采样N条消息
```

---

## 7. MVP 范围

### 7.1 第一阶段实现（快速演示）

**必须有**:
- [ ] 网络接口扫描
- [ ] MQTT Broker 自动发现（mDNS + 端口扫描）
- [ ] 连接到发现的 Broker
- [ ] 订阅所有 Topic (#)
- [ ] Topic 和设备去重
- [ ] 能力推断（简单规则）
- [ ] 基础 REST API（获取设备列表）
- [ ] 基础控制（发送 MQTT 消息）
- [ ] Web UI 或 CLI 演示

**可以没有**:
- [ ] WebSocket（先用 REST polling）
- [ ] 复杂的能力推断规则
- [ ] 用户界面优化
- [ ] 错误处理和恢复
- [ ] 性能优化

---

## 8. 技术栈 (MVP)

- **语言**: Python 3.9+
- **框架**: FastAPI（Web 服务）
- **MQTT**: paho-mqtt（连接和消息）
- **网络**: socket（端口扫描）, zeroconf（mDNS）
- **异步**: asyncio（并发任务）
- **数据**: Pydantic（数据验证）
- **前端**: 简单的 HTML + JS 或直接用 curl 测试

---

## 9. 风险和缓解

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| MQTT Broker 不在标准端口 | 无法发现 | 扫描多个常用端口 (1883, 8883) |
| Topic 结构千奇百怪 | 能力推断失败 | 提供手动配置选项（非MVP） |
| 多个 Broker 在同一网络 | 设备分散 | 自动检测并连接所有 Broker |
| MQTT 消息量大 | 内存溢出 | 限制缓冲区大小，丢弃旧消息 |
| 网络延迟 | 扫描时间长 | 设置合理的超时时间和并发限制 |

---

## 10. 下一步扩展

- [ ] Home Assistant 协议支持
- [ ] Mi Home 集成
- [ ] BLE 本地扫描
- [ ] Matter 支持
- [ ] 持久化设备注册表
- [ ] 用户权限控制
- [ ] 高级能力推断规则
- [ ] 设备分组和场景

---

## 11. 文件结构

```
smart-room-backend/
├── README.md
├── requirements.txt
├── main.py                          # FastAPI 入口
├── config.py                        # 配置常量
│
├── core/
│   ├── __init__.py
│   ├── network.py                   # 网络接口监听
│   ├── discovery.py                 # MQTT Broker 发现
│   └── device_registry.py           # 设备注册表
│
├── mqtt_adapter/
│   ├── __init__.py
│   ├── broker_connector.py          # Broker 连接管理
│   ├── topic_listener.py            # Topic 监听和收集
│   ├── device_analyzer.py           # Topic 分析和设备识别
│   └── command_executor.py          # 执行控制命令
│
├── models/
│   ├── __init__.py
│   ├── device.py                    # 设备数据模型
│   └── capability.py                # 能力数据模型
│
├── api/
│   ├── __init__.py
│   ├── routes.py                    # REST 路由
│   └── websocket.py                 # WebSocket (future)
│
├── web/
│   └── index.html                   # 简单 Web UI
│
└── tests/
    ├── test_network.py
    ├── test_discovery.py
    └── test_mqtt.py
```

---

## 12. 演示脚本 (MVP)

```bash
# 1. 启动后端
python main.py

# 2. 扫描网络 (自动)
# GET http://localhost:8000/api/status

# 3. 查看发现的设备
# GET http://localhost:8000/api/devices

# 4. 控制设备 (示例：打开客厅风扇)
# POST http://localhost:8000/api/devices/fan-001/control
# Body: {"action": "power", "value": true}

# 5. 实时监控 (WebSocket, future)
# ws://localhost:8000/ws
```

---

## 总结

这个设计提供了一个**零配置、自动发现**的MQTT设备管理系统。系统能够：

1. ✅ 自动扫描本地网络和发现MQTT Broker
2. ✅ 自动识别MQTT设备和能力
3. ✅ 提供统一的API和WebSocket接口
4. ✅ 快速演示（MVP 可在3-5天内完成）

关键是从**最简单的情况开始**（单个Broker, 标准Topic结构），然后逐步扩展支持更复杂的场景。
