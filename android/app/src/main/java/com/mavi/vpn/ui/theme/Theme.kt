package com.mavi.vpn.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * Mavi VPN design tokens. The desktop client mirrors these names and values in
 * gui/src/style.css; change both when a token changes.
 */
@Immutable
data class MaviColors(
    val bg: Color,
    val surface: Color,
    val elevated: Color,
    val subtle: Color,
    val ink: Color,
    val ink2: Color,
    val mute: Color,
    val line: Color,
    val lineStrong: Color,
    val accent: Color,
    val accentSolid: Color,
    val accentSoft: Color,
    val onAccent: Color,
    val ok: Color,
    val okSoft: Color,
    val warn: Color,
    val warnSoft: Color,
    val danger: Color,
    val dangerSoft: Color,
)

val LightColors = MaviColors(
    bg = Color(0xFFF6F7F9),
    surface = Color(0xFFFFFFFF),
    elevated = Color(0xFFFFFFFF),
    subtle = Color(0xFFEEF0F3),
    ink = Color(0xFF111318),
    ink2 = Color(0xFF3F4551),
    mute = Color(0xFF6B7280),
    line = Color(0xFFE3E6EB),
    lineStrong = Color(0xFFCFD4DC),
    accent = Color(0xFF2B44FF),
    accentSolid = Color(0xFF2B44FF),
    accentSoft = Color(0xFFEDF0FF),
    onAccent = Color(0xFFFFFFFF),
    ok = Color(0xFF0F7B4F),
    okSoft = Color(0xFFE6F5ED),
    warn = Color(0xFFA15C07),
    warnSoft = Color(0xFFFDF3E1),
    danger = Color(0xFFC0281F),
    dangerSoft = Color(0xFFFDECEA),
)

val DarkColors = MaviColors(
    bg = Color(0xFF0C0D10),
    surface = Color(0xFF121418),
    elevated = Color(0xFF1A1C22),
    subtle = Color(0xFF1C1F26),
    ink = Color(0xFFECEEF2),
    ink2 = Color(0xFFB6BBC6),
    mute = Color(0xFF808796),
    line = Color(0xFF23262E),
    lineStrong = Color(0xFF333742),
    accent = Color(0xFF8494FF),
    accentSolid = Color(0xFF3D55F5),
    accentSoft = Color(0xFF1B2048),
    onAccent = Color(0xFFFFFFFF),
    ok = Color(0xFF45C98A),
    okSoft = Color(0xFF11281E),
    warn = Color(0xFFEAA64B),
    warnSoft = Color(0xFF2B2112),
    danger = Color(0xFFF0776C),
    dangerSoft = Color(0xFF2E1614),
)

val LocalMaviColors = staticCompositionLocalOf { LightColors }

/** Corner radii shared with the desktop client (--radius-md / --radius-lg). */
object MaviShapes {
    val control = 10.dp
    val card = 14.dp
}

/** Technical values (endpoints, addresses, pins) are set in the monospace face. */
val MaviMono = FontFamily.Monospace

val MaviTypography = Typography(
    headlineSmall = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.SemiBold,
        fontSize = 24.sp,
        lineHeight = 30.sp,
        letterSpacing = (-0.4).sp,
    ),
    titleMedium = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.SemiBold,
        fontSize = 18.sp,
        lineHeight = 24.sp,
    ),
    bodyLarge = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Normal,
        fontSize = 15.sp,
        lineHeight = 22.sp,
    ),
    bodyMedium = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Normal,
        fontSize = 14.sp,
        lineHeight = 20.sp,
    ),
    labelLarge = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Medium,
        fontSize = 15.sp,
    ),
    labelMedium = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Medium,
        fontSize = 13.sp,
    ),
    labelSmall = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Medium,
        fontSize = 12.sp,
    ),
)

@Composable
fun MaviVpnTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    content: @Composable () -> Unit,
) {
    val colors = if (darkTheme) DarkColors else LightColors

    // Map the tokens onto Material3 so stock components pick them up too.
    val materialColorScheme = if (darkTheme) {
        darkColorScheme(
            background = colors.bg,
            surface = colors.surface,
            surfaceVariant = colors.subtle,
            primary = colors.accentSolid,
            onPrimary = colors.onAccent,
            onBackground = colors.ink,
            onSurface = colors.ink,
            onSurfaceVariant = colors.ink2,
            outline = colors.lineStrong,
            outlineVariant = colors.line,
            error = colors.danger,
        )
    } else {
        lightColorScheme(
            background = colors.bg,
            surface = colors.surface,
            surfaceVariant = colors.subtle,
            primary = colors.accentSolid,
            onPrimary = colors.onAccent,
            onBackground = colors.ink,
            onSurface = colors.ink,
            onSurfaceVariant = colors.ink2,
            outline = colors.lineStrong,
            outlineVariant = colors.line,
            error = colors.danger,
        )
    }

    CompositionLocalProvider(LocalMaviColors provides colors) {
        MaterialTheme(
            colorScheme = materialColorScheme,
            typography = MaviTypography,
            content = content,
        )
    }
}

object MaviTheme {
    val colors: MaviColors
        @Composable
        get() = LocalMaviColors.current
}
