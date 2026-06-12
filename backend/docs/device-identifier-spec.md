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
    nmap_mac_prefixes: list[str]      # ["AA:BB:CC"] — OUI prefix match
    nmap_os_guesses: list[str]        # ["Linux*embedded*", "Espressif*"] — wildcard match
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
MQTT Event  ──→ MqttAnnotator         ──→ SignalEvidence ──┐
mDNS Event  ──→ MdnsAnnotator         ──→ SignalEvidence ──┤
SSDP Event  ──→ SsdpAnnotator         ──→ SignalEvidence ──┤
nmap Event  ──→ NmapAnnotator (未来)   ──→ SignalEvidence ──┤
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
| **Annotator 插件** | 每个 Source 类型对应一个 Annotator 实现，通过注册表发现。负责从该源的原始事件中提取结构化 `SignalEvidence`。新增源时只需添加一个新的 Annotator 类，核心管线不动 |
| **AnnotatorRegistry** | 维护 `source_type → Annotator` 的映射。`Annotator` 是抽象基类，插件通过 `@register_annotator("source_type")` 注册 |
| **Deduplicator** | 按 IP/hostname/MAC 关联同一设备的信号 |
| **BayesUpdater** | 贝叶斯更新：P(类型\|新证据) = P(新证据\|类型) × P(类型) / P(新证据) |
| **DeviceRegistry** | 维护所有已知设备，按置信度排序输出 |

## Annotator 扩展机制

跟 Source 注册表同样的插件模式：

```python
# 抽象基类
class Annotator(ABC):
    source_type: str  # "mqtt", "mdns", "ssdp", "nmap", ...

    @abstractmethod
    def annotate(self, event: SourceEvent) -> SignalEvidence | None: ...


# 注册表
ANNOTATORS: dict[str, type[Annotator]] = {}

def register_annotator(source_type: str):
    """Decorator: register an Annotator subclass."""
    def wrapper(cls):
        ANNOTATORS[source_type] = cls
        cls.source_type = source_type
        return cls
    return wrapper


# 示例：MQTT 标注器
@register_annotator("mqtt")
class MqttAnnotator(Annotator):
    def annotate(self, event: SourceEvent) -> SignalEvidence | None:
        topic = event.payload.get("topic", "")
        return SignalEvidence(
            source_id=event.source_id,
            source_type="mqtt",
            mqtt_topic=topic,
            mqtt_payload_keys=set(event.payload.get("value", {}).keys()),
            hostname=self._extract_hostname_hint(topic),
            timestamp=event.timestamp,
        )
```

关键约束：
- 每个 Annotator **只知道自己源的信号格式**，不耦合其他源
- 新增数据源（如 nmap）→ 只需实现 `NmapAnnotator(Annotator)` + 一行注册，去重器/贝叶斯更新器零改动
- `SignalEvidence` 是统一中间格式——所有 Annotator 产出同一种证据结构，下游管线完全源无关

---

## Source 扩展：nmap

除现有 MQTT/mDNS/SSDP 三个源外，新增第四个源类型 `nmap`——周期性地对局域网执行主机发现扫描，吐出设备级信息（IP、MAC、hostname、OS guess）。

### 自动安装检测

nmap 是外部依赖（非 Python 包），Source 启动时先检查安装状态：

1. 执行 `nmap --version`（Windows）或 `which nmap`（Unix）
2. 如果未安装：
   - **Windows**: `winget install Insecure.Nmap`（为开源安全扫描工具）
   - **macOS**: `brew install nmap`
   - **Linux**: `apt-get install -y nmap` / `dnf install -y nmap`
3. 安装完成后重新检测版本号，确认可用
4. 如果安装失败（权限不足、网络不通等），Source 标记为 `unavailable`，emit error 事件，不阻塞其他源

### 配置

```toml
[[sources]]
source_id = "nmap-1"
source_type = "nmap"
enabled = true

[sources.settings]
scan_interval_s = 300              # 每 5 分钟扫一次
target_subnet = "192.168.1.0/24"   # 扫描目标（可选，留空则自动检测本机子网）
scan_flags = "-sn -PR"             # ping scan + ARP，不扫端口（快速发现）
```

**字段说明**：

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `scan_interval_s` | 300 | 扫描间隔，最小 30s（nmap 本身耗时通常 5-15s，太短没意义） |
| `target_subnet` | `""`（自动检测） | CIDR 格式。留空则通过本机 IP + 子网掩码自动推断 |
| `scan_flags` | `"-sn -PR"` | nmap 命令行参数。默认 ping scan + ARP（快速、不需要 root/管理员权限、不触发 IDS） |

### 输出事件

nmap Source emit 的事件类型：

| event_type | 触发条件 | payload 内容 |
|-----------|---------|-------------|
| `"status"` | 安装检查通过 | `{"msg": "nmap 7.98 ready", "version": "7.98"}` |
| `"scan_start"` | 每次扫描开始 | `{"subnet": "192.168.1.0/24", "flags": "-sn -PR"}` |
| `"discovery"` | 每个发现的 host | `{"ip": "192.168.5.2", "mac": "AA:BB:CC:DD:EE:FF", "vendor": "Espressif", "hostnames": ["govee-h5179-abc123.local."], "status": "up", "os_guess": "Linux 4.x (embedded)"}` |
| `"scan_end"` | 扫描完成 | `{"total_hosts": 12, "up_hosts": 5, "duration_s": 8.3}` |
| `"error"` | 安装失败/扫描超时 | `{"msg": "nmap not found and winget install failed"}` |

**payload 结构（discovery 事件）**：

```python
{
    "ip": "192.168.5.2",                 # str — 必有
    "mac": "AA:BB:CC:DD:EE:FF" | None,   # str | None — ARP 可能解析不到
    "vendor": "Espressif" | None,        # str | None — OUI 数据库查到的厂商
    "hostnames": ["govee-h5179-abc123.local."],
    "status": "up",                      # "up" | "down"
    "os_guess": "Linux 4.x (embedded)",  # str | None — nmap 的 OS 猜测
}
```

### nmap ↔ 其他源的关联价值

nmap 跟 MQTT/mDNS 的互补性：

- **nmap 提供 MAC** → mDNS 的 hostname 里通常有设备 ID 片段、MQTT topic 里有 `abc123` → MAC OUI 确认厂商。三者交叉验证后置信度大幅提升
- **nmap 提供全网视角** → mDNS 只能看到广播设备、MQTT 只能看到已连接 broker 的设备 → nmap 扫出所有在线主机，补上"盲区"
- **nmap 的 OS guess** 是粗信号（"Linux 4.x embedded" → IoT 设备），但跟 MQTT topic 的细信号叠加后能排除假阳性

### Source 实现要点

```python
@register_source("nmap")
class NmapSource(Source):
    async def start(self) -> None:
        # 1. 检查 nmap 是否安装
        version = await self._check_nmap()
        if version is None:
            installed = await self._install_nmap()
            if not installed:
                self.emit("error", {"msg": "nmap installation failed"})
                return
        self.emit("status", {"msg": f"nmap {version} ready"})

        # 2. 定期扫描
        while self._running:
            await self._scan()
            await asyncio.sleep(self._scan_interval)

    async def _scan(self) -> None:
        cmd = ["nmap", *self._flags.split(), "-oX", "-", self._subnet]
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE)
        stdout, _ = await proc.communicate()
        for host in self._parse_xml(stdout):
            self.emit("discovery", host)
```

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
        nmap_mac_prefixes=["AA:BB:CC"],
        nmap_os_guesses=["Linux*embedded*"],
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
        nmap_mac_prefixes=[],
        nmap_os_guesses=["Linux*embedded*"],
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
        nmap_mac_prefixes=[],
        nmap_os_guesses=[],
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

## MQTT Topic Classifier

MQTT 协议不区分操作 topic 和数据 topic——topic 语义完全依赖约定。需要一个带置信度的启发式分类器，在每个 MQTT 事件到达时判定其用途。

### 分类器规则（按置信度降序）

| # | 检测方式 | → 分类 | 置信度 |
|---|---------|--------|--------|
| 1 | payload 是小写枚举值（`"ON"`/`"OFF"`/`"TOGGLE"`/`"true"`/`"false"`/`"open"`/`"close"`） | `command` | 0.90 |
| 2 | topic 末段 = `set` / `command` / `cmnd` / `ctrl` / `control` | `command` | 0.85 |
| 3 | topic 末段含传感器词（`temperature` / `humidity` / `pressure` / `light` / `motion` / `voltage` / `current` / `power` / `co2` / `pm25`） | `telemetry` | 0.80 |
| 4 | payload 含数值 + unit 结构（`{"value": 25.3, "unit": "C"}`） | `telemetry` | 0.75 |
| 5 | topic 末段 = `state` / `status` / `data` / `telemetry` | `telemetry` | 0.70 |
| 6 | 以上都不匹配 | `unknown` | 0.30 |

### 输出

```python
@dataclass
class TopicClassification:
    category: str           # "telemetry" | "command" | "unknown"
    confidence: float       # 0.0 ~ 1.0
    rationale: str          # 命中了哪条规则
```

### 如何被管线使用

- **数据管线** — 优先处理 `telemetry` 类事件（置信度 ≥ 0.70），提取传感器数值。
- **操作管线** — 优先处理 `command` 类事件（置信度 ≥ 0.70），记录为设备能力。也考虑 `unknown` 类事件中那些 topic 带有操作语义的模式（`/toggle`, `/switch` 等）。
- **LLM profiling** — 分类结果作为设备特征的输入信号。

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
│   ├── fingerprint.py        # DeviceFingerprint, FINGERPRINTS registry
│   ├── evidence.py           # SignalEvidence
│   ├── device.py             # Device, DeviceHypothesis
│   ├── annotator.py          # Annotator ABC + ANNOTATORS registry + register_annotator
│   ├── annotators/
│   │   ├── __init__.py       # import all → trigger @register_annotator
│   │   ├── mqtt.py           # MqttAnnotator
│   │   ├── mdns.py           # MdnsAnnotator
│   │   ├── ssdp.py           # SsdpAnnotator
│   │   └── nmap.py           # NmapAnnotator (未来)
│   ├── deduplicator.py       # Deduplicator
│   ├── bayes.py              # BayesUpdater
│   ├── registry.py           # DeviceRegistry
│   └── identifier.py         # DeviceIdentifier (orchestrator)
```

---

## 实现路径

1. `fingerprint.py` + `evidence.py` + `device.py` — 定义数据结构
2. `annotator.py` + `annotators/__init__.py` — Annotator ABC + 注册表骨架
3. `annotators/mqtt.py` + `annotators/mdns.py` + `annotators/ssdp.py` — 三个标注器实现，从 SourceEvent 转 SignalEvidence
4. `deduplicator.py` — 按 IP/hostname 关联
5. `bayes.py` — 似然计算 + 贝叶斯更新
6. `registry.py` — 设备注册表
7. `identifier.py` — 串联全流程，暴露 `ingest(event)` 接口
8. 接入 MainWindow — 监听 `device_identified` 信号，显示设备列表

先写数据结构 + annotator 骨架 + MqttAnnotator，Govee H5179 一个指纹能跑通全流程，再扩展其他标注器。
