package com.mavi.vpn

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.SystemClock

private const val RESUME_ALARM_REQUEST_CODE = 2
private const val RESUME_ALARM_WINDOW_MILLIS = 60_000L

/**
 * Brings a paused tunnel back. The main-thread timer is precise while the
 * device is awake; its uptime clock stalls in deep sleep, so a wakeup alarm
 * on the elapsed-realtime clock covers that case. Whichever fires first
 * resumes, the other one then finds the tunnel no longer paused.
 */
internal class VpnPauseScheduler(
    private val context: Context,
    private val onResume: () -> Unit,
) {
    private val handler = Handler(Looper.getMainLooper())
    private val resumeRunnable = Runnable { onResume() }
    private val alarmManager = context.getSystemService(AlarmManager::class.java)
    private val resumeIntent: PendingIntent by lazy {
        PendingIntent.getService(
            context,
            RESUME_ALARM_REQUEST_CODE,
            Intent(context, MaviVpnService::class.java).setAction(ACTION_RESUME),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
    }

    fun schedule(durationMillis: Long) {
        cancel()
        handler.postDelayed(resumeRunnable, durationMillis)

        val alarms = alarmManager ?: return
        val triggerAt = SystemClock.elapsedRealtime() + durationMillis
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S || alarms.canScheduleExactAlarms()) {
            alarms.setExactAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, triggerAt, resumeIntent)
        } else {
            // Without the exact-alarm grant the system may stretch this window;
            // the handler above still resumes on time while the screen is on.
            alarms.setWindow(AlarmManager.ELAPSED_REALTIME_WAKEUP, triggerAt, RESUME_ALARM_WINDOW_MILLIS, resumeIntent)
        }
    }

    fun cancel() {
        handler.removeCallbacks(resumeRunnable)
        alarmManager?.cancel(resumeIntent)
    }
}
