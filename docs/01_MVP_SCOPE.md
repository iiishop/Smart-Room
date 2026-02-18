# Quest 3 MVP Scope

## Objective

Deliver the smallest usable Quest 3 + Unity + Python pipeline that streams RGB and depth from headset to PC.

## In Scope

1. Quest 3 Unity app runs on-device.
2. App captures RGB and depth-related data path available to the chosen SDK route.
3. App sends frames/packets to PC backend through local network.
4. Python backend accepts packets and logs/saves basic output.

## Out of Scope (for this MVP)

- Full smart-home control UI.
- Home Assistant deep integration.
- Object recognition automation (YOLO, segmentation, etc.).
- Production-grade compression, encryption, and high-performance transport tuning.

## Suggested Acceptance Criteria

1. Quest app can connect to backend by IP and port.
2. Backend receives continuous data stream for at least 30 seconds.
3. Backend can confirm packet type (RGB/depth/meta) and timestamp order.
4. End-to-end reconnect works after one network interruption.

## Risks To Track

- Depth access differs by SDK path (Meta XR Core route vs OpenXR/AR Foundation route).
- Spatial permissions must be granted on-device.
- Bandwidth and frame size can cause dropped packets.
