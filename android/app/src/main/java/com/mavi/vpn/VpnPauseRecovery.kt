package com.mavi.vpn

import android.content.SharedPreferences

private const val PENDING_PAUSE_KEY = "pending_vpn_pause"

/** Survives process death, including when RESUME arrives before a sticky restart. */
internal class VpnPauseRecovery(private val prefs: SharedPreferences) {
    // Persist before tearing down the tunnel; refuse the pause if storage fails.
    fun markPaused(): Boolean = prefs.edit().putBoolean(PENDING_PAUSE_KEY, true).commit()

    fun canResume(state: VpnConnectionState): Boolean =
        state == VpnConnectionState.PAUSED ||
            (state == VpnConnectionState.STOPPED && prefs.getBoolean(PENDING_PAUSE_KEY, false))

    // Explicit Stop and successful starts invalidate any queued resume command.
    fun clear(): Boolean = prefs.edit().remove(PENDING_PAUSE_KEY).commit()
}
