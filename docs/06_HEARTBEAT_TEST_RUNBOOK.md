# Heartbeat Test Runbook (Quest 3 + Unity + FastAPI + PySide6)

## 1. What is implemented

Unity side:

- `Assets/Scripts/Networking/StreamTransportSwitcher.cs`
- `Assets/Scripts/Networking/QuestHeartbeatClient.cs`
- `Assets/Scripts/UI/HeadLockedPanelFollower.cs`

Backend side:

- `backend/main.py` (FastAPI + WebSocket heartbeat receiver)
- `backend/run_dashboard.py` (starts API and PySide6 dashboard)

## 2. Unity scene wiring (once)

1. Open scene: `Assets/Scenes/SampleScene.unity`
2. Create `Canvas`:
   - Render Mode: `World Space`
   - Size: around `0.4m x 0.2m` (for readability)
3. Add 3 UI TMP text objects under Canvas:
   - `TitleText`
   - `CounterText`
   - `StatusText`
4. Create empty object `NetworkBootstrap` and add components:
   - `StreamTransportSwitcher`
   - `QuestHeartbeatClient`
5. Drag references into `QuestHeartbeatClient` inspector:
   - `Transport Switcher` -> `NetworkBootstrap`
   - `Title Text` -> `TitleText`
   - `Counter Text` -> `CounterText`
   - `Status Text` -> `StatusText`
   - Note: these three references must be `TMP_Text` components.
6. Add `HeadLockedPanelFollower` component to Canvas.

## 3. Transport configuration

- Keep `StreamTransportSwitcher.mode = Auto`
- Wired endpoint (USB debug): `ws://127.0.0.1:8000`
- Wireless endpoint: `ws://<PC_LAN_IP>:8000`

Auto behavior:

- Editor: wired mode
- Device build: wireless mode

## 4. Start backend dashboard

In `backend/` directory:

```bash
uv run python run_dashboard.py
```

You should see a window: `Quest 3 Heartbeat Dashboard`.

## 5. USB debug test steps

1. Connect Quest 3 with USB-C.
2. Ensure ADB sees device:

```bash
adb devices
```

3. Enable reverse tunnel:

```bash
adb reverse tcp:8000 tcp:8000
```

4. In Unity, click Play (or Build And Run to device).
5. Expected results:
   - HUD shows `Hello World` and `Hello World 1/2/3...`
   - Status text changes to connected/sent ticks
   - Dashboard updates connection status, tick, app version, device model

## 6. Wireless test steps (after app installed)

1. Quest 3 and PC join same Wi-Fi.
2. Set `wirelessEndpoint.host` to PC LAN IP.
3. Start backend (`uv run python run_dashboard.py`).
4. Launch app on headset.

## 7. Troubleshooting quick checks

1. Android OpenXR is enabled with Meta XR feature group.
2. `Meta Quest Support` is enabled under Android OpenXR features.
3. `Initialize XR on Startup` is checked on Android XR Plug-in Management.
4. If USB mode fails, rerun `adb reverse tcp:8000 tcp:8000`.
5. If dashboard shows disconnected, verify Windows firewall allows port `8000`.
