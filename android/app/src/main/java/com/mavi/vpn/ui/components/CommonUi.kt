package com.mavi.vpn.ui.components

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.drawable.BitmapDrawable
import android.graphics.drawable.Drawable
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.LocalTextStyle
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextFieldColors
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import com.mavi.vpn.ui.theme.MaviColors
import com.mavi.vpn.ui.theme.MaviMono
import com.mavi.vpn.ui.theme.MaviShapes
import com.mavi.vpn.ui.theme.MaviTheme

@Composable
fun MaviTextField(
    value: String,
    onValueChange: (String) -> Unit,
    label: String,
    modifier: Modifier = Modifier,
    placeholder: String = "",
    singleLine: Boolean = true,
    visualTransformation: androidx.compose.ui.text.input.VisualTransformation =
        androidx.compose.ui.text.input.VisualTransformation.None,
    mono: Boolean = false,
) {
    val T = MaviTheme.colors
    OutlinedTextField(
        value = value,
        onValueChange = onValueChange,
        label = { Text(label) },
        placeholder = { if (placeholder.isNotEmpty()) Text(placeholder) },
        modifier = modifier,
        singleLine = singleLine,
        visualTransformation = visualTransformation,
        shape = RoundedCornerShape(MaviShapes.control),
        textStyle = if (mono) LocalTextStyle.current.copy(fontFamily = MaviMono) else LocalTextStyle.current,
        colors = maviTextFieldColors(T),
    )
}

@Composable
fun maviTextFieldColors(T: MaviColors = MaviTheme.colors): TextFieldColors =
    OutlinedTextFieldDefaults.colors(
        focusedBorderColor = T.accent,
        unfocusedBorderColor = T.lineStrong,
        focusedTextColor = T.ink,
        unfocusedTextColor = T.ink,
        focusedLabelColor = T.accent,
        unfocusedLabelColor = T.mute,
        focusedPlaceholderColor = T.mute,
        unfocusedPlaceholderColor = T.mute,
        focusedContainerColor = T.surface,
        unfocusedContainerColor = T.surface,
        cursorColor = T.accent,
    )

fun drawableToBitmap(drawable: Drawable): Bitmap? {
    return try {
        if (drawable is BitmapDrawable) {
            return drawable.bitmap
        }
        val width = if (drawable.intrinsicWidth > 0) drawable.intrinsicWidth else 48
        val height = if (drawable.intrinsicHeight > 0) drawable.intrinsicHeight else 48
        val bitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(bitmap)
        drawable.setBounds(0, 0, canvas.width, canvas.height)
        drawable.draw(canvas)
        bitmap
    } catch (e: Exception) {
        null
    }
}

fun Bitmap.toImageBitmap(): ImageBitmap = this.asImageBitmap()
