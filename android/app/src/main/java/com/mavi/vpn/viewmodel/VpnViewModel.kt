package com.mavi.vpn.viewmodel

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.mavi.vpn.EnrollmentClient
import com.mavi.vpn.EnrollmentProfile
import com.mavi.vpn.EnrollmentRedeemResult
import com.mavi.vpn.EnrollmentRequest
import com.mavi.vpn.EnrollmentUiState
import com.mavi.vpn.MaviVpnService
import com.mavi.vpn.VpnConnectionState
import com.mavi.vpn.data.PrefsManager
import com.mavi.vpn.enrollmentImportAllowed
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

class VpnViewModel(
    application: Application,
) : AndroidViewModel(application) {
    private val prefs = PrefsManager(application)

    // UI State
    var serverIp = MutableStateFlow(prefs.savedIp)
    var serverPort = MutableStateFlow(prefs.savedPort)

    // In normal mode the credential is a user-entered preshared key; in Keycloak
    // mode it is the OAuth access token. They must live in separate slots so a
    // Keycloak login never overwrites the preshared key (and vice versa).
    // Historically both shared `savedToken`, which left a stale Keycloak token in
    // the preshared field after switching back to normal mode and broke Connect
    // until the app was reinstalled.
    var authToken =
        MutableStateFlow(
            if (prefs.savedUseKeycloak) {
                prefs.keycloak
                    .snapshot()
                    .tokens
                    ?.accessToken
                    .orEmpty()
            } else {
                ""
            },
        )
    var presharedKey =
        MutableStateFlow(
            run {
                val stored = prefs.savedPresharedKey
                when {
                    stored.isNotEmpty() -> stored
                    // Migrate older installs that kept the preshared key in
                    // `savedToken`. Skip when a Keycloak refresh token exists, because
                    // then `savedToken` holds a leftover access token, not a key.
                    !prefs.savedUseKeycloak && prefs.savedRefreshToken.isBlank() -> prefs.savedToken
                    else -> ""
                }
            },
        )
    var certPin = MutableStateFlow(prefs.savedPin)
    var echConfig = MutableStateFlow(prefs.savedEchConfig)

    // Keycloak state
    var useKeycloak = MutableStateFlow(prefs.savedUseKeycloak)
    var kcUrl = MutableStateFlow(prefs.savedKcUrl)
    var kcRealm = MutableStateFlow(prefs.savedKcRealm)
    var kcClientId = MutableStateFlow(prefs.savedKcClientId)

    // Settings state
    var splitMode = MutableStateFlow(prefs.savedSplitMode)
    var splitPackages = MutableStateFlow(prefs.savedSplitPackages)
    var censorshipResistant = MutableStateFlow(prefs.savedCensorshipResistant)
    var http3Framing = MutableStateFlow(prefs.savedHttp3Framing)
    var http2Framing = MutableStateFlow(prefs.savedHttp2Framing)
    var vpnMtu = MutableStateFlow(prefs.savedVpnMtu)

    private val _errorMessage = MutableStateFlow("")
    val errorMessage: StateFlow<String> = _errorMessage.asStateFlow()

    private val mutableEnrollmentState = MutableStateFlow<EnrollmentUiState>(EnrollmentUiState.Idle)
    internal val enrollmentState: StateFlow<EnrollmentUiState> = mutableEnrollmentState.asStateFlow()
    private val handledEnrollmentRequests = mutableSetOf<String>()

    val connectionState = MaviVpnService.connectionState
    val pausedUntil = MaviVpnService.pausedUntil

    init {
        // We only clear out token entirely if the token is completely
        // unusable and refresh fails, but for the viewModel startup,
        // we can just leave it. If it fails to refresh during connection,
        // MaviVpnService handles the clearing.

        // Persist a migrated preshared key (legacy installs kept it in the
        // `savedToken` slot) so the one-time fallback survives even if the user
        // never reconnects.
        if (prefs.savedPresharedKey.isEmpty() && presharedKey.value.isNotEmpty()) {
            prefs.savedPresharedKey = presharedKey.value
        }
    }

    fun updateErrorMessage(message: String) {
        _errorMessage.value = message
    }

    internal fun startEnrollment(request: EnrollmentRequest) {
        if (mutableEnrollmentState.value != EnrollmentUiState.Idle || !handledEnrollmentRequests.add(request.fingerprint)) {
            return
        }
        if (!enrollmentImportAllowed(connectionState.value)) {
            mutableEnrollmentState.value =
                EnrollmentUiState.Error(
                    "Disconnect the VPN before importing an enrollment profile. The ticket has not been redeemed.",
                )
            return
        }
        mutableEnrollmentState.value = EnrollmentUiState.Redeeming
        viewModelScope.launch {
            mutableEnrollmentState.value =
                when (val result = EnrollmentClient.redeem(request)) {
                    is EnrollmentRedeemResult.Success -> EnrollmentUiState.Confirmation(result.profile)
                    is EnrollmentRedeemResult.Failure -> EnrollmentUiState.Error(result.message)
                }
        }
    }

    internal fun showEnrollmentError(message: String) {
        if (mutableEnrollmentState.value == EnrollmentUiState.Idle) {
            mutableEnrollmentState.value = EnrollmentUiState.Error(message)
        }
    }

    internal fun cancelEnrollment() {
        if (mutableEnrollmentState.value is EnrollmentUiState.Confirmation) {
            mutableEnrollmentState.value =
                EnrollmentUiState.Error(
                    "The profile was not imported. The ticket has already been redeemed; request a new enrollment link.",
                )
        }
    }

    internal fun confirmEnrollmentImport() {
        val confirmation = mutableEnrollmentState.value as? EnrollmentUiState.Confirmation ?: return
        val error = importEnrollment(confirmation.profile)
        mutableEnrollmentState.value =
            if (error == null) {
                EnrollmentUiState.Imported
            } else {
                EnrollmentUiState.Error("$error The ticket has already been redeemed; request a new link afterward.")
            }
    }

    internal fun dismissEnrollment() {
        if (mutableEnrollmentState.value is EnrollmentUiState.Error || mutableEnrollmentState.value == EnrollmentUiState.Imported) {
            mutableEnrollmentState.value = EnrollmentUiState.Idle
        }
    }

    internal fun importEnrollment(profile: EnrollmentProfile): String? {
        if (!enrollmentImportAllowed(connectionState.value)) {
            return "Disconnect the VPN before importing an enrollment profile."
        }

        prefs.importEnrollment(profile)
        serverIp.value = profile.endpoint.host
        serverPort.value = profile.endpoint.port.toString()
        certPin.value = profile.certPin
        echConfig.value = profile.echConfig.orEmpty()
        useKeycloak.value = true
        kcUrl.value = profile.keycloak.url
        kcRealm.value = profile.keycloak.realm
        kcClientId.value = profile.keycloak.clientId
        censorshipResistant.value = profile.transport.censorshipResistant
        http3Framing.value = profile.transport.http3Framing
        http2Framing.value = profile.transport.http2Framing
        vpnMtu.value = profile.vpnMtu
        reloadOAuthTokens()
        _errorMessage.value = ""
        return null
    }

    fun clearAuthToken() {
        authToken.value = ""
        prefs.keycloak.clear()
    }

    fun reloadOAuthTokens() {
        authToken.value =
            prefs.keycloak
                .snapshot()
                .tokens
                ?.accessToken
                .orEmpty()
    }

    fun hasSavedKeycloakRefreshToken(): Boolean =
        !prefs.keycloak
            .snapshot()
            .tokens
            ?.refreshToken
            .isNullOrBlank()

    fun isSavedKeycloakSessionInvalid(): Boolean = prefs.savedKeycloakSessionInvalid

    fun saveServerDetails() {
        prefs.savedIp = serverIp.value
        prefs.savedPort = serverPort.value
        prefs.savedPin = certPin.value
        prefs.savedEchConfig = echConfig.value
        prefs.savedPresharedKey = presharedKey.value
    }

    fun setKeycloakEnabled(enabled: Boolean) {
        if (enabled == useKeycloak.value) return
        if (enabled) {
            // Persist the preshared key before switching so a later Keycloak login
            // cannot clobber it.
            prefs.savedPresharedKey = presharedKey.value
        } else if (connectionState.value != VpnConnectionState.CONNECTED) {
            // Leaving Keycloak mode: drop the OAuth session so a stale access or
            // refresh token can't masquerade as a valid session (and skip the
            // interactive login) the next time Keycloak is enabled. Skip while a
            // tunnel is live so we don't pull the credential out from under the
            // running Keycloak session.
            clearAuthToken()
        }
        useKeycloak.value = enabled
        prefs.savedUseKeycloak = enabled
        _errorMessage.value = ""
    }

    fun saveKeycloakDetails() {
        prefs.keycloak.updateAuthority(kcUrl.value, kcRealm.value, kcClientId.value)
        reloadOAuthTokens()
        prefs.savedUseKeycloak = useKeycloak.value
    }

    fun saveSettings(
        mode: String,
        packages: String,
        crMode: Boolean,
        h3Mode: Boolean,
        h2Mode: Boolean,
        vpnMtuValue: Int,
    ) {
        val normalizedH2 = h2Mode
        val normalizedCr = crMode && !normalizedH2
        val normalizedH3 = (h3Mode || normalizedCr) && !normalizedH2
        splitMode.value = mode
        splitPackages.value = packages
        censorshipResistant.value = normalizedCr
        http3Framing.value = normalizedH3
        http2Framing.value = normalizedH2
        vpnMtu.value = vpnMtuValue

        prefs.savedSplitMode = mode
        prefs.savedSplitPackages = packages
        prefs.savedCensorshipResistant = normalizedCr
        prefs.savedHttp3Framing = normalizedH3
        prefs.savedHttp2Framing = normalizedH2
        prefs.savedVpnMtu = vpnMtuValue
    }

    fun saveSplitTunneling(
        mode: String,
        packages: String,
    ) {
        splitMode.value = mode
        splitPackages.value = packages
        prefs.savedSplitMode = mode
        prefs.savedSplitPackages = packages
    }
}
