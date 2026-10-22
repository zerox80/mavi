package com.mavi.vpn

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow

enum class VpnConnectionState {
    STOPPED, CONNECTING, CONNECTED, RECONNECTING, PAUSED;

    val canStop: Boolean get() = this != STOPPED
    val canPause: Boolean get() = this == CONNECTED || this == RECONNECTING
}

/** UI lifetime covers the whole worker session, including handshake and retries.
 * Callers serialize updates with the service's session-generation lock. */
internal class VpnConnectionStatus {
    private val mutableState = MutableStateFlow(VpnConnectionState.STOPPED)
    val state = mutableState.asStateFlow()

    private val mutablePausedUntil = MutableStateFlow(0L)

    /** Wall-clock millis at which a paused tunnel resumes, or 0 when not paused. */
    val pausedUntil = mutablePausedUntil.asStateFlow()

    fun start() {
        mutablePausedUntil.value = 0L
        mutableState.value = VpnConnectionState.CONNECTING
    }

    fun connected(connected: Boolean) {
        val current = mutableState.value
        if (current == VpnConnectionState.STOPPED || current == VpnConnectionState.PAUSED) return
        mutableState.value = when {
            connected -> VpnConnectionState.CONNECTED
            current == VpnConnectionState.CONNECTING -> VpnConnectionState.CONNECTING
            else -> VpnConnectionState.RECONNECTING
        }
    }

    fun pause(resumeAtMillis: Long) {
        mutablePausedUntil.value = resumeAtMillis
        mutableState.value = VpnConnectionState.PAUSED
    }

    fun stop() {
        mutablePausedUntil.value = 0L
        mutableState.value = VpnConnectionState.STOPPED
    }
}
