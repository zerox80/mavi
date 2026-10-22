package com.mavi.vpn

import android.app.AlarmManager
import android.app.PendingIntent
import android.os.Looper
import android.os.SystemClock
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.robolectric.annotation.Implementation
import org.robolectric.annotation.Implements
import org.robolectric.shadows.ShadowAlarmManager
import java.time.Duration

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [31], manifest = Config.NONE)
class VpnPauseSchedulerTest {
    private val context get() = RuntimeEnvironment.getApplication()
    private val alarms get() = shadowOf(context.getSystemService(AlarmManager::class.java))

    @Test
    fun fallbackWithoutExactPermissionCanWakeDeviceDuringDoze() {
        ShadowAlarmManager.setCanScheduleExactAlarms(false)
        assertResumeAlarm(ShadowAlarmManager.WINDOW_HEURISTIC)
    }

    @Test
    fun exactAlarmIsUsedWhenPermissionIsGranted() {
        ShadowAlarmManager.setCanScheduleExactAlarms(true)
        assertResumeAlarm(ShadowAlarmManager.WINDOW_EXACT)
    }

    @Test
    @Config(shadows = [RevokedExactAlarmManager::class])
    fun permissionRevokedDuringSchedulingFallsBackToIdleCapableAlarm() {
        ShadowAlarmManager.setCanScheduleExactAlarms(true)
        assertResumeAlarm(ShadowAlarmManager.WINDOW_HEURISTIC)
    }

    @Test
    fun stoppingPauseCancelsBothWakeupAlarmAndInProcessTimer() {
        var resumeCount = 0
        val scheduler = VpnPauseScheduler(context) { resumeCount++ }
        scheduler.schedule(300_000L)
        scheduler.cancel()
        shadowOf(Looper.getMainLooper()).idleFor(Duration.ofMinutes(6))

        assertTrue(alarms.scheduledAlarms.isEmpty())
        assertEquals(0, resumeCount)
    }

    private fun assertResumeAlarm(expectedWindow: Long) {
        val deadline = SystemClock.elapsedRealtime() + 300_000L
        val scheduler = VpnPauseScheduler(context) {}
        scheduler.schedule(300_000L)
        val alarm = alarms.scheduledAlarms.single()

        assertEquals(AlarmManager.ELAPSED_REALTIME_WAKEUP, alarm.getType())
        assertEquals(deadline, alarm.triggerAtMs)
        assertTrue(alarm.isAllowWhileIdle)
        assertEquals(expectedWindow, alarm.windowLengthMs)
        @Suppress("DEPRECATION") // Robolectric exposes the PendingIntent only through this field.
        val operation = alarm.operation
        assertEquals(ACTION_RESUME, shadowOf(operation).savedIntent.action)
        scheduler.cancel()
    }

    @Implements(AlarmManager::class)
    class RevokedExactAlarmManager : ShadowAlarmManager() {
        @Implementation
        override fun setExactAndAllowWhileIdle(type: Int, triggerAtMillis: Long, operation: PendingIntent) {
            throw SecurityException("Exact alarm permission revoked")
        }
    }
}
