package com.mavi.vpn.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
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
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.unit.dp
import com.mavi.vpn.VPN_PAUSE_MINUTES
import com.mavi.vpn.pauseOptionLabel
import com.mavi.vpn.ui.components.SectionLabel
import com.mavi.vpn.ui.theme.MaviShapes
import com.mavi.vpn.ui.theme.MaviTheme
import kotlinx.coroutines.delay

@Composable
fun PauseOptions(onPause: (Int) -> Unit) {
    val T = MaviTheme.colors
    val shape = RoundedCornerShape(MaviShapes.control)
    Column(modifier = Modifier.fillMaxWidth()) {
        SectionLabel("Pause for")
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            VPN_PAUSE_MINUTES.forEach { minutes ->
                Box(
                    modifier = Modifier
                        .weight(1f)
                        .clip(shape)
                        .background(T.surface)
                        .border(1.dp, T.line, shape)
                        .clickable(role = Role.Button) { onPause(minutes) }
                        .padding(vertical = 12.dp),
                    contentAlignment = Alignment.Center,
                ) {
                    Text(
                        text = pauseOptionLabel(minutes),
                        style = MaterialTheme.typography.labelMedium,
                        color = T.ink,
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
