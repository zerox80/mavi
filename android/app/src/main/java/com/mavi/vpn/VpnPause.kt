package com.mavi.vpn

import java.util.Locale

internal const val ACTION_PAUSE = "PAUSE"
internal const val ACTION_RESUME = "RESUME"
internal const val EXTRA_PAUSE_MINUTES = "PAUSE_MINUTES"

/** Pause lengths offered in the UI. The service rejects anything else, so a
 *  pause always ends with the tunnel coming back on its own. */
val VPN_PAUSE_MINUTES = listOf(5, 15, 30, 60)

internal fun pauseDurationMillis(minutes: Int): Long? = if (minutes in VPN_PAUSE_MINUTES) minutes * 60_000L else null

fun pauseOptionLabel(minutes: Int): String = if (minutes % 60 == 0) "${minutes / 60} h" else "$minutes min"

/** Formats the time left until resume as m:ss, or h:mm:ss from one hour up. */
fun formatPauseRemaining(remainingMillis: Long): String {
    val totalSeconds = (remainingMillis.coerceAtLeast(0L) + 999L) / 1000L
    val hours = totalSeconds / 3600
    val minutes = (totalSeconds % 3600) / 60
    val seconds = totalSeconds % 60
    return if (hours > 0) {
        "%d:%02d:%02d".format(Locale.ROOT, hours, minutes, seconds)
    } else {
        "%d:%02d".format(Locale.ROOT, minutes, seconds)
    }
}
