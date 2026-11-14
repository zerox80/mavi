@file:Suppress("ktlint:standard:function-naming", "ktlint:standard:property-naming")

package com.mavi.vpn.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.WindowInsetsSides
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.only
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Link
import androidx.compose.material.icons.outlined.Tune
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.unit.dp
import com.mavi.vpn.VpnConnectionState
import com.mavi.vpn.ui.components.MaviDivider
import com.mavi.vpn.ui.theme.MaviTheme
import com.mavi.vpn.viewmodel.VpnViewModel
import kotlinx.coroutines.delay

@Composable
fun VpnScreen(
    viewModel: VpnViewModel,
    onConnect: (String, String, String, String) -> Unit,
    onDisconnect: () -> Unit,
    onPause: (Int) -> Unit,
    onResume: () -> Unit,
    onOpenSettings: () -> Unit,
) {
    val context = LocalContext.current
    val connectionState by viewModel.connectionState.collectAsState()
    val isConnected = connectionState == VpnConnectionState.CONNECTED

    val useKeycloak by viewModel.useKeycloak.collectAsState()

    // Mirror token changes persisted by MaviVpnService without owning refresh/logout decisions.
    LaunchedEffect(useKeycloak) {
        if (!useKeycloak) return@LaunchedEffect

        val prefs = com.mavi.vpn.data.PrefsManager(viewModel.getApplication())
        while (true) {
            val freshToken = prefs.keycloak.snapshot().tokens?.accessToken.orEmpty()
            if (freshToken != viewModel.authToken.value) {
                viewModel.authToken.value = freshToken
            }

            if (prefs.savedKeycloakSessionInvalid) {
                viewModel.updateErrorMessage("Keycloak access token expired. Please login again.")
            }
            delay(5_000)
        }
    }

    var currentTab by remember { mutableStateOf("home") }
    val T = MaviTheme.colors

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(T.bg)
            .windowInsetsPadding(WindowInsets.safeDrawing.only(WindowInsetsSides.Top + WindowInsetsSides.Horizontal))
            .imePadding(),
    ) {
        Box(
            modifier = Modifier
                .weight(1f)
                .fillMaxWidth(),
        ) {
            when (currentTab) {
                "home" ->
                    HomeView(
                        viewModel = viewModel,
                        connectionState = connectionState,
                        onConnect = onConnect,
                        onDisconnect = onDisconnect,
                        onPause = onPause,
                        onResume = onResume,
                        onOpenSettings = onOpenSettings,
                        onGoToConfig = { currentTab = "config" },
                    )
                "config" -> ConfigView(
                    viewModel = viewModel,
                    context = context,
                    isConnected = isConnected,
                )
            }
        }

        Column(
            modifier = Modifier
                .fillMaxWidth()
                .background(T.surface),
        ) {
            MaviDivider()
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .windowInsetsPadding(WindowInsets.safeDrawing.only(WindowInsetsSides.Bottom))
                    .height(64.dp)
                    .padding(horizontal = 12.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                BottomTab(
                    label = "Connection",
                    icon = Icons.Outlined.Link,
                    selected = currentTab == "home",
                    onClick = { currentTab = "home" },
                )
                BottomTab(
                    label = "Configuration",
                    icon = Icons.Outlined.Tune,
                    selected = currentTab == "config",
                    onClick = { currentTab = "config" },
                )
            }
        }
    }
}

@Composable
private fun RowScope.BottomTab(
    label: String,
    icon: ImageVector,
    selected: Boolean,
    onClick: () -> Unit,
) {
    val T = MaviTheme.colors
    Column(
        horizontalAlignment = Alignment.CenterHorizontally,
        modifier = Modifier
            .weight(1f)
            .selectable(
                selected = selected,
                interactionSource = remember { MutableInteractionSource() },
                indication = null,
                role = Role.Tab,
                onClick = onClick,
            ),
    ) {
        Box(
            modifier = Modifier
                .clip(CircleShape)
                .background(if (selected) T.accentSoft else Color.Transparent)
                .padding(horizontal = 18.dp, vertical = 4.dp),
        ) {
            Icon(
                imageVector = icon,
                contentDescription = null,
                tint = if (selected) T.accent else T.mute,
                modifier = Modifier.size(20.dp),
            )
        }
        Spacer(modifier = Modifier.height(4.dp))
        Text(
            text = label,
            style = MaterialTheme.typography.labelSmall,
            color = if (selected) T.ink else T.mute,
        )
    }
}
