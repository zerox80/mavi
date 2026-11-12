package com.mavi.vpn

import android.os.Looper
import com.mavi.vpn.viewmodel.VpnViewModel
import okhttp3.HttpUrl.Companion.toHttpUrl
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [31], manifest = Config.NONE)
class VpnEnrollmentRetryTest {
    @Test
    fun retriesRateLimitedTicketAndDeduplicatesAfterRedemption() {
        val model = VpnViewModel(RuntimeEnvironment.getApplication())
        val request = EnrollmentRequest("https://panel.example/api/v1/enrollment/redeem".toHttpUrl(), "a".repeat(43))
        var attempts = 0
        val redeem: suspend (EnrollmentRequest) -> EnrollmentRedeemResult = {
            attempts++
            if (attempts == 1) {
                EnrollmentRedeemResult.Failure("Wait before trying again", canRetry = true)
            } else {
                EnrollmentRedeemResult.Success(enrollmentProfile())
            }
        }

        model.startEnrollment(request, redeem)
        shadowOf(Looper.getMainLooper()).idle()
        assertTrue(model.enrollmentState.value is EnrollmentUiState.Error)
        model.dismissEnrollment()

        model.startEnrollment(request, redeem)
        shadowOf(Looper.getMainLooper()).idle()
        assertTrue(model.enrollmentState.value is EnrollmentUiState.Confirmation)
        model.cancelEnrollment()
        model.dismissEnrollment()

        model.startEnrollment(request, redeem)
        shadowOf(Looper.getMainLooper()).idle()
        assertEquals(2, attempts)
        assertEquals(EnrollmentUiState.Idle, model.enrollmentState.value)
    }
}
