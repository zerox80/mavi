@file:Suppress("ktlint:standard:function-naming", "ktlint:standard:property-naming")

package com.mavi.vpn.ui.screens

import android.content.Context
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import com.mavi.vpn.OAuthHelper
import com.mavi.vpn.ui.components.MaviCard
import com.mavi.vpn.ui.components.MaviDivider
import com.mavi.vpn.ui.components.MaviTextField
import com.mavi.vpn.ui.components.PrimaryButton
import com.mavi.vpn.ui.components.SectionLabel
import com.mavi.vpn.ui.components.StatusLine
import com.mavi.vpn.ui.components.StatusTone
import com.mavi.vpn.ui.components.SwitchRow
import com.mavi.vpn.ui.theme.MaviShapes
import com.mavi.vpn.ui.theme.MaviTheme
import com.mavi.vpn.viewmodel.VpnViewModel

@Composable
fun ConfigView(
    viewModel: VpnViewModel,
    context: Context,
    isConnected: Boolean,
) {
    val T = MaviTheme.colors
    val serverIp by viewModel.serverIp.collectAsState()
    val serverPort by viewModel.serverPort.collectAsState()
    val authToken by viewModel.authToken.collectAsState()
    val presharedKey by viewModel.presharedKey.collectAsState()
    val certPin by viewModel.certPin.collectAsState()
    val echConfig by viewModel.echConfig.collectAsState()

    val useKeycloak by viewModel.useKeycloak.collectAsState()
    val kcUrl by viewModel.kcUrl.collectAsState()
    val kcRealm by viewModel.kcRealm.collectAsState()
    val kcClientId by viewModel.kcClientId.collectAsState()

    val errorMessage by viewModel.errorMessage.collectAsState()
    val hasKeycloakRefreshToken = viewModel.hasSavedKeycloakRefreshToken()
    val keycloakSessionInvalid = viewModel.isSavedKeycloakSessionInvalid()
    val hasKeycloakToken = useKeycloak && authToken.isNotEmpty()
    val hasKeycloakSession = hasKeycloakToken || hasKeycloakRefreshToken
    val keycloakTokenUsableLocally = hasKeycloakToken && OAuthHelper.isAccessTokenUsable(authToken, skewSeconds = 0)

    Column(
        modifier =
            Modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 20.dp, vertical = 16.dp),
    ) {
        Text(
            text = "Configuration",
            style = MaterialTheme.typography.titleMedium,
            color = T.ink,
            modifier = Modifier.padding(start = 4.dp, top = 4.dp, bottom = 20.dp),
        )

        SectionLabel("Endpoint")
        MaviCard {
            Column(modifier = Modifier.padding(16.dp)) {
                MaviTextField(
                    value = serverIp,
                    onValueChange = { viewModel.serverIp.value = it },
                    label = "Server IP / Endpoint",
                    modifier = Modifier.fillMaxWidth(),
                    mono = true,
                )
                Spacer(modifier = Modifier.height(12.dp))
                MaviTextField(
                    value = serverPort,
                    onValueChange = { viewModel.serverPort.value = it },
                    label = "Port",
                    modifier = Modifier.fillMaxWidth(),
                    mono = true,
                )
                Spacer(modifier = Modifier.height(12.dp))
                MaviTextField(
                    value = certPin,
                    onValueChange = { viewModel.certPin.value = it },
                    label = "Certificate PIN (SHA256 Hex)",
                    modifier = Modifier.fillMaxWidth(),
                    mono = true,
                )
                Spacer(modifier = Modifier.height(12.dp))
                MaviTextField(
                    value = echConfig,
                    onValueChange = { viewModel.echConfig.value = it },
                    label = "ECH Config List (Hex) - Optional",
                    placeholder = "Enter to bypass SNI inspection",
                    modifier = Modifier.fillMaxWidth(),
                    mono = true,
                )
            }
        }

        Spacer(modifier = Modifier.height(24.dp))

        SectionLabel("Authentication")
        MaviCard {
            SwitchRow(
                title = "Keycloak authentication",
                description = "Sign in through your Keycloak server",
                checked = useKeycloak,
                onCheckedChange = { checked -> viewModel.setKeycloakEnabled(checked) },
            )
            MaviDivider()
            Column(modifier = Modifier.padding(16.dp)) {
                if (useKeycloak) {
                    MaviTextField(
                        value = kcUrl,
                        onValueChange = { viewModel.kcUrl.value = it },
                        label = "Keycloak Server URL",
                        placeholder = "https://auth.example.com",
                        modifier = Modifier.fillMaxWidth(),
                    )
                    Spacer(modifier = Modifier.height(12.dp))
                    MaviTextField(
                        value = kcRealm,
                        onValueChange = { viewModel.kcRealm.value = it },
                        label = "Realm",
                        modifier = Modifier.fillMaxWidth(),
                    )
                    Spacer(modifier = Modifier.height(12.dp))
                    MaviTextField(
                        value = kcClientId,
                        onValueChange = { viewModel.kcClientId.value = it },
                        label = "Client ID",
                        modifier = Modifier.fillMaxWidth(),
                    )

                    Spacer(modifier = Modifier.height(16.dp))

                    PrimaryButton(
                        text = "Login with Keycloak",
                        icon = Icons.Default.Lock,
                        modifier = Modifier.fillMaxWidth(),
                        onClick = {
                            val authConfigError = OAuthHelper.validateAuthConfiguration(kcUrl)
                            if (kcUrl.isBlank()) {
                                viewModel.updateErrorMessage("Please enter a Keycloak Server URL.")
                            } else if (authConfigError != null) {
                                viewModel.updateErrorMessage(authConfigError)
                            } else {
                                viewModel.saveKeycloakDetails()
                                viewModel.updateErrorMessage("")
                                if (!OAuthHelper.startAuth(context, kcUrl, kcRealm, kcClientId)) {
                                    viewModel.updateErrorMessage("Could not start Keycloak login.")
                                }
                            }
                        },
                    )

                    if (hasKeycloakSession || keycloakSessionInvalid) {
                        val statusText =
                            when {
                                keycloakSessionInvalid -> "Session expired - login required"
                                keycloakTokenUsableLocally -> "Authenticated locally"
                                hasKeycloakRefreshToken -> "Access token will refresh"
                                else -> "Login required"
                            }
                        val sessionUsable =
                            !keycloakSessionInvalid && (keycloakTokenUsableLocally || hasKeycloakRefreshToken)

                        StatusLine(
                            label = statusText,
                            tone = if (sessionUsable) StatusTone.OK else StatusTone.PROGRESS,
                            modifier = Modifier.padding(top = 12.dp),
                        )
                    }
                } else {
                    MaviTextField(
                        value = presharedKey,
                        onValueChange = { viewModel.presharedKey.value = it },
                        label = "Preshared Key",
                        placeholder = "Pre-shared token",
                        modifier = Modifier.fillMaxWidth(),
                        visualTransformation = PasswordVisualTransformation(),
                    )
                }
            }
        }

        if (errorMessage.isNotEmpty()) {
            Spacer(modifier = Modifier.height(16.dp))
            Text(
                text = errorMessage,
                color = T.danger,
                style = MaterialTheme.typography.bodyMedium,
                modifier =
                    Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(MaviShapes.control))
                        .background(T.dangerSoft)
                        .padding(horizontal = 14.dp, vertical = 12.dp),
            )
        }
    }
}
