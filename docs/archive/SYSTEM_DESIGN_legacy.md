# Smart Room - 智能家居统一控制系统

## 项目概述

一个为VR应用（Meta Quest 3）服务的智能家居中央控制平台。系统自动发现和接管本地局域网内的IoT设备，提供零配置的统一控制接口。

### 核心目标
- **自动发现**: 无需手动配置，系统自动扫描并识别局域网内的IoT设备
- **统一控制**: 支持MQTT、Home Assistant、Mi Home等多种协议，提供统一的控制接口
- **VR集成**: 通过WebSocket提供实时推送，与Meta Quest 3中的Unity应用无缝集成
- **零配置**: 用户无需手动添加设备或配置参数，开箱即用

---

## 整体架构

```
┌────────────────────────────────────────────┐
│  VR Frontend (Meta Quest 3 / Unity)        │
│  WebSocket 客户端，实时接收设备信息和状态 │
└────────────────┬─────────────────────────┘
                 │
         ┌───────▼────────┐
         │ WebSocket API  │
         │ REST API       │
         └───────┬────────┘
                 │
┌────────────────▼────────────────────────────────┐
│      Python Backend (FastAPI)                   │
│      localhost:8000                             │
│                                                 │
│  ┌──────────────────────────────────────────┐  │
│  │ 1. Network Discovery Service             │  │
│  │    - 网络接口监听                        │  │
│  │    - mDNS/IP 扫描                        │  │
│  │    - 多协议 Broker 探测                  │  │
│  └──────────────────────────────────────────┘  │
│                                                 │
│  ┌──────────────────────────────────────────┐  │
│  │ 2. Protocol Adapters (协议适配层)        │  │
│  │    ├─ MQTT Adapter (优先级1)             │  │
│  │    ├─ Home Assistant Adapter (优先级2)   │  │
│  │    ├─ Mi Home Adapter (优先级3)          │  │
│  │    └─ Matter/Zigbee (未来)               │  │
│  └──────────────────────────────────────────┘  │
│                                                 │
│  ┌──────────────────────────────────────────┐  │
│  │ 3. Device Manager (设备管理)             │  │
│  │    - 设备注册表                          │  │
│  │    - 状态同步                            │  │
│  │    - 能力推断引擎                        │  │
│  └──────────────────────────────────────────┘  │
│                                                 │
│  ┌──────────────────────────────────────────┐  │
│  │ 4. API & WebSocket Layer                 │  │
│  │    - REST 端点                           │  │
│  │    - 实时推送                            │  │
│  └──────────────────────────────────────────┘  │
└─────┬──────────────┬──────────────┬────────────┘
      │              │              │
 ┌────▼────┐   ┌────▼────┐   ┌────▼─────┐
 │ MQTT网络 │   │ MQTT远程 │   │ 其他协议   │
 │设备      │   │ Broker   │   │ (HA/Mi)   │
 │(本地)    │   │ (云端)   │   │           │
 └─────────┘   └──────────┘   └───────────┘
```

---

## 功能模块

### 1. 网络发现模块 (Network Discovery)

**核心职责**: 扫描本地网络，发现MQTT Broker和设备

**关键点**:
- 监听系统所有网络接口 (eth0, wlan0, ...)
- mDNS扫描 (`_mqtt._tcp`) - 快速发现配置良好的Broker
- TCP端口扫描 (1883, 8883) - 发现非mDNS的Broker
- 支持多网络并行扫描 (有线 + 热点)

**输入**: 系统启动事件
**输出**: 发现的MQTT Broker列表 `{host, port, interface}`

---

### 2. MQTT适配器 (MQTT Adapter) - 当前优先

**核心职责**: 连接Broker，发现和管理MQTT设备

#### 2.1 连接管理
- 为每个发现的Broker创建连接
- 自动重连机制 (指数退避)
- 连接池管理

#### 2.2 设备自动发现
- 订阅通配符主题 (`#`)
- 收集所有Topic信息
- **设备识别**: 根据Topic层次结构识别设备
  ```
  例: home/living-room/fan/power → 设备 "living-room-fan"
  例: devices/sensor-001/temperature → 设备 "sensor-001"
  ```

#### 2.3 能力推断
- 分析消息内容推断数据类型 (bool/int/enum)
- 自动识别可读/可写能力
- 建立Topic与能力的映射

**输入**: MQTT消息流
**输出**: 设备列表 + 能力映射

**数据模型**:
```python
Device:
  - device_id: str
  - name: str (推断或用户设置)
  - type: str (fan, light, sensor, ...)
  - broker: {host, port}
  - capabilities: [
      {action, type, readable, writable, current_value}
    ]
```

---

### 3. 设备管理器 (Device Manager)

**核心职责**: 维护设备生命周期和状态

**功能**:
- 注册新发现的设备
- 定期同步设备状态
- 设备离线检测 (超时移除)
- 执行控制命令 (通过MQTT发布)

**关键操作**:
- `GetDevices()` → 返回所有设备
- `ControlDevice(device_id, action, value)` → 执行控制
- `UpdateDeviceState()` → 同步状态

---

### 4. API层 (API & WebSocket)

#### REST API Endpoints:
```
GET  /api/devices                    # 获取设备列表
GET  /api/devices/{device_id}        # 获取设备详情
POST /api/devices/{device_id}/control  # 控制设备
GET  /api/status                     # 系统状态
POST /api/scan                       # 手动扫描
```

#### WebSocket 事件:
```
device:discovered      # 新设备发现
device:removed        # 设备离线
device:state_changed  # 设备状态变化
scan:progress         # 扫描进度
```

---

## 实现阶段

### Phase 0: MVP - MQTT自动发现 (目标: 3-5天)
**目标**: 快速完成演示，验证核心功能

- [x] 项目框架 (FastAPI + Python)
- [ ] 网络接口扫描
- [ ] MQTT Broker 自动发现 (mDNS + 端口扫描)
- [ ] 连接Broker，订阅所有Topic
- [ ] 设备识别和去重
- [ ] 简单的能力推断
- [ ] REST API (设备列表 + 控制)
- [ ] Web UI 或 CLI 演示

**不需要**:
- WebSocket (用REST polling)
- 复杂能力推断
- 错误恢复
- 性能优化

---

### Phase 1: Home Assistant 支持 (后续)
- REST API集成
- 自动发现Home Assistant实例
- 统一设备管理

---

### Phase 2: Mi Home 集成 (后续)
- Mi Home API集成
- 蓝牙设备支持 (第二优先级)

---

### Phase 3: VR完全集成 (后续)
- WebSocket实时推送
- Unity客户端集成
- 手势控制交互

---

## 项目结构

```
smart-room/
├── docs/
│   ├── SYSTEM_DESIGN.md              # 本文件
│   └── API_SPEC.md                   # API文档
│
├── backend/
│   ├── main.py                       # FastAPI入口
│   ├── config.py                     # 配置常量
│   ├── requirements.txt
│   │
│   ├── core/
│   │   ├── network.py                # 网络接口管理
│   │   ├── discovery.py              # Broker发现
│   │   └── registry.py               # 设备注册表
│   │
│   ├── adapters/
│   │   ├── mqtt/
│   │   │   ├── __init__.py
│   │   │   ├── broker_connector.py   # Broker连接
│   │   │   ├── topic_listener.py     # Topic监听
│   │   │   ├── device_analyzer.py    # 设备分析
│   │   │   └── command_executor.py   # 命令执行
│   │   ├── homeassistant/ (future)
│   │   └── mi_home/ (future)
│   │
│   ├── models/
│   │   ├── device.py                 # 设备数据模型
│   │   └── capability.py             # 能力数据模型
│   │
│   ├── api/
│   │   ├── routes.py                 # REST路由
│   │   └── websocket.py              # WebSocket (future)
│   │
│   └── tests/
│       ├── test_discovery.py
│       └── test_mqtt.py
│
├── frontend/
│   ├── index.html                    # 简单Web UI
│   └── style.css
│
└── vr/
    └── unity/
        └── SmartRoom/ (future)
```

---

## 技术栈

| 组件 | 技术 | 说明 |
|------|------|------|
| 后端框架 | FastAPI | Python异步Web框架 |
| MQTT客户端 | paho-mqtt | 稳定的MQTT库 |
| 网络扫描 | zeroconf | mDNS发现 |
| 端口扫描 | socket/asyncio | TCP连接扫描 |
| 异步IO | asyncio | 并发任务处理 |
| 数据验证 | Pydantic | 数据模型和验证 |
| 前端 | HTML5 + JS | 简单Web UI (MVP) |
| VR | Unity | Meta Quest 3集成 (后期) |

---

## 关键设计决策

| 决策 | 原因 |
|------|------|
| 优先MQTT | 应用最广泛，协议简单，易于发现 |
| 自动发现优于配置 | 零配置，用户友好 |
| Topic结构识别 | 无需预知Topic，自适应不同设备 |
| Broker可远程 | 支持MQTT即服务，扩展灵活性 |
| WebSocket推送 | 实时同步，低延迟 |
| 分阶段实现 | 快速验证，逐步扩展 |

---

## MVP范围界定

### 必须实现:
- [x] 项目框架和API框架
- [ ] 网络接口扫描
- [ ] MQTT Broker 自动发现
- [ ] 设备识别和去重
- [ ] 简单能力推断 (bool/int/enum)
- [ ] REST API 控制
- [ ] 简单Web UI或CLI

### 不需要实现:
- WebSocket (暂用REST polling)
- Home Assistant/Mi Home
- BLE本地扫描
- 错误恢复和日志
- 数据库持久化
- 用户认证

---

## 下一步行动

1. **立即**: 完成MVP框架和MQTT发现演示 (3-5天)
2. **展示**: 运行演示，验证自动发现功能
3. **反馈**: 根据演示结果调整设计
4. **扩展**: 逐步添加其他协议支持

---

## 风险管理

| 风险 | 缓解方案 |
|------|---------|
| Broker在非标准端口 | 扫描多个常用端口 |
| Topic结构不规则 | 使用启发式规则 + 频率分析 |
| 网络扫描耗时长 | 并发扫描 + 设置合理超时 |
| MQTT消息量大 | 限制缓冲区大小 |
| 多个Broker冲突 | 自动检测并连接所有 |

---

## 成功标准

MVP成功的标准:
- ✅ 能自动发现本地MQTT Broker (5秒内)
- ✅ 能发现接入其中的所有MQTT设备
- ✅ 能推断设备的控制能力
- ✅ 能通过REST API控制设备
- ✅ 有简单的Web UI可以看到和操作设备
- ✅ 可以作为演示材料展示
