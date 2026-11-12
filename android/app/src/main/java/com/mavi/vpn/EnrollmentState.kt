package com.mavi.vpn

internal sealed interface EnrollmentUiState {
    data object Idle : EnrollmentUiState

    data object Redeeming : EnrollmentUiState

    data class Confirmation(
        val profile: EnrollmentProfile,
    ) : EnrollmentUiState

    data class Error(
        val message: String,
    ) : EnrollmentUiState

    data object Imported : EnrollmentUiState
}

internal fun enrollmentImportAllowed(state: VpnConnectionState): Boolean = state == VpnConnectionState.STOPPED
