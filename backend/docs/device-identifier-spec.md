# Device Identifier — Spec

## Overview

Discover Client 的三个源（MQTT, mDNS, SSDP）吐出原始发现事件，但这些事件里没有"这是什么设备"。Device Identifier 层负责：

1. **标注** — 从每条事件中提取信号证据（topic 模式、服务类型、payload 结构等）
2. **去重** — 把来自不同源的信号关联到同一台物理设备
3. **分类** — 为每台设备维护一组竞争假设，贝叶斯更新，收敛到高置信度判断

输出：每个设备的身份（类型 + 型号 + 置信度），更新到 GUI。

---

## Core Abstractions

### 1. DeviceFingerprint（设备指纹）

描述一种已知设备类型的特征签名。按信号源组织：

```python
@dataclass
class DeviceFingerprint:
    device_type: str          # "Govee H5179", "TP-Link HS100", "Aqara WSDCGQ11"
    category: str             # "temp_sensor", "smart_plug", "light", "gateway"
    mqtt_topic_patterns: list[str]    # ["govee/+/state"]
    mqtt_payload_keys: set[str]       # {"temp", "humidity", "battery"}
    mdns_service_types: list[str]     # ["_matter._tcp.local."]
    mdns_txt_keys: set[str]           # {"SII", "SAI"}
    ssdp_usn_patterns: list[str]      # ["uuid:...+::upnp:rootdevice"]
    ssdp_server_patterns: list[str]   # ["Linux/... UPnP/..."]
    hostname_pattern: str | None      # "govee-h5179-*" — for dedup cross-source
```

指纹是可扩展的——每加入一种新设备，添加一条记录。

### 2. SignalEvidence（信号证据）

单条事件的标注结果，从原始 payload 中提取结构化证据：

```python
@dataclass
class SignalEvidence:
    source_id: str            # mqtt-1, mdns-1, ssdp-1 — which source produced this
    source_type: str          # "mqtt", "mdns", "ssdp"
    # Identification clues
    mqtt_topic: str | None
    mqtt_payload_keys: set[str] | None
    mdns_service_type: str | None
    mdns_txt_keys: set[str] | None
    ssdp_usn: str | None
    ssdp_server: str | None
    # Deduplication clues
    ip_address: str | None
    hostname: str | None
    mac_prefix: str | None
    # Timestamp
    timestamp: float
```

### 3. DeviceHypothesis（设备假设）

一台物理设备的竞争假设集合：

```python
@dataclass
class DeviceHypothesis:
    fingerprint: DeviceFingerprint
    probability: float       # P(设备类型 | 所有证据)
```

### 4. Device（设备追踪单元）

```python
@dataclass
class Device:
    device_id: str                    # 内部 ID（稳定，跨源不变）
    hypotheses: list[DeviceHypothesis]  # 按置信度降序
    total_evidence_count: int
    last_seen: float
    # 去重用的锚点
    ip_addresses: set[str]
    hostnames: set[str]
    mac_prefixes: set[str]
```

---

## Architecture

```
MQTT Event  ──→ MQTTSignalAnnotator ──→ SignalEvidence ──┐
mDNS Event  ──→ MDNSSignalAnnotator  ──→ SignalEvidence ──┤
SSDP Event  ──→ SSDPSignalAnnotator  ──→ SignalEvidence ──┤
                                                          │
                                                    ┌─────▼──────┐
                                                    │ Deduplicator│
                                                    │  (关联同设备) │
                                                    └─────┬──────┘
                                                          │
                                                  ┌───────▼────────┐
                                                  │ BayesUpdater   │
                                                  │  (更新置信度)    │
                                                  └───────┬────────┘
                                                          │
                                                  ┌───────▼────────┐
                                                  │ Device Registry │
                                                  │  (维护设备列表)   │
                                                  └────────────────┘
```

### 组件说明

| 组件 | 职责 |
|------|------|
| **MQTTSignalAnnotator** | 从 MQTT 事件提取 topic 模式、payload keys |
| **MDNSSignalAnnotator** | 从 mDNS 事件提取 service_type、TXT keys、hostname、IP |
| **SSDPSignalAnnotator** | 从 SSDP 事件提取 USN、Server 头 |
| **Deduplicator** | 按 IP/hostname/MAC 关联同一设备的信号 |
| **BayesUpdater** | 贝叶斯更新：P(类型|新证据) = P(新证据|类型) * P(类型) / P(新证据) |
| **DeviceRegistry** | 维护所有已知设备，按置信度排序输出 |

---

## Evidence → Device 流程

### Step 1: 标注

每条事件进来，对应的 Annotator 提取 `SignalEvidence`：

- MQTT: `topic="govee/abc123/state"` → topic 模式 `govee/+/state`, payload keys `{temp, humidity}`, hostname hint `abc123`
- mDNS: `service_type="_matter._tcp.local."`, hostname `govee-h5179-abc123.local.`, IP `192.168.5.2`, TXT keys `{SII, SAI}`
- SSDP: USN 模式, Server 头

### Step 2: 去重

Deduplicator 按以下优先级尝试关联：

1. **Hostname 匹配** — 最高优先级。MQTT topic 中的 `abc123` 匹配 mDNS hostname 中的 `abc123` → 同一设备
2. **IP 地址** — 同一 IP 的不同源信号 → 同一设备
3. **MAC 前缀** — mDNS TXT 记录或 Matter Fabric ID

去重成功后，合并到同一个 `Device` 的 `ip_addresses` / `hostnames` 集合。

去重失败（无匹配）→ 创建新 `Device`。后续新信号可能通过 IP/hostname 把这个单独设备重新关联上。

### Step 3: 贝叶斯更新

新证据到达某个 Device 时：

1. 加载所有已注册的 `DeviceFingerprint`
2. 对每个指纹计算 **似然** P(证据|指纹)：
   - MQTT topic 模式匹配 +1.0, 不匹配 -0.5
   - MQTT payload keys 匹配比例 × 0.5
   - mDNS service_type 匹配 +0.3, 不匹配 -0.2
   - SSDP USN 匹配 +0.3
   - 有但不匹配（比如有 MQTT 数据但指纹没定义 MQTT 特征）→ 似然 = 中性 0.0
3. 贝叶斯更新：`posterior = prior × likelihood`, 归一化
4. 新增一个 **通用兜底假设**（"未知设备", 固定低先验 0.02）—— 如果特定指纹都不匹配，兜底假设会慢慢上升

### Step 4: 输出

`DeviceRegistry` 维护设备列表，按最高假设置信度排序。对新到置信度 > 0.7 的设备 fire `device_identified` 事件，GUI 更新显示。

---

## Fingerprint Registry（初始条目）

```python
FINGERPRINTS = [
    DeviceFingerprint(
        device_type="Govee H5179",
        category="temp_sensor",
        mqtt_topic_patterns=["govee/+/state"],
        mqtt_payload_keys={"temp", "humidity", "battery"},
        mdns_service_types=["_matter._tcp.local."],
        mdns_txt_keys={"SII", "SAI"},
        ssdp_usn_patterns=[],
        ssdp_server_patterns=[],
        hostname_pattern="govee-*",
    ),
    DeviceFingerprint(
        device_type="TP-Link Smart Plug",
        category="smart_plug",
        mqtt_topic_patterns=["tasmota/+/STATE"],
        mqtt_payload_keys={"POWER", "ENERGY"},
        mdns_service_types=["_matter._tcp.local."],
        mdns_txt_keys={"CM"},
        ssdp_usn_patterns=["uuid:...+UPnP:TP-LINK..."],
        ssdp_server_patterns=["Linux/... UPnP/..."],
        hostname_pattern=None,
    ),
    # 未知设备兜底
    DeviceFingerprint(
        device_type="Unknown Device",
        category="unknown",
        mqtt_topic_patterns=[],
        mqtt_payload_keys=set(),
        mdns_service_types=[],
        mdns_txt_keys=set(),
        ssdp_usn_patterns=[],
        ssdp_server_patterns=[],
        hostname_pattern=None,
    ),
]
```

---

## Integration with DiscoverClient

```
DiscoverClient.subscribe(on_event)
              │
              ├──→ UI event_received (现有)
              │
              └──→ DeviceIdentifier.ingest(event)  ← 新增
                          │
                          ├──→ annotate → SignalEvidence
                          ├──→ deduplicate → Device
                          └──→ bayes_update → DeviceIdentity
                          └──→ fire device_identified / device_updated
```

---

## 边界情况

- **单源设备** — 只有 MQTT 信号，没有 mDNS/SSDP 辅证 → 只用 MQTT 指纹匹配，置信度偏低但足够
- **信号消失** — 设备下线后，假设保持不变。等重新上线后继续更新
- **冲突证据** — 同一设备同时匹配两个高似然指纹 → 两者概率竞争，需要更多证据区分（比如topic更深层匹配）
- **新设备类型** — 所有现有指纹都低似然，"未知设备"概率上升 → 触发 `unknown_device` 事件，提示用户添加新指纹
- **Matter 网关** — mDNS 扫到 `_matter._tcp.local.` 但可能是个 Hub 而非终端设备 → 通过 TXT 记录区分（Hub 有 `SAI` 子类型）

---

## 文件结构

```
backend/discover_client/
├── identification/
│   ├── __init__.py
│   ├── fingerprint.py      # DeviceFingerprint, FINGERPRINTS registry
│   ├── evidence.py         # SignalEvidence
│   ├── device.py           # Device, DeviceHypothesis
│   ├── annotators.py       # MQTTSignalAnnotator, MDNSSignalAnnotator, SSDPSignalAnnotator
│   ├── deduplicator.py     # Deduplicator
│   ├── bayes.py            # BayesUpdater
│   ├── registry.py         # DeviceRegistry
│   └── identifier.py       # DeviceIdentifier (orchestrator)
```

---

## 实现路径

1. `fingerprint.py` + `evidence.py` — 定义数据结构
2. `annotators.py` — 三个标注器，从 SourceEvent 转 SignalEvidence
3. `device.py` — Device + DeviceHypothesis
4. `deduplicator.py` — 按 IP/hostname 关联
5. `bayes.py` — 似然计算 + 贝叶斯更新
6. `registry.py` — 设备注册表
7. `identifier.py` — 串联全流程，暴露 `ingest(event)` 接口
8. 接入 MainWindow — 监听 `device_identified` 信号，显示设备列表

先写数据结构 + 标注器，Govee H5179 一个指纹能跑通全流程，再扩展。
