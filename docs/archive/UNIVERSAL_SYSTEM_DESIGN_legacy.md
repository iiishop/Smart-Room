# Smart Room - 通用 IoT 设备管理系统

## 系统概述

Smart Room 是一个通用的 IoT 设备发现和管理平台，能够自动发现本地网络上的各种智能设备，并提供统一的控制界面。

## 核心设计理念

### 1. 多协议支持
系统采用**插件化架构**，每个协议通过独立的适配器实现：
- MQTT 设备
- HomeKit 设备（Apple）
- 米家设备（小米）
- Tuya 设备（涂鸦）
- UPnP/DLNA 设备
- Zigbee 设备（通过网关）
- HTTP/REST API 设备
- 自定义设备

### 2. 自动发现机制
使用多种发现协议：
- **mDNS/Bonjour**: 发现网络上广播服务的设备
- **SSDP/UPnP**: 发现 UPnP 兼容设备
- **Network Scan**: ARP 扫描 + 端口扫描
- **Protocol-Specific**: 各协议特有的发现机制

### 3. 统一数据模型
所有设备都抽象为统一的数据模型：
```
Device {
  - id: 唯一标识符
  - name: 显示名称
  - type: 设备类型 (light, switch, sensor, etc.)
  - protocol: 使用的协议 (mqtt, homekit, mijia, etc.)
  - capabilities: 设备能力列表
  - state: 当前状态
  - connection_info: 连接信息
}
```

## 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                        Frontend (Web UI)                     │
│                  统一设备控制界面                             │
└─────────────────────────────────────────────────────────────┘
                              ↓ HTTP/WebSocket
┌─────────────────────────────────────────────────────────────┐
│                      FastAPI Backend                         │
│                                                               │
│  ┌──────────────────────────────────────────────────────┐  │
│  │           Device Manager (核心协调器)                │  │
│  │  - 协调所有适配器                                    │  │
│  │  - 维护设备注册表                                    │  │
│  │  - 处理设备控制请求                                  │  │
│  └──────────────────────────────────────────────────────┘  │
│                              ↓                                │
│  ┌──────────────────────────────────────────────────────┐  │
│  │           Discovery Manager (发现管理器)             │  │
│  │  - mDNS/Bonjour Scanner                              │  │
│  │  - SSDP/UPnP Scanner                                 │  │
│  │  - Network Scanner (ARP + Port)                      │  │
│  └──────────────────────────────────────────────────────┘  │
│                              ↓                                │
│  ┌──────────────────────────────────────────────────────┐  │
│  │         Protocol Adapters (协议适配器)               │  │
│  │                                                        │  │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐ │  │
│  │  │  MQTT    │ │ HomeKit  │ │  Mijia   │ │  Tuya  │ │  │
│  │  │ Adapter  │ │ Adapter  │ │ Adapter  │ │ Adapter│ │  │
│  │  └──────────┘ └──────────┘ └──────────┘ └────────┘ │  │
│  │                                                        │  │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐ │  │
│  │  │  UPnP    │ │  Zigbee  │ │   HTTP   │ │ Custom │ │  │
│  │  │ Adapter  │ │ Adapter  │ │ Adapter  │ │ Adapter│ │  │
│  │  └──────────┘ └──────────┘ └──────────┘ └────────┘ │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│                    Physical IoT Devices                      │
│  💡 智能灯泡  🌡️ 温度传感器  🔌 智能插座  📹 摄像头  ...    │
└─────────────────────────────────────────────────────────────┘
```

## 核心模块

### 1. Protocol Adapter Interface (协议适配器接口)

所有协议适配器必须实现的抽象接口：

```python
class ProtocolAdapter(ABC):
    @abstractmethod
    async def discover_devices(self) -> List[Device]:
        """发现设备"""
        pass
    
    @abstractmethod
    async def connect_device(self, device: Device) -> bool:
        """连接设备"""
        pass
    
    @abstractmethod
    async def control_device(self, device: Device, action: str, value: Any) -> bool:
        """控制设备"""
        pass
    
    @abstractmethod
    async def get_device_state(self, device: Device) -> Dict:
        """获取设备状态"""
        pass
    
    @abstractmethod
    def get_protocol_name(self) -> str:
        """返回协议名称"""
        pass
```

### 2. Discovery Manager (发现管理器)

负责运行各种发现协议：

```python
class DiscoveryManager:
    def __init__(self):
        self.scanners = [
            MDNSScanner(),      # mDNS/Bonjour
            SSDPScanner(),      # UPnP/SSDP
            NetworkScanner(),   # ARP + Port Scan
        ]
    
    async def discover_all(self) -> List[DeviceInfo]:
        """运行所有扫描器"""
        pass
```

### 3. Device Manager (设备管理器)

协调所有适配器，管理设备生命周期：

```python
class DeviceManager:
    def __init__(self):
        self.adapters: List[ProtocolAdapter] = []
        self.devices: Dict[str, Device] = {}
    
    def register_adapter(self, adapter: ProtocolAdapter):
        """注册协议适配器"""
        pass
    
    async def discover_devices(self):
        """通过所有适配器发现设备"""
        pass
    
    async def control_device(self, device_id: str, action: str, value: Any):
        """控制设备"""
        pass
```

## 实现计划

### Phase 1: 核心框架 (MVP)
✅ 统一数据模型
✅ 协议适配器接口
✅ 设备注册表
✅ REST API
✅ 基础 Web UI

### Phase 2: 发现机制
- mDNS/Bonjour 扫描器
- SSDP/UPnP 扫描器
- 网络扫描器（ARP + Port）

### Phase 3: 基础协议适配器
- MQTT 适配器（支持自动发现 broker）
- HTTP/REST API 适配器
- UPnP 适配器

### Phase 4: 智能家居适配器
- HomeKit 适配器
- 米家适配器
- Tuya 适配器

### Phase 5: 高级功能
- 场景自动化
- 设备分组
- 数据持久化
- 历史记录
- 告警通知

## 关键技术

### 发现协议

#### 1. mDNS/Bonjour
```python
from zeroconf import ServiceBrowser, Zeroconf

# 监听常见服务类型
SERVICE_TYPES = [
    "_http._tcp.local.",       # HTTP 设备
    "_homekit._tcp.local.",    # HomeKit 设备
    "_hap._tcp.local.",        # HomeKit Accessory Protocol
    "_mqtt._tcp.local.",       # MQTT broker
    "_miio._tcp.local.",       # 米家设备
    "_iot._tcp.local.",        # 通用 IoT
]
```

#### 2. SSDP (Simple Service Discovery Protocol)
```python
# 发送 M-SEARCH 多播
SSDP_MULTICAST = "239.255.255.250"
SSDP_PORT = 1900
```

#### 3. 网络扫描
```python
# ARP 扫描找到所有设备
# 端口扫描识别服务
COMMON_IOT_PORTS = [
    80,    # HTTP
    443,   # HTTPS
    1883,  # MQTT
    8883,  # MQTT SSL
    8080,  # HTTP Alt
    51827, # HomeKit
]
```

## 数据流

### 设备发现流程
```
1. 启动发现管理器
   ↓
2. 并行运行所有扫描器
   - mDNS Scanner → 发现广播设备
   - SSDP Scanner → 发现 UPnP 设备
   - Network Scanner → 发现所有在线设备
   ↓
3. 收集发现的设备信息（IP, 端口, 服务类型）
   ↓
4. 将设备信息分发给对应的协议适配器
   ↓
5. 协议适配器尝试连接和识别设备
   ↓
6. 注册到设备管理器
   ↓
7. 在 Web UI 显示
```

### 设备控制流程
```
1. 用户在 Web UI 点击控制按钮
   ↓
2. 前端发送 HTTP 请求到 API
   POST /api/devices/{id}/control
   ↓
3. Device Manager 查找设备
   ↓
4. 找到对应的协议适配器
   ↓
5. 适配器执行协议特定的控制命令
   - MQTT: 发布 topic
   - HomeKit: 调用 HAP 接口
   - HTTP: 发送 REST 请求
   ↓
6. 返回结果给前端
   ↓
7. 更新 UI 状态
```

## 配置

### 基础配置
```yaml
# config.yaml
discovery:
  mdns_enabled: true
  ssdp_enabled: true
  network_scan_enabled: true
  scan_interval: 60  # 扫描间隔（秒）

protocols:
  mqtt:
    enabled: true
    auto_discover_broker: true
  
  homekit:
    enabled: true
    pairing_code: null  # 配对码
  
  mijia:
    enabled: true
    tokens: {}  # 设备 token
  
  tuya:
    enabled: false
    api_key: null
    api_secret: null

network:
  interface: null  # null = 所有接口
  local_only: true  # 只显示本地网络设备
```

## API 接口

### 设备管理
```
GET    /api/devices              # 获取所有设备
GET    /api/devices/{id}         # 获取单个设备
POST   /api/devices/{id}/control # 控制设备
POST   /api/devices/scan         # 手动触发扫描
DELETE /api/devices/{id}         # 删除设备
```

### 协议管理
```
GET  /api/protocols           # 获取所有支持的协议
GET  /api/protocols/{name}    # 获取协议详情
POST /api/protocols/{name}/config  # 配置协议
```

### 发现管理
```
POST /api/discovery/start     # 开始发现
POST /api/discovery/stop      # 停止发现
GET  /api/discovery/status    # 获取发现状态
```

## 扩展性

### 添加新协议适配器

1. 创建新的适配器类，继承 `ProtocolAdapter`
2. 实现所有抽象方法
3. 在 `device_manager.py` 中注册适配器
4. 可选：添加协议特定的发现机制

```python
class MyCustomAdapter(ProtocolAdapter):
    def get_protocol_name(self) -> str:
        return "my_custom_protocol"
    
    async def discover_devices(self) -> List[Device]:
        # 实现发现逻辑
        pass
    
    # ... 实现其他方法
```

## 参考资料

- [Home Assistant Architecture](https://developers.home-assistant.io/docs/architecture_index/)
- [mDNS/Bonjour](https://en.wikipedia.org/wiki/Zero-configuration_networking)
- [UPnP Device Architecture](http://upnp.org/specs/arch/UPnP-arch-DeviceArchitecture-v2.0.pdf)
- [MQTT Protocol](https://mqtt.org/)
- [HomeKit Accessory Protocol](https://developer.apple.com/homekit/)
- [米家设备协议](https://github.com/rytilahti/python-miio)
