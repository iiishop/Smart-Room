# Quest 3 Unity Streaming Plan

## Architecture (MVP)

- Sender: Unity app on Quest 3.
- Transport: local Wi-Fi (WebSocket or UDP; start with WebSocket for simplicity).
- Receiver: Python backend on PC.

## Milestones

### M1 - Connectivity

1. Unity app connects to Python endpoint.
2. Send heartbeat JSON every second.
3. Backend logs device ID and latency.

### M2 - RGB Path

1. Enable camera/passthrough path based on selected SDK route.
2. Downsample RGB frame (for example 320x240 or 640x360).
3. Serialize and send with timestamp + frame index.
4. Backend decodes and saves sample frames.

### M3 - Depth Path

1. Enable depth source (Depth API or OpenXR Meta depth feature).
2. Send reduced depth payload and metadata (size, format, min/max).
3. Backend validates continuity and payload integrity.

### M4 - Stability

1. Add reconnect logic in Unity.
2. Add receiver-side timeout and packet counters.
3. Run 5-minute soak test and record dropped packet ratio.

## Data Envelope (suggested)

```json
{
  "type": "rgb|depth|heartbeat",
  "device_id": "quest3-001",
  "frame_id": 1234,
  "timestamp_ms": 1739580000000,
  "encoding": "jpeg|raw16|json",
  "width": 640,
  "height": 360,
  "payload_b64": "..."
}
```

## Validation Checklist

- Connection success rate >= 95% during repeated app launches.
- RGB stream visible on backend.
- Depth stream arrives with expected frame metadata.
- Logs include connection, frame count, and failure reasons.
