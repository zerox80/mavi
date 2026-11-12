@file:Suppress("ktlint:standard:function-naming", "ktlint:standard:property-naming")

package com.mavi.vpn.ui.components

import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchColors
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.mavi.vpn.ui.theme.MaviMono
import com.mavi.vpn.ui.theme.MaviShapes
import com.mavi.vpn.ui.theme.MaviTheme

enum class StatusTone { NEUTRAL, OK, PROGRESS }

/** Bordered surface that groups related rows; rows are separated with [MaviDivider]. */
@Composable
fun MaviCard(
    modifier: Modifier = Modifier,
    content: @Composable ColumnScope.() -> Unit,
) {
    val T = MaviTheme.colors
    val shape = RoundedCornerShape(MaviShapes.card)
    Column(
        modifier = modifier
            .fillMaxWidth()
            .clip(shape)
            .background(T.surface)
            .border(1.dp, T.line, shape),
        content = content,
    )
}

@Composable
fun MaviDivider() {
    HorizontalDivider(thickness = 1.dp, color = MaviTheme.colors.line)
}

@Composable
fun SectionLabel(
    text: String,
    modifier: Modifier = Modifier,
) {
    Text(
        text = text,
        style = MaterialTheme.typography.labelMedium,
        color = MaviTheme.colors.mute,
        modifier = modifier.padding(start = 4.dp, bottom = 8.dp),
    )
}

/** Dot and text showing a state, with no container. The dot pulses while [pulsing]. */
@Composable
fun StatusLine(
    label: String,
    tone: StatusTone,
    modifier: Modifier = Modifier,
    pulsing: Boolean = false,
) {
    val T = MaviTheme.colors
    val color = when (tone) {
        StatusTone.OK -> T.ok
        StatusTone.PROGRESS -> T.warn
        StatusTone.NEUTRAL -> T.mute
    }
    val dotColor = if (tone == StatusTone.NEUTRAL) T.lineStrong else color
    val dotAlpha = if (pulsing) {
        val transition = rememberInfiniteTransition(label = "status")
        val alpha by transition.animateFloat(
            initialValue = 1f,
            targetValue = 0.35f,
            animationSpec = infiniteRepeatable(tween(600), RepeatMode.Reverse),
            label = "statusDot",
        )
        alpha
    } else {
        1f
    }

    Row(modifier = modifier, verticalAlignment = Alignment.CenterVertically) {
        Box(
            modifier = Modifier
                .size(7.dp)
                .alpha(dotAlpha)
                .background(dotColor, CircleShape),
        )
        Spacer(modifier = Modifier.width(8.dp))
        Text(text = label, style = MaterialTheme.typography.labelSmall, color = color)
    }
}

/** Label on the left, value on the right, set directly on the page. Use [mono] for addresses and other technical values. */
@Composable
fun DetailRow(
    label: String,
    value: String,
    mono: Boolean = false,
) {
    val T = MaviTheme.colors
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .heightIn(min = 48.dp)
            .padding(horizontal = 4.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(text = label, style = MaterialTheme.typography.bodyMedium, color = T.mute)
        Spacer(modifier = Modifier.width(16.dp))
        Text(
            text = value,
            modifier = Modifier.weight(1f),
            style = MaterialTheme.typography.bodyMedium,
            fontFamily = if (mono) MaviMono else null,
            fontWeight = if (mono) FontWeight.Normal else FontWeight.Medium,
            color = T.ink,
            textAlign = TextAlign.End,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
    }
}

@Composable
private fun RowText(
    title: String,
    description: String,
    modifier: Modifier = Modifier,
) {
    val T = MaviTheme.colors
    Column(modifier = modifier) {
        Text(
            text = title,
            style = MaterialTheme.typography.bodyLarge,
            fontWeight = FontWeight.Medium,
            color = T.ink,
        )
        Text(text = description, fontSize = 13.sp, lineHeight = 18.sp, color = T.mute)
    }
}

/** Setting with a switch; the whole row toggles. */
@Composable
fun SwitchRow(
    title: String,
    description: String,
    checked: Boolean,
    onCheckedChange: (Boolean) -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .toggleable(value = checked, role = Role.Switch, onValueChange = onCheckedChange)
            .padding(horizontal = 16.dp, vertical = 14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        RowText(title = title, description = description, modifier = Modifier.weight(1f))
        Spacer(modifier = Modifier.width(12.dp))
        Switch(checked = checked, onCheckedChange = null, colors = maviSwitchColors())
    }
}

/** Row that opens another screen or a system dialog. */
@Composable
fun NavRow(
    title: String,
    description: String,
    onClick: () -> Unit,
    horizontalPadding: Dp = 16.dp,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(role = Role.Button, onClick = onClick)
            .padding(horizontal = horizontalPadding, vertical = 14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        RowText(title = title, description = description, modifier = Modifier.weight(1f))
        Spacer(modifier = Modifier.width(12.dp))
        Icon(
            imageVector = Icons.AutoMirrored.Filled.KeyboardArrowRight,
            contentDescription = null,
            tint = MaviTheme.colors.mute,
            modifier = Modifier.size(20.dp),
        )
    }
}

@Composable
fun PrimaryButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    icon: ImageVector? = null,
) {
    val T = MaviTheme.colors
    Button(
        onClick = onClick,
        modifier = modifier.height(52.dp),
        shape = RoundedCornerShape(MaviShapes.control),
        colors = ButtonDefaults.buttonColors(containerColor = T.accentSolid, contentColor = T.onAccent),
    ) {
        if (icon != null) {
            Icon(imageVector = icon, contentDescription = null, modifier = Modifier.size(18.dp))
            Spacer(modifier = Modifier.width(8.dp))
        }
        Text(text = text, style = MaterialTheme.typography.labelLarge)
    }
}

/** Quiet outlined button for actions that stop or leave something, e.g. Disconnect. */
@Composable
fun SecondaryButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val T = MaviTheme.colors
    OutlinedButton(
        onClick = onClick,
        modifier = modifier.height(52.dp),
        shape = RoundedCornerShape(MaviShapes.control),
        colors = ButtonDefaults.outlinedButtonColors(containerColor = T.surface, contentColor = T.ink),
        border = BorderStroke(1.dp, T.lineStrong),
    ) {
        Text(text = text, style = MaterialTheme.typography.labelLarge)
    }
}

/** Title bar for screens that are opened from another screen. */
@Composable
fun ScreenHeader(
    title: String,
    onBack: () -> Unit,
    subtitle: String? = null,
) {
    val T = MaviTheme.colors
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(top = 4.dp, bottom = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        IconButton(onClick = onBack) {
            Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back", tint = T.ink)
        }
        Column(modifier = Modifier.padding(start = 4.dp)) {
            Text(text = title, style = MaterialTheme.typography.titleMedium, color = T.ink)
            if (subtitle != null) {
                Text(text = subtitle, fontSize = 13.sp, color = T.mute)
            }
        }
    }
}

@Composable
fun maviSwitchColors(): SwitchColors {
    val T = MaviTheme.colors
    return SwitchDefaults.colors(
        checkedThumbColor = T.onAccent,
        checkedTrackColor = T.accentSolid,
        checkedBorderColor = T.accentSolid,
        uncheckedThumbColor = T.mute,
        uncheckedTrackColor = T.subtle,
        uncheckedBorderColor = T.lineStrong,
    )
}
