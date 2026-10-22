package com.mavi.vpn

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class VpnPauseTest {
    @Test
    fun onlyOfferedDurationsArePausable() {
        assertEquals(listOf(5, 15, 30, 60), VPN_PAUSE_MINUTES)
        assertEquals(300_000L, pauseDurationMillis(5))
        assertEquals(3_600_000L, pauseDurationMillis(60))
        for (minutes in listOf(0, -5, 1, 61, Int.MAX_VALUE)) {
            assertNull(pauseDurationMillis(minutes))
        }
    }

    @Test
    fun optionLabelsUseHoursForFullHours() {
        assertEquals(listOf("5 min", "15 min", "30 min", "1 h"), VPN_PAUSE_MINUTES.map(::pauseOptionLabel))
    }

    @Test
    fun remainingTimeRoundsUpAndSwitchesToHours() {
        assertEquals("0:00", formatPauseRemaining(0L))
        assertEquals("0:00", formatPauseRemaining(-1_000L))
        assertEquals("0:01", formatPauseRemaining(1L))
        assertEquals("4:59", formatPauseRemaining(299_000L))
        assertEquals("5:00", formatPauseRemaining(299_001L))
        assertEquals("59:59", formatPauseRemaining(3_599_000L))
        assertEquals("1:00:00", formatPauseRemaining(3_600_000L))
    }
}
