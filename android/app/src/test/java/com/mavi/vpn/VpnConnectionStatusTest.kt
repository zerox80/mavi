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
}
