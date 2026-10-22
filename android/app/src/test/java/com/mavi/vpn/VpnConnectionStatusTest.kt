package com.mavi.vpn

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class VpnConnectionStatusTest {
    @Test
    fun unreachableServerRemainsCancellableUntilStopped() {
        val status = VpnConnectionStatus()
        assertFalse(status.state.value.canStop)
        status.start()
        repeat(3) {
            status.connected(false)
            assertEquals(VpnConnectionState.CONNECTING, status.state.value)
            assertTrue(status.state.value.canStop)
        }
        status.stop()
        assertEquals(VpnConnectionState.STOPPED, status.state.value)
        assertFalse(status.state.value.canStop)
        status.connected(true)
        assertEquals(VpnConnectionState.STOPPED, status.state.value)
    }

    @Test
    fun droppedTunnelRemainsCancellableAndCanReconnect() {
        val status = VpnConnectionStatus()
        status.start()
        status.connected(true)
        status.connected(false)
        assertEquals(VpnConnectionState.RECONNECTING, status.state.value)
        assertTrue(status.state.value.canStop)
        status.connected(false)
        assertEquals(VpnConnectionState.RECONNECTING, status.state.value)
        status.connected(true)
        assertEquals(VpnConnectionState.CONNECTED, status.state.value)
        status.stop()
        status.connected(false)
        assertEquals(VpnConnectionState.STOPPED, status.state.value)
        status.start()
        assertEquals(VpnConnectionState.CONNECTING, status.state.value)
    }

    @Test
    fun pausedTunnelIgnoresLateWorkerUpdatesUntilResumedOrStopped() {
        val status = VpnConnectionStatus()
        status.start()
        status.connected(true)
        assertTrue(status.state.value.canPause)

        status.pause(resumeAtMillis = 42L)
        assertEquals(VpnConnectionState.PAUSED, status.state.value)
        assertEquals(42L, status.pausedUntil.value)
        assertTrue(status.state.value.canStop)
        assertFalse(status.state.value.canPause)
        status.connected(true)
        status.connected(false)
        assertEquals(VpnConnectionState.PAUSED, status.state.value)

        status.start()
        assertEquals(VpnConnectionState.CONNECTING, status.state.value)
        assertEquals(0L, status.pausedUntil.value)
        assertFalse(status.state.value.canPause)

        status.pause(resumeAtMillis = 7L)
        status.stop()
        assertEquals(VpnConnectionState.STOPPED, status.state.value)
        assertEquals(0L, status.pausedUntil.value)
    }
}
