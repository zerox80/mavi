package com.mavi.vpn

import com.mavi.vpn.data.MemoryPreferences
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class VpnPauseRecoveryTest {
    @Test
    fun resumeBeforeStickyRestartRecoversPauseAfterProcessLoss() {
        val storage = MemoryPreferences()
        assertTrue(VpnPauseRecovery(storage.prefs).markPaused())

        // A new process has neither the original recovery object nor PAUSED state.
        val restored = VpnPauseRecovery(storage.prefs)
        val connection = VpnConnectionStatus()
        assertTrue(restored.canResume(connection.state.value))
    }

    @Test
    fun delayedResumeAfterStopDoesNotReconnect() {
        val storage = MemoryPreferences()
        val recovery = VpnPauseRecovery(storage.prefs)
        assertTrue(recovery.markPaused())
        assertTrue(recovery.clear())

        val restored = VpnPauseRecovery(storage.prefs)
        assertFalse(restored.canResume(VpnConnectionStatus().state.value))
    }

    @Test
    fun resumeWithoutPauseDoesNotStartVpn() {
        val recovery = VpnPauseRecovery(MemoryPreferences().prefs)
        assertFalse(recovery.canResume(VpnConnectionState.STOPPED))
    }

    @Test
    fun duplicateResumeDoesNotInterruptReconnectingTunnel() {
        val recovery = VpnPauseRecovery(MemoryPreferences().prefs)
        assertTrue(recovery.markPaused())
        for (state in listOf(VpnConnectionState.CONNECTING, VpnConnectionState.CONNECTED, VpnConnectionState.RECONNECTING)) {
            assertFalse(recovery.canResume(state))
        }
    }

    @Test
    fun inMemoryPauseStillResumesWithoutPersistedMarker() {
        val recovery = VpnPauseRecovery(MemoryPreferences().prefs)
        assertTrue(recovery.canResume(VpnConnectionState.PAUSED))
    }

    @Test
    fun failedPersistenceIsReportedSoServiceCanRefuseToPause() {
        val storage = MemoryPreferences().apply { commitSucceeds = false }
        val recovery = VpnPauseRecovery(storage.prefs)
        assertFalse(recovery.markPaused())
        assertFalse(recovery.clear())
    }
}
