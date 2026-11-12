@file:Suppress("ktlint:standard:function-naming")

package com.mavi.vpn.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.DialogProperties
import com.mavi.vpn.EnrollmentProfile
import com.mavi.vpn.EnrollmentUiState
import com.mavi.vpn.viewmodel.VpnViewModel

@Composable
internal fun EnrollmentDialog(viewModel: VpnViewModel) {
    val state by viewModel.enrollmentState.collectAsState()
    when (val current = state) {
        EnrollmentUiState.Idle -> Unit
        EnrollmentUiState.Redeeming -> RedeemingDialog()
        is EnrollmentUiState.Confirmation ->
            ConfirmationDialog(
                profile = current.profile,
                onImport = viewModel::confirmEnrollmentImport,
                onCancel = viewModel::cancelEnrollment,
            )
        is EnrollmentUiState.Error ->
            MessageDialog(
                title = "Enrollment not imported",
                message = current.message,
                onDismiss = viewModel::dismissEnrollment,
            )
        EnrollmentUiState.Imported ->
            MessageDialog(
                title = "Enrollment imported",
                message = "The profile was imported. No VPN connection has been started. You can now connect with Keycloak.",
                onDismiss = viewModel::dismissEnrollment,
            )
    }
}

@Composable
private fun RedeemingDialog() {
    AlertDialog(
        onDismissRequest = {},
        properties = blockingDialogProperties(),
        title = { Text("Redeeming enrollment") },
        text = {
            Row(horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                CircularProgressIndicator(modifier = Modifier.width(24.dp).height(24.dp))
                Text("Contacting the panel and validating the profile. Keep this screen open.")
            }
        },
        confirmButton = {},
    )
}

@Composable
private fun ConfirmationDialog(
    profile: EnrollmentProfile,
    onImport: () -> Unit,
    onCancel: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = {},
        properties = blockingDialogProperties(),
        title = { Text("Confirm enrollment") },
        text = {
            Column(modifier = Modifier.verticalScroll(rememberScrollState())) {
                Text("Review these details before importing. The ticket has been redeemed and cannot be used again.")
                Spacer(modifier = Modifier.height(16.dp))
                SelectionContainer {
                    Column {
                        EnrollmentDetail("Label", profile.label)
                        EnrollmentDetail("Endpoint", profile.endpoint.raw)
                        EnrollmentDetail("Keycloak URL", profile.keycloak.url)
                        EnrollmentDetail("Transport", profile.transport.wireName)
                        EnrollmentDetail("VPN MTU", profile.vpnMtu.toString())
                        EnrollmentDetail("Certificate PIN", profile.certPin.replace(",", ",\n"), pin = true)
                    }
                }
            }
        },
        confirmButton = { Button(onClick = onImport) { Text("Import profile") } },
        dismissButton = { TextButton(onClick = onCancel) { Text("Cancel") } },
    )
}

@Composable
private fun EnrollmentDetail(
    label: String,
    value: String,
    pin: Boolean = false,
) {
    Column(modifier = Modifier.fillMaxWidth()) {
        Text(label, fontSize = 12.sp, color = MaterialTheme.colorScheme.primary, fontWeight = FontWeight.SemiBold)
        Text(
            value,
            fontSize = if (pin) 11.sp else 14.sp,
            fontFamily = if (pin) FontFamily.Monospace else FontFamily.Default,
        )
        Spacer(modifier = Modifier.height(12.dp))
    }
}

@Composable
private fun MessageDialog(
    title: String,
    message: String,
    onDismiss: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = {},
        properties = blockingDialogProperties(),
        title = { Text(title) },
        text = { Text(message) },
        confirmButton = { Button(onClick = onDismiss) { Text("OK") } },
    )
}

private fun blockingDialogProperties() = DialogProperties(dismissOnBackPress = false, dismissOnClickOutside = false)
