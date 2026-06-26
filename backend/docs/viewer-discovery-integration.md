# Viewer and Discover Integration

The RGB-D Viewer now owns the Discover runtime. Normal operation requires one
process:

```powershell
cd backend
$env:UV_CACHE_DIR=".uv-cache"
uv run python viewer/quest3_rgbd_align_viewer.py --server
```

The Viewer starts discovery sources from `discover_client/config.toml` and
stores stable network identities in:

```text
viewer_room_store/discover_registry.json
```

The `Network Devices` tab supports multi-selection and TSV/JSON copy. `Data`,
`Operations`, and `Device Profiles` expose the latest values, explicit command
topics, identity evidence, and the reason records were grouped. The
`Discovery Settings` tab has an Add/Edit/Enable/Delete source manager; the raw
TOML editor remains available as an advanced view.

## Identity Model

`viewer.device_id` identifies the Quest capture device. A marked physical
device is identified by `(room_id, object_id)`. It is bound to Discover through
`canonical_device_id`.

IP addresses are connections, not persistent identities. Stable aliases use
MAC addresses, SSDP USNs, source-scoped MQTT logical identities, explicit
discovery identifiers, and high-specificity identifiers shared across sources.

MQTT identity is resolved in this order:

1. Explicit Home Assistant MQTT discovery device identifiers.
2. Zigbee2MQTT `bridge/devices` IEEE address and `exposes` metadata.
3. Source-scoped logical topic identities from known dialects.
4. A learned topic-tree model that separates a physical entity from repeated
   terminal channels, such as `location/{LMS,TPS,CDS}`.
5. Semantic keyword inference only for low-confidence display classification;
   it is not used as the primary device identity.

Operations are exposed only when there is a publishable command topic:

- Home Assistant MQTT discovery supplies `command_topic` or `cmd_t`. The
  parser expands `~` base-topic shorthand and device-based `components`/`cmps`
  configs into concrete state and command topics.
- Zigbee2MQTT supplies writable `exposes` entries and the device `/set` topic.
  Only properties with `access & 2` contribute command values.
- Homie 5 supplies a retained `homie/5/<device-id>/$description`; properties
  with `settable: true` become `<property-topic>/set` operations.
- Tasmota discovery supplies `FullTopic`, Topic, prefix order, relay layout, and
  accepted power states. These are expanded into the exact `POWER` command
  topic.
- An observed `/set`, `/command`, `cmnd`, or other explicit command topic is
  tracked directly.
- A telemetry value such as `POWER=ON` is not by itself considered an
  operation because it does not identify a safe publish destination.

MQTT 5 request/response properties and packet-level traffic fingerprints are
not promoted to confirmed operations in the current application pipeline. They
can indicate that a control exchange happened, but they do not always provide a
stable, safe publish destination for the Viewer operation UI.

Legacy Tasmota `/sensors` discovery snapshots are consumed as metadata rather
than devices. Earlier registries that treated the `sn` sensor-snapshot key as a
serial number are repaired at startup by removing the corrupted discovery
aliases and rebuilding devices from their explicit config.

On startup, the persistent registry migrates old channel-level records to the
physical-entity model. Legacy root records produced by the old bare-value
fallback are removed only when the same broker contains at least five
descendant entities and the root has no independent MAC/IP/hostname/SSDP
identity.

Registry and source configuration writes use one debounced atomic replacement,
unique temporary files, `fsync`, and retry with exponential backoff. A transient
Windows antivirus/indexer lock no longer causes the MQTT event to be ingested
again through the fallback pipeline.

## Startup and Throughput

- MQTT callbacks only update in-memory evidence. Device profiles are rebuilt in
  one batch at most once per second, rather than once per message.
- The startup Registry migration runs on the Discover background thread, not
  the Tk caller thread.
- The Discover callback only sets a thread-safe dirty flag. Tk polls revisions
  on its own event loop every two seconds.
- Data, Operations, and Device Profiles trees are populated only while their
  tab is visible.
- SAM2 and Any2Full preload in a serialized background worker. The Viewer
  window and HTTP server become available before model loading finishes.
- Any2Full startup has a bounded timeout, so a worker that never emits its
  ready response cannot permanently block Viewer startup.

An 8-second test against the configured MQTT broker processed 1,736 incoming
events into 144 physical devices using six profile rebuilds. The final rebuild
took approximately 250 ms. The previous implementation would have attempted
1,736 complete profile and UI rebuilds.

## Source Strategy

- MQTT remains the primary source. Prefer explicit device metadata from Home
  Assistant discovery, Zigbee2MQTT, Homie, or Sparkplug when the publisher can
  provide it.
- Packet sniffing is an optional identity source. A `packet_sniff` source can
  import pcap/pcapng captures or run a live Scapy/Npcap capture for ARP plus
  MQTT TCP port 1883. It extracts `MQTT CONNECT` client IDs, client IPs,
  ARP-derived MAC addresses, and client-to-broker `PUBLISH` topic prefixes.
  This evidence is used for identity binding only; it is not treated as
  data/operation evidence.
- mDNS and SSDP add hostname, service, USN, and description identities for
  local IP devices.
- Nmap contributes MAC/OUI, hostnames, and network reachability.
- Broker management APIs such as EMQX client/subscription endpoints can add
  MQTT client ID and source IP evidence, but require a broker-specific adapter
  and credentials. They are not assumed to be universally available.
- Traffic-fingerprint ML is useful as a candidate/type signal, not as an
  automatic persistent identity. It should remain below explicit identifiers
  and require user confirmation for ambiguous pairings.

### Packet Sniff Source

Offline pcap import needs no extra Python dependencies. Capture on the broker
host or a switch mirror port, then add this source in Discovery Settings:

```toml
[[sources]]
source_id = "sniff-lab-pcap"
source_type = "packet_sniff"
enabled = true

[sources.settings]
pcap_path = "D:\\captures\\mqtt.pcap"
live = false
interface = ""
broker_ports = [1883]
capture_filter = ""
emit_publish_topics = true
max_packets = 0
```

Live capture is optional and requires Scapy plus Npcap/libpcap privileges:

```toml
[[sources]]
source_id = "sniff-lab-live"
source_type = "packet_sniff"
enabled = true

[sources.settings]
pcap_path = ""
live = true
interface = "Wi-Fi"
broker_ports = [1883]
capture_filter = ""
emit_publish_topics = true
max_packets = 0
```

The default live BPF filter is `arp or (tcp port 1883)`. TLS MQTT on 8883 is
not decoded because the MQTT CONNECT/PUBLISH payload is encrypted.

## Pairing Flow

1. Viewer VLM analysis creates a structured visual profile.
2. Discover creates structured profiles from MQTT, mDNS, SSDP, and Nmap
   evidence.
3. The LLM evaluates fixed rules as `match`, `conflict`, or `unknown`.
4. Backend code applies the fixed scoring table.
5. A user confirms one candidate before the binding is persisted.

## HTTP API

```text
GET  /api/discover/status
GET  /api/discover/devices
GET  /api/room/object/pairing/candidates
POST /api/room/object/pairing/refresh
POST /api/room/object/pairing/bind
POST /api/room/object/pairing/unbind
```

Binding records are stored inside the completed Viewer object record under
`network_binding`. A network device cannot be bound to two objects in the same
room unless the caller explicitly uses `force: true`.
