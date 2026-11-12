@file:Suppress("ktlint:standard:function-naming", "ktlint:standard:property-naming")

package com.mavi.vpn.ui.screens

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.mavi.vpn.VpnConnectionState
import com.mavi.vpn.formatPauseRemaining
import com.mavi.vpn.ui.components.DetailRow
import com.mavi.vpn.ui.components.MaviCard
import com.mavi.vpn.ui.components.MaviDivider
import com.mavi.vpn.ui.components.NavRow
import com.mavi.vpn.ui.components.PrimaryButton
import com.mavi.vpn.ui.components.SecondaryButton
import com.mavi.vpn.ui.components.StatusPill
import com.mavi.vpn.ui.components.StatusTone
import com.mavi.vpn.ui.theme.MaviTheme
import com.mavi.vpn.viewmodel.VpnViewModel

@Composable
fun HomeView(
    viewModel: VpnViewModel,
    connectionState: VpnConnectionState,
    onConnect: (String, String, String, String) -> Unit,
    onDisconnect: () -> Unit,
    onPause: (Int) -> Unit,
    onResume: () -> Unit,
    onOpenSettings: () -> Unit,
    onGoToConfig: () -> Unit,
) {
    val T = MaviTheme.colors
    val serverIp by viewModel.serverIp.collectAsState()
    val serverPort by viewModel.serverPort.collectAsState()
    val authToken by viewModel.authToken.collectAsState()
    val presharedKey by viewModel.presharedKey.collectAsState()
    val certPin by viewModel.certPin.collectAsState()
    val useKeycloak by viewModel.useKeycloak.collectAsState()
    val kcUrl by viewModel.kcUrl.collectAsState()
    val pausedUntil by viewModel.pausedUntil.collectAsState()
    val censorshipResistant by viewModel.censorshipResistant.collectAsState()
    val http3Framing by viewModel.http3Framing.collectAsState()
    val http2Framing by viewModel.http2Framing.collectAsState()
    val hasKeycloakRefreshToken = viewModel.hasSavedKeycloakRefreshToken()

    val isConnected = connectionState == VpnConnectionState.CONNECTED
    val isConnecting = connectionState == VpnConnectionState.CONNECTING ||
        connectionState == VpnConnectionState.RECONNECTING
    val isPaused = connectionState == VpnConnectionState.PAUSED
    val pauseRemaining = rememberPauseRemainingMillis(if (isPaused) pausedUntil else 0L)
    val statusLabel = when (connectionState) {
        VpnConnectionState.STOPPED -> "Not connected"
        VpnConnectionState.CONNECTING -> "Connecting"
        VpnConnectionState.CONNECTED -> "Connected"
        VpnConnectionState.RECONNECTING -> "Reconnecting"
        VpnConnectionState.PAUSED -> "Paused"
    }
    val statusTone = when {
        isConnected -> StatusTone.OK
        isConnecting || isPaused -> StatusTone.PROGRESS
        else -> StatusTone.NEUTRAL
    }
    val statusDetail = when {
        isConnected -> "Traffic is routed through this connection"
        isConnecting -> "Establishing a secure connection…"
        isPaused && pauseRemaining == 0L -> "Waiting to resume"
        isPaused -> "Resumes in ${formatPauseRemaining(pauseRemaining)}"
        else -> "Ready to connect"
    }
    val transport = when {
        http2Framing -> "HTTP/2 CONNECT-IP"
        http3Framing || censorshipResistant -> "HTTP/3"
        else -> "Raw QUIC"
    }

    val onPrimaryAction: () -> Unit = {
        if (isPaused) {
            onResume()
        } else if (connectionState.canStop) {
            onDisconnect()
        } else {
            viewModel.updateErrorMessage("")
            if (serverIp.isEmpty()) {
                viewModel.updateErrorMessage("Please enter a server endpoint in Config.")
                onGoToConfig()
            } else if (useKeycloak) {
                val hasKeycloakSession = authToken.isNotEmpty() || hasKeycloakRefreshToken
                if (kcUrl.isEmpty() || !hasKeycloakSession) {
                    viewModel.updateErrorMessage("Please login with Keycloak first in Config.")
                    onGoToConfig()
                } else {
                    viewModel.saveKeycloakDetails()
                    onConnect(serverIp, serverPort, authToken, certPin)
                }
            } else {
                if (presharedKey.isEmpty()) {
                    viewModel.updateErrorMessage("Please enter a Preshared Key in Config.")
                    onGoToConfig()
                } else {
                    viewModel.saveServerDetails()
                    onConnect(serverIp, serverPort, presharedKey, certPin)
                }
            }
        }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 20.dp, vertical = 16.dp),
    ) {
        Text(
            text = "Mavi VPN",
            style = MaterialTheme.typography.titleMedium,
            color = T.ink,
            modifier = Modifier.padding(start = 4.dp, top = 4.dp, bottom = 20.dp),
        )

        MaviCard {
            Column(modifier = Modifier.padding(horizontal = 16.dp, vertical = 20.dp)) {
                StatusPill(label = statusLabel, tone = statusTone, pulsing = isConnecting)
                Spacer(modifier = Modifier.height(14.dp))
                Text(
                    text = serverIp.ifEmpty { "No endpoint configured" },
                    style = MaterialTheme.typography.headlineSmall,
                    color = T.ink,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Spacer(modifier = Modifier.height(4.dp))
                Text(text = statusDetail, style = MaterialTheme.typography.bodyMedium, color = T.mute)
            }
            MaviDivider()
            DetailRow(label = "Port", value = serverPort.ifEmpty { "—" }, mono = true)
            MaviDivider()
            DetailRow(label = "Authentication", value = if (useKeycloak) "Keycloak" else "Pre-shared key")
            MaviDivider()
            DetailRow(label = "Protocol", value = if (http2Framing) "TCP · TLS 1.3" else "QUIC · TLS 1.3")
            MaviDivider()
            DetailRow(label = "Transport", value = transport)
        }

        Spacer(modifier = Modifier.height(16.dp))

        val actionLabel = when {
            isConnected -> "Disconnect"
            isConnecting -> "Cancel"
            isPaused -> "Resume now"
            else -> "Connect"
        }
        // Stopping is the quiet action; starting or resuming is the primary one.
        if (isConnected || isConnecting) {
            SecondaryButton(text = actionLabel, onClick = onPrimaryAction, modifier = Modifier.fillMaxWidth())
        } else {
            PrimaryButton(text = actionLabel, onClick = onPrimaryAction, modifier = Modifier.fillMaxWidth())
        }

        if (connectionState.canPause) {
            Spacer(modifier = Modifier.height(20.dp))
            PauseOptions(onPause = onPause)
        } else if (isPaused) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(top = 8.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Text("Android may delay automatic resume.", fontSize = 12.sp, color = T.mute)
                TextButton(onClick = onDisconnect) {
                    Text("Disconnect", style = MaterialTheme.typography.labelLarge, color = T.ink2)
                }
            }
        }

        Spacer(modifier = Modifier.height(24.dp))

        MaviCard {
            NavRow(
                title = "Settings",
                description = "Transport, MTU and split tunneling",
                onClick = onOpenSettings,
            )
        }
    }
}
