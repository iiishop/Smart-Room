# Quest WiFi Probe

Minimal Android app for Quest 3 WiFi API validation (DEA-88).

## What it logs

- `SCAN`: periodic `startScan()` trigger + result count
- `SCAN_ITEM`: SSID, BSSID, RSSI, frequency, capabilities
- `RSSI_CHANGE`: timestamped RSSI value changes
- `SUMMARY`: min/max/avg scan interval and effective scans/min

Log tag: `QUEST_WIFI_PROBE`

## Build

```bash
./gradlew assembleRelease
```

See runbook at `docs/08_QUEST3_WIFI_API_VALIDATION_RUNBOOK.md`.
