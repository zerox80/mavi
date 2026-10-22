package com.mavi.vpn

import android.content.Intent
import android.net.VpnService
import android.util.Log
import com.mavi.vpn.data.PrefsManager

internal data class VpnStartRequest(
    val ip: String,
    val port: String,
    val token: String,
    val pin: String,
    val splitMode: String,
    val splitPackages: String,
)

internal fun resolveVpnStartRequest(
    intent: Intent?,
    prefs: PrefsManager,
): VpnStartRequest? =
    resolveVpnStartRequest(
        action = intent?.action,
        savedRequest = {
            Log.i("MaviVPN", "Service restarted by System. Reloading credentials...")
            savedVpnStartRequest(prefs)
        },
        connectRequest = { connectVpnStartRequest(requireNotNull(intent), prefs) },
    )

// System starts have no connection extras. Only an explicit CONNECT may
// replace saved preferences with values from an intent.
internal fun resolveVpnStartRequest(
    action: String?,
    savedRequest: () -> VpnStartRequest,
    connectRequest: () -> VpnStartRequest,
): VpnStartRequest? =
    when (action) {
        null, VpnService.SERVICE_INTERFACE -> savedRequest()
        "CONNECT" -> connectRequest()
        else -> null
    }

internal fun savedVpnStartRequest(prefs: PrefsManager): VpnStartRequest {
    // Normal mode keeps its credential in savedPresharedKey, Keycloak mode in
    // an authority-bound record. Pick the active mode so a system restart
    // reconnects with the matching credential.
    val token =
        if (prefs.savedUseKeycloak) {
            prefs.keycloak
                .snapshot()
                .tokens
                ?.accessToken
                .orEmpty()
        } else {
            prefs.savedPresharedKey
        }
    return VpnStartRequest(
        ip = prefs.savedIp,
        port = prefs.savedPort,
        token = token,
        pin = prefs.savedPin,
        splitMode = prefs.savedSplitMode,
        splitPackages = prefs.savedSplitPackages,
    )
}

private fun connectVpnStartRequest(
    intent: Intent,
    prefs: PrefsManager,
): VpnStartRequest {
    val ip = intent.getStringExtra("IP") ?: ""
    val port = intent.getStringExtra("PORT") ?: "10443"
    val token = intent.getStringExtra("TOKEN") ?: ""
    val pin = intent.getStringExtra("PIN") ?: ""
    val splitMode = intent.getStringExtra("SPLIT_MODE") ?: ""
    val splitPackages = intent.getStringExtra("SPLIT_PACKAGES") ?: ""

    prefs.savedIp = ip
    prefs.savedPort = port
    val resolvedToken: String
    if (prefs.savedUseKeycloak) {
        // An intent token has no proven issuer and must not seed OAuth storage.
        resolvedToken =
            prefs.keycloak
                .snapshot()
                .tokens
                ?.accessToken
                .orEmpty()
    } else {
        prefs.savedPresharedKey = token
        resolvedToken = token
    }
    prefs.savedPin = pin
    prefs.savedSplitMode = splitMode
    prefs.savedSplitPackages = splitPackages

    return VpnStartRequest(
        ip = ip,
        port = port,
        token = resolvedToken,
        pin = pin,
        splitMode = splitMode,
        splitPackages = splitPackages,
    )
}

internal fun vpnStartHasCredentials(
    prefs: PrefsManager,
    currentToken: String,
): Boolean =
    if (prefs.savedUseKeycloak) {
        prefs.keycloak.snapshot().tokens?.let {
            it.accessToken.isNotEmpty() || it.refreshToken.isNotBlank()
        } == true
    } else {
        currentToken.isNotEmpty()
    }
