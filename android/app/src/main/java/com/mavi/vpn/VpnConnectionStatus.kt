package com.mavi.vpn

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow

enum class VpnConnectionState {
    STOPPED, CONNECTING, CONNECTED, RECONNECTING;

    val canStop: Boolean get() = this != STOPPED
}

/** UI lifetime covers the whole worker session, including handshake and retries.
 * Callers serialize updates with the service's session-generation lock. */
internal class VpnConnectionStatus {
    private val mutableState = MutableStateFlow(VpnConnectionState.STOPPED)
    val state = mutableState.asStateFlow()

    fun start() {
        mutableState.value = VpnConnectionState.CONNECTING
    }

    fun connected(connected: Boolean) {
        val current = mutableState.value
        if (current == VpnConnectionState.STOPPED) return
        mutableState.value = when {
            connected -> VpnConnectionState.CONNECTED
            current == VpnConnectionState.CONNECTING -> VpnConnectionState.CONNECTING
            else -> VpnConnectionState.RECONNECTING
        }
    }

    fun stop() {
        mutableState.value = VpnConnectionState.STOPPED
    }
}
