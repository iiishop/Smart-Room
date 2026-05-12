# Quest 3 WiFi API Validation Runbook (DEA-88)

## 0) Build prerequisites

- JDK: `17` or `21` (do not use JDK `25+` with current wrapper `8.2.1`)
- Verify Java:

```bash
java -version
```

- If current Java is not 17/21, set `JAVA_HOME` to a JDK 17/21 installation before build.

## 1) Build APK

```bash
cd quest-wifi-probe
./gradlew --version
./gradlew clean assembleRelease
```

Expected artifact:

- `quest-wifi-probe/app/build/outputs/apk/release/app-release.apk`

Capture reproducible artifact info:

```bash
sha256sum quest-wifi-probe/app/build/outputs/apk/release/app-release.apk
```

Windows PowerShell alternative:

```powershell
Get-FileHash -Algorithm SHA256 "quest-wifi-probe/app/build/outputs/apk/release/app-release.apk"
```

## 2) Sideload to Quest 3

```bash
adb install -r quest-wifi-probe/app/build/outputs/apk/release/app-release.apk
adb shell am start -n ai.multica.questwifiprobe/.MainActivity
```

Grant permissions in headset UI if prompted (location + notifications).

## 3) Collect logs

```bash
adb logcat -c
adb logcat -v time QUEST_WIFI_PROBE:I *:S > quest3_wifi_probe.log
```

Also pull app file log:

```bash
adb shell run-as ai.multica.questwifiprobe cat files/quest_wifi_probe.log > quest3_wifi_probe_file.log
```

## 4) Test scripts by validation item

- V1: Keep headset active, run at least one 2.4GHz scene and one 5GHz scene, verify `SCAN_ITEM` lines are non-empty.
- V2: Keep app foreground for 10 minutes, then read `SUMMARY` and verify avg scan interval.
- V3: Keep connected to WiFi for 5 minutes, inspect `RSSI_CHANGE` deltas.
- V7: Repeat with screen-off and with headset removed (sleep) while service remains alive; compare scan/rssi cadence.

## 5) Fill report

Use `docs/09_QUEST3_WIFI_API_VALIDATION_REPORT.md` and attach:

- APK file hash + path
- full logcat file(s)
- pass/fail per V1/V2/V3/V7
