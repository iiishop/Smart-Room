package ai.multica.questwifiprobe

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.net.wifi.WifiManager
import android.os.Build
import android.os.IBinder
import android.os.SystemClock
import android.util.Log
import androidx.core.app.NotificationCompat
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledFuture
import java.util.concurrent.TimeUnit

class WifiProbeService : Service() {
    private val executor = Executors.newSingleThreadScheduledExecutor()
    private var scanTask: ScheduledFuture<*>? = null
    private var rssiTask: ScheduledFuture<*>? = null
    private lateinit var wifiManager: WifiManager
    private lateinit var logger: ProbeLogger

    private val scanIntervalsMs = mutableListOf<Long>()
    private var lastScanTs = 0L
    private var startTs = 0L
    private var lastRssi: Int? = null
    private var lastRssiChangeTs = 0L

    override fun onCreate() {
        super.onCreate()
        wifiManager = applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
        logger = ProbeLogger(this)
        startTs = SystemClock.elapsedRealtime()
        startForeground(2001, createNotification())
        logger.log("SERVICE_START", "startedAt=${tsNow()}")

        scanTask = executor.scheduleAtFixedRate({ runScanTick() }, 0, 5, TimeUnit.SECONDS)
        rssiTask = executor.scheduleAtFixedRate({ runRssiTick() }, 0, 1, TimeUnit.SECONDS)
    }

    override fun onDestroy() {
        scanTask?.cancel(true)
        rssiTask?.cancel(true)
        executor.shutdownNow()
        emitSummary()
        logger.log("SERVICE_STOP", "stoppedAt=${tsNow()}")
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun runScanTick() {
        try {
            val requestedTs = SystemClock.elapsedRealtime()
            val started = wifiManager.startScan()
            val results = wifiManager.scanResults.orEmpty()
            val now = SystemClock.elapsedRealtime()

            if (lastScanTs > 0L) {
                scanIntervalsMs += now - lastScanTs
            }
            lastScanTs = now

            logger.log(
                "SCAN",
                "requestedAt=${tsNow()} started=$started elapsedSinceReqMs=${now - requestedTs} count=${results.size}"
            )
            for ((i, item) in results.withIndex()) {
                logger.log(
                    "SCAN_ITEM",
                    "index=$i ssid=${safe(item.SSID)} bssid=${safe(item.BSSID)} rssi=${item.level} freq=${item.frequency} cap=${safe(item.capabilities)}"
                )
            }
        } catch (e: Throwable) {
            logger.log("SCAN_ERROR", e.message ?: "unknown")
        }
    }

    private fun runRssiTick() {
        try {
            val rssi = wifiManager.connectionInfo?.rssi ?: Int.MIN_VALUE
            if (lastRssi == null || rssi != lastRssi) {
                val now = SystemClock.elapsedRealtime()
                val delta = if (lastRssiChangeTs == 0L) -1L else now - lastRssiChangeTs
                lastRssiChangeTs = now
                logger.log("RSSI_CHANGE", "at=${tsNow()} rssi=$rssi deltaMs=$delta")
            }
            lastRssi = rssi
        } catch (e: Throwable) {
            logger.log("RSSI_ERROR", e.message ?: "unknown")
        }
    }

    private fun emitSummary() {
        if (scanIntervalsMs.isEmpty()) {
            logger.log("SUMMARY", "noScanIntervalsCaptured=true")
            return
        }
        val min = scanIntervalsMs.minOrNull() ?: -1
        val max = scanIntervalsMs.maxOrNull() ?: -1
        val avg = scanIntervalsMs.average()
        val runtimeMin = (SystemClock.elapsedRealtime() - startTs) / 60000.0
        val scansPerMinute = if (runtimeMin <= 0.0) 0.0 else scanIntervalsMs.size / runtimeMin
        logger.log(
            "SUMMARY",
            "scanIntervalMinMs=$min scanIntervalMaxMs=$max scanIntervalAvgMs=${"%.2f".format(avg)} scansPerMin=${"%.2f".format(scansPerMinute)}"
        )
    }

    private fun createNotification(): Notification {
        val channelId = "quest_wifi_probe"
        val nm = getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(channelId, "Quest WiFi Probe", NotificationManager.IMPORTANCE_LOW)
            nm.createNotificationChannel(channel)
        }
        return NotificationCompat.Builder(this, channelId)
            .setSmallIcon(android.R.drawable.stat_notify_sync)
            .setContentTitle("Quest WiFi Probe")
            .setContentText("Collecting WiFi scan/RSSI telemetry")
            .setOngoing(true)
            .build()
    }

    private fun tsNow(): String {
        val fmt = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSSZ", Locale.US)
        return fmt.format(Date())
    }

    private fun safe(v: String?): String = (v ?: "").replace("\n", " ")
}

private class ProbeLogger(context: Context) {
    private val appCtx = context.applicationContext

    fun log(tag: String, msg: String) {
        val line = "[$tag] $msg"
        Log.i("QUEST_WIFI_PROBE", line)
        try {
            appCtx.openFileOutput("quest_wifi_probe.log", Context.MODE_APPEND).bufferedWriter().use {
                it.appendLine(line)
            }
        } catch (_: Throwable) {
        }
    }
}
