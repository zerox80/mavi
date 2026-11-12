package com.mavi.vpn

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.net.URLEncoder
import java.nio.charset.StandardCharsets

class EnrollmentLinkTest {
    private val ticket = "aB0_-" + "x".repeat(40)

    @Test
    fun parsesDeepLinkAndBuildsPanelRedeemUrl() {
        val route = EnrollmentDeepLink.parse(link(ticket, "https://panel.example/base/"))

        val request = (route as EnrollmentRoute.Valid).request
        assertEquals("https://panel.example/base/api/v1/enrollment/redeem", request.redeemUrl.toString())
        assertTrue(request.requestJson().contains(ticket))
        assertFalse(request.toString().contains(ticket))
        assertEquals(64, request.fingerprint.length)
        val duplicate =
            (EnrollmentDeepLink.parse(link(ticket, "https://panel.example/base/")) as EnrollmentRoute.Valid).request
        assertEquals(request.fingerprint, duplicate.fingerprint)
    }

    @Test
    fun leavesOAuthAndWebLinksForExistingRouting() {
        val links =
            listOf(
                "com.mavi.vpn://oauth/callback?code=value&state=value",
                "https://vpn.example.com/oauth/callback?code=value&state=value",
                "https://panel.example/enroll/$ticket",
            )
        for (link in links) {
            assertEquals(EnrollmentRoute.NotEnrollment, EnrollmentDeepLink.parse(link))
        }
    }

    @Test
    fun rejectsInvalidTicketsAndParameterLists() {
        val invalidTickets = listOf("a".repeat(31), "a".repeat(129), "a".repeat(31) + "+", "a".repeat(31) + "=")
        for (invalidTicket in invalidTickets) {
            assertTrue(EnrollmentDeepLink.parse(link(invalidTicket, PANEL)) is EnrollmentRoute.Invalid)
        }
        val encodedPanel = encode(PANEL)
        val queries =
            listOf(
                "ticket=$ticket",
                "panel=$encodedPanel",
                "ticket=$ticket&ticket=$ticket&panel=$encodedPanel",
                "ticket=$ticket&panel=$encodedPanel&panel=$encodedPanel",
                "ticket=$ticket&panel=$encodedPanel&other=value",
                "ticket=$ticket&&panel=$encodedPanel",
            )
        for (query in queries) {
            assertTrue(EnrollmentDeepLink.parse("mavivpn://enroll?$query") is EnrollmentRoute.Invalid)
        }
    }

    @Test
    fun panelRequiresHttpsExceptForExplicitLoopbackDevelopment() {
        val invalidPanels =
            listOf(
                "http://panel.example",
                "ftp://panel.example",
                "https://user@panel.example",
                "$PANEL?next=other",
                "$PANEL#fragment",
            )
        for (panel in invalidPanels) {
            assertTrue(EnrollmentDeepLink.parse(link(ticket, panel)) is EnrollmentRoute.Invalid)
        }
        for (panel in listOf("http://localhost:8080", "http://127.0.0.1:8080", "http://[::1]:8080")) {
            assertTrue(EnrollmentDeepLink.parse(link(ticket, panel)) is EnrollmentRoute.Valid)
        }
        assertTrue(EnrollmentDeepLink.parse(link(ticket, "http://localhost.evil.example")) is EnrollmentRoute.Invalid)
    }

    @Test
    fun rejectsWrongHostPathAndMalformedUris() {
        val deepLinks =
            listOf(
                "mavivpn://other?ticket=$ticket&panel=${encode(PANEL)}",
                "mavivpn://enroll/path?ticket=$ticket&panel=${encode(PANEL)}",
                "mavivpn://enroll:123?ticket=$ticket&panel=${encode(PANEL)}",
                "mavivpn://enroll?ticket=%zz&panel=${encode(PANEL)}",
            )
        for (deepLink in deepLinks) {
            assertTrue(EnrollmentDeepLink.parse(deepLink) is EnrollmentRoute.Invalid)
        }
    }

    private fun link(
        ticket: String,
        panel: String,
    ): String = "mavivpn://enroll?ticket=$ticket&panel=${encode(panel)}"

    private fun encode(value: String): String = URLEncoder.encode(value, StandardCharsets.UTF_8)

    private companion object {
        const val PANEL = "https://panel.example"
    }
}
