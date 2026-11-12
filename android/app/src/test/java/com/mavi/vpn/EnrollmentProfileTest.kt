package com.mavi.vpn

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

class EnrollmentProfileTest {
    @Test
    fun parsesAndNormalizesValidProfile() {
        val profile = enrollmentProfile()

        assertEquals("mavi.connection.v1", profile.schema)
        assertEquals("Alice laptop", profile.label)
        assertEquals("vpn.example.com", profile.endpoint.host)
        assertEquals(10443, profile.endpoint.port)
        assertEquals("https://auth.example.com", profile.keycloak.url)
        assertEquals("00ff", profile.echConfig)
        assertTrue(profile.transport.http3Framing)
        assertTrue(profile.transport.censorshipResistant)
        assertFalse(profile.transport.http2Framing)

        val withoutEch = enrollmentProfileJson().put("ech_config", JSONObject.NULL)
        assertNull(EnrollmentProfile.parse(JSONObject().put("profile", withoutEch).toString()).echConfig)
    }

    @Test
    fun rejectsUnknownAndCredentialFieldsAtEveryLevel() {
        for (field in listOf("token", "access_token", "refresh_token", "preshared_key", "ticket", "unexpected")) {
            assertInvalid(enrollmentResponseJson().put(field, "do-not-import"))
            val withProfileField = enrollmentResponseJson()
            withProfileField.getJSONObject("profile").put(field, "do-not-import")
            assertInvalid(withProfileField)
            val withKeycloakField = enrollmentResponseJson()
            withKeycloakField
                .getJSONObject("profile")
                .getJSONObject("keycloak")
                .put(field, "do-not-import")
            assertInvalid(withKeycloakField)
        }
    }

    @Test
    fun validationErrorsNeverContainInjectedCredentials() {
        val secret = "secret-that-must-not-leak"
        val response = enrollmentResponseJson()
        response.getJSONObject("profile").put("refresh_token", secret)

        val error =
            assertThrows(EnrollmentValidationException::class.java) {
                EnrollmentProfile.parse(response.toString())
            }
        assertFalse(error.message.orEmpty().contains(secret))
    }

    @Test
    fun rejectsWrongSchemaMtuAndNumericCoercion() {
        val wrongSchema = enrollmentResponseJson()
        wrongSchema.getJSONObject("profile").put("schema", "mavi.connection.v2")
        assertInvalid(wrongSchema)

        for (mtu in listOf(0, 1279, 1360, "1280", JSONObject.NULL)) {
            val response = enrollmentResponseJson()
            response.getJSONObject("profile").put("vpn_mtu", mtu)
            assertInvalid(response)
        }
        val floatingPoint = enrollmentResponseJson().toString().replace("\"vpn_mtu\":1280", "\"vpn_mtu\":1280.0")
        assertThrows(EnrollmentValidationException::class.java) {
            EnrollmentProfile.parse(floatingPoint)
        }
    }

    @Test
    fun acceptsHostnameIpv4AndBracketedIpv6Endpoints() {
        for (endpoint in listOf("vpn.example.com:10443", "192.0.2.10:443", "[2001:db8::1]:65535")) {
            val response = enrollmentResponseJson()
            response.getJSONObject("profile").put("endpoint", endpoint)
            val parsed = EnrollmentProfile.parse(response.toString())
            assertEquals(endpoint, parsed.endpoint.raw)
        }
        assertEquals("2001:db8::1", EnrollmentEndpoint.parse("[2001:db8::1]:443")?.host)
    }

    @Test
    fun rejectsAmbiguousOrInvalidEndpoints() {
        val invalid =
            listOf(
                "vpn.example.com",
                "vpn.example.com:0",
                "vpn.example.com:65536",
                "https://vpn.example.com:443",
                "user@vpn.example.com:443",
                "2001:db8::1:443",
                "[2001:db8::1]443",
                "[not-an-ipv6]:443",
                "999.999.999.999:443",
                "bad..hostname:443",
                "-bad.example:443",
                "vpn.example.com:abc",
            )
        for (endpoint in invalid) {
            val response = enrollmentResponseJson()
            response.getJSONObject("profile").put("endpoint", endpoint)
            assertInvalid(response)
        }
    }

    @Test
    fun validatesPinListsAndEchLimits() {
        val pins = List(8) { index -> "%02x".format(index).repeat(32) }
        for (count in 1..8) {
            val response = enrollmentResponseJson()
            response.getJSONObject("profile").put("cert_pin", pins.take(count).joinToString(","))
            EnrollmentProfile.parse(response.toString())
        }
        val tooManyPins = (pins + "cd".repeat(32)).joinToString(",")
        for (pin in listOf("", "ab", "z".repeat(64), "${"ab".repeat(32)},", tooManyPins)) {
            val response = enrollmentResponseJson()
            response.getJSONObject("profile").put("cert_pin", pin)
            assertInvalid(response)
        }
        for (ech in listOf("", "0", "zz", "00".repeat(8193))) {
            val response = enrollmentResponseJson()
            response.getJSONObject("profile").put("ech_config", ech)
            assertInvalid(response)
        }
    }

    @Test
    fun transportMappingIsExclusive() {
        val expected =
            mapOf(
                "quic" to Triple(false, false, false),
                "http3" to Triple(true, false, false),
                "http3_censorship_resistant" to Triple(true, false, true),
                "http2" to Triple(false, true, false),
            )
        for ((wireName, settings) in expected) {
            val response = enrollmentResponseJson()
            response.getJSONObject("profile").put("transport", wireName)
            val transport = EnrollmentProfile.parse(response.toString()).transport
            assertEquals(settings.first, transport.http3Framing)
            assertEquals(settings.second, transport.http2Framing)
            assertEquals(settings.third, transport.censorshipResistant)
        }
    }

    @Test
    fun keycloakRequiresSafeIdentifiersAndHttpsExceptLoopback() {
        for (url in listOf("http://auth.example.com", "https://user@auth.example.com", "https://auth.example.com?x=1")) {
            val response = enrollmentResponseJson()
            response.getJSONObject("profile").getJSONObject("keycloak").put("url", url)
            assertInvalid(response)
        }
        for (field in listOf("realm", "client_id")) {
            val response = enrollmentResponseJson()
            response.getJSONObject("profile").getJSONObject("keycloak").put(field, "other/realm")
            assertInvalid(response)
        }
        val loopback = enrollmentResponseJson()
        loopback.getJSONObject("profile").getJSONObject("keycloak").put("url", "http://127.0.0.1:8080")
        EnrollmentProfile.parse(loopback.toString())
    }

    @Test
    fun importIsBlockedForEveryActiveSessionState() {
        assertTrue(enrollmentImportAllowed(VpnConnectionState.STOPPED))
        for (state in VpnConnectionState.entries - VpnConnectionState.STOPPED) {
            assertFalse(enrollmentImportAllowed(state))
        }
    }

    private fun assertInvalid(response: JSONObject) {
        try {
            EnrollmentProfile.parse(response.toString())
            fail("Expected invalid profile: $response")
        } catch (_: EnrollmentValidationException) {
            // Expected.
        }
    }
}
