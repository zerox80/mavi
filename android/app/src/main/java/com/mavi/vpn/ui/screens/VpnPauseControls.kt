package com.mavi.vpn.ui.screens

import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.mavi.vpn.VPN_PAUSE_MINUTES
import com.mavi.vpn.pauseOptionLabel
import com.mavi.vpn.ui.theme.MaviTheme
import kotlinx.coroutines.delay

@Composable
fun PauseOptions(onPause: (Int) -> Unit) {
    val T = MaviTheme.colors
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Text(
            text = "PAUSE FOR",
            fontFamily = MaterialTheme.typography.labelSmall.fontFamily,
            fontSize = 9.sp,
            letterSpacing = 1.5.sp,
            color = T.mute
        )
        Spacer(modifier = Modifier.height(8.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            VPN_PAUSE_MINUTES.forEach { minutes ->
                Box(
                    modifier = Modifier
                        .clip(CircleShape)
                        .border(1.dp, T.line, CircleShape)
                        .clickable { onPause(minutes) }
                        .padding(horizontal = 14.dp, vertical = 8.dp)
                ) {
                    Text(
                        text = pauseOptionLabel(minutes),
                        fontSize = 13.sp,
                        fontWeight = FontWeight.SemiBold,
                        color = T.ink2
                    )
                }
            }
        }
    }
}

/** Millis left until [pausedUntil], re-read once per second while paused. */
@Composable
fun rememberPauseRemainingMillis(pausedUntil: Long): Long {
    var now by remember(pausedUntil) { mutableLongStateOf(System.currentTimeMillis()) }
    LaunchedEffect(pausedUntil) {
        while (pausedUntil > 0L) {
            now = System.currentTimeMillis()
            delay(1_000)
        }
    }
    return (pausedUntil - now).coerceAtLeast(0L)
}
