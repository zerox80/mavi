@file:Suppress("ktlint:standard:function-naming", "ktlint:standard:property-naming")

package com.mavi.vpn.ui.screens

import android.widget.Toast
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.mavi.vpn.MainActivity
import com.mavi.vpn.ui.components.MaviCard
import com.mavi.vpn.ui.components.MaviDivider
import com.mavi.vpn.ui.components.MaviTextField
import com.mavi.vpn.ui.components.NavRow
import com.mavi.vpn.ui.components.PrimaryButton
import com.mavi.vpn.ui.components.ScreenHeader
import com.mavi.vpn.ui.components.SectionLabel
import com.mavi.vpn.ui.components.SwitchRow
import com.mavi.vpn.ui.theme.MaviTheme
import com.mavi.vpn.viewmodel.VpnViewModel

@Composable
fun SettingsScreen(
    viewModel: VpnViewModel,
    onBack: (String, String, Boolean, Boolean, Boolean, Int) -> Unit,
    onOpenSplitTunneling: () -> Unit,
) {
    val context = LocalContext.current
    val T = MaviTheme.colors

    val initialMode by viewModel.splitMode.collectAsState()
    val initialSelection by viewModel.splitPackages.collectAsState()
    val initialCensorshipResistant by viewModel.censorshipResistant.collectAsState()
    val initialHttp3Framing by viewModel.http3Framing.collectAsState()
    val initialHttp2Framing by viewModel.http2Framing.collectAsState()
    val initialVpnMtu by viewModel.vpnMtu.collectAsState()

    var censorshipResistant by remember { mutableStateOf(initialCensorshipResistant) }
    var http3Framing by remember { mutableStateOf(initialHttp3Framing) }
    var http2Framing by remember { mutableStateOf(initialHttp2Framing) }
    var vpnMtuText by remember { mutableStateOf(if (initialVpnMtu > 0) initialVpnMtu.toString() else "") }

    fun parseValidatedMtu(): Int? {
        if (vpnMtuText.isBlank()) return 0
        val value = vpnMtuText.toIntOrNull() ?: return null
        if (value !in 1280..1360) {
            Toast.makeText(context, "VPN MTU must be between 1280 and 1360", Toast.LENGTH_SHORT).show()
            return null
        }
        return value
    }

    fun saveAndBack() {
        val mtu = parseValidatedMtu() ?: return
        onBack(
            initialMode,
            initialSelection,
            censorshipResistant,
            http3Framing,
            http2Framing,
            mtu,
        )
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(T.bg)
            .safeDrawingPadding()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 20.dp, vertical = 8.dp),
    ) {
        ScreenHeader(title = "Settings", onBack = { saveAndBack() })

        SectionLabel("Transport")
        MaviCard {
            SwitchRow(
                title = "Censorship-resistant mode",
                description = "Obfuscate traffic as HTTP/3 to bypass firewalls.",
                checked = censorshipResistant,
                onCheckedChange = {
                    censorshipResistant = it
                    if (it) {
                        http3Framing = true
                        http2Framing = false
                    }
                },
            )
            MaviDivider()
            SwitchRow(
                title = "HTTP/3 framing",
                description = "Tunnel packets inside HTTP/3 datagrams (RFC 9297).",
                checked = http3Framing,
                onCheckedChange = {
                    http3Framing = it
                    if (it) http2Framing = false
                    if (!it) censorshipResistant = false
                },
            )
            MaviDivider()
            SwitchRow(
                title = "HTTP/2 CONNECT-IP",
                description = "Tunnel over TLS/TCP using HTTP/2 and RFC 9297 capsules.",
                checked = http2Framing,
                onCheckedChange = {
                    http2Framing = it
                    if (it) {
                        censorshipResistant = false
                        http3Framing = false
                    }
                },
            )
        }

        Spacer(modifier = Modifier.height(24.dp))

        SectionLabel("VPN MTU")
        MaviCard {
            Column(modifier = Modifier.padding(16.dp)) {
                MaviTextField(
                    value = vpnMtuText,
                    onValueChange = { vpnMtuText = it.filter { c -> c.isDigit() } },
                    label = "Inner TUN MTU (1280–1360)",
                    placeholder = "1280",
                    modifier = Modifier.fillMaxWidth(),
                    mono = true,
                )
            }
        }
        SectionLabel(
            text = "Default: 1280. Must match the server.",
            modifier = Modifier.padding(top = 8.dp),
        )

        Spacer(modifier = Modifier.height(16.dp))

        SectionLabel("Device")
        MaviCard {
            NavRow(
                title = "Split tunneling",
                description = run {
                    val selectedCount = initialSelection.split(",").count { it.isNotBlank() }
                    val modeLabel =
                        if (initialMode == "include") "Only selected apps use VPN" else "Selected apps bypass VPN"
                    "$modeLabel · $selectedCount selected"
                },
                onClick = onOpenSplitTunneling,
            )
            MaviDivider()
            NavRow(
                title = "Battery optimization",
                description = "Ensure the VPN is not killed in the background.",
                onClick = { (context as? MainActivity)?.requestBatteryOptimizationIgnore() },
            )
        }

        Spacer(modifier = Modifier.height(24.dp))

        PrimaryButton(
            text = "Save & back",
            onClick = { saveAndBack() },
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(modifier = Modifier.height(16.dp))
    }
}
