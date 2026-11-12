package com.mavi.vpn

import org.json.JSONObject
import java.net.InetAddress

internal const val ENROLLMENT_SCHEMA = "mavi.connection.v1"
internal const val ENROLLMENT_VPN_MTU = 1280

internal class EnrollmentValidationException(
    message: String,
) : Exception(message)

internal enum class EnrollmentTransport(
    val wireName: String,
    val http3Framing: Boolean,
    val http2Framing: Boolean,
    val censorshipResistant: Boolean,
) {
    QUIC("quic", false, false, false),
    HTTP3("http3", true, false, false),
    HTTP3_CENSORSHIP_RESISTANT("http3_censorship_resistant", true, false, true),
    HTTP2("http2", false, true, false),
    ;

    companion object {
        fun fromWireName(value: String): EnrollmentTransport? = entries.firstOrNull { it.wireName == value }
    }
}

internal data class EnrollmentKeycloak(
    val url: String,
    val realm: String,
    val clientId: String,
)

internal data class EnrollmentEndpoint(
    val raw: String,
    val host: String,
    val port: Int,
) {
    override fun toString(): String = raw

    companion object {
        fun parse(endpoint: String): EnrollmentEndpoint? {
            if (
                endpoint.isEmpty() ||
                endpoint.length > 255 ||
                endpoint.any { it.isWhitespace() || it.code <= 0x20 || it in "/@?#" }
            ) {
                return null
            }

            val host: String
            val portText: String
            if (endpoint.startsWith("[")) {
                val closingBracket = endpoint.indexOf(']')
                if (closingBracket <= 1 || !endpoint.substring(closingBracket + 1).startsWith(":")) {
                    return null
                }
                host = endpoint.substring(1, closingBracket)
                portText = endpoint.substring(closingBracket + 2)
                if (!isValidIpv6(host)) return null
            } else {
                if (endpoint.count { it == ':' } != 1) return null
                host = endpoint.substringBefore(':')
                portText = endpoint.substringAfter(':')
                if (!isValidHostnameOrIpv4(host)) return null
            }

            if (host.length > 253 || portText.isEmpty() || !portText.all { it in '0'..'9' }) return null
            val port = portText.toIntOrNull()?.takeIf { it in 1..65535 } ?: return null
            return EnrollmentEndpoint(endpoint, host, port)
        }

        private fun isValidHostnameOrIpv4(host: String): Boolean {
            if (host.isEmpty() || host.length > 253) return false
            if (host.all { it in '0'..'9' || it == '.' } && '.' in host) return isValidIpv4(host)
            return host.split('.').all { label ->
                label.length in 1..63 &&
                    label.first().isAsciiLetterOrDigit() &&
                    label.last().isAsciiLetterOrDigit() &&
                    label.all { it.isAsciiLetterOrDigit() || it == '-' }
            }
        }

        private fun isValidIpv4(host: String): Boolean {
            val parts = host.split('.')
            return parts.size == 4 &&
                parts.all { part ->
                    part.isNotEmpty() &&
                        part.length <= 3 &&
                        part.all { it in '0'..'9' } &&
                        (part == "0" || !part.startsWith('0')) &&
                        (part.toIntOrNull() ?: -1) in 0..255
                }
        }

        private fun isValidIpv6(host: String): Boolean {
            if (
                host.count { it == ':' } < 2 ||
                !host.all { it in '0'..'9' || it in 'a'..'f' || it in 'A'..'F' || it == ':' || it == '.' }
            ) {
                return false
            }
            return try {
                InetAddress.getByName(host)
                true
            } catch (_: Exception) {
                false
            }
        }

        private fun Char.isAsciiLetterOrDigit(): Boolean = this in 'a'..'z' || this in 'A'..'Z' || this in '0'..'9'
    }
}

internal data class EnrollmentProfile(
    val schema: String,
    val deviceId: String,
    val label: String,
    val endpoint: EnrollmentEndpoint,
    val certPin: String,
    val transport: EnrollmentTransport,
    val echConfig: String?,
    val vpnMtu: Int,
    val keycloak: EnrollmentKeycloak,
) {
    companion object {
        private val responseFields = setOf("profile")
        private val profileFields =
            setOf(
                "schema",
                "device_id",
                "label",
                "endpoint",
                "cert_pin",
                "transport",
                "ech_config",
                "vpn_mtu",
                "keycloak",
            )
        private val requiredProfileFields = profileFields - "ech_config"
        private val keycloakFields = setOf("url", "realm", "client_id")

        fun parse(body: String): EnrollmentProfile {
            try {
                val response = JSONObject(body)
                requireFields(response, responseFields, responseFields)
                val profile = response.getJSONObject("profile")
                requireFields(profile, profileFields, requiredProfileFields)

                val schema = profile.getString("schema")
                requireValue(schema == ENROLLMENT_SCHEMA)
                val deviceId = profile.getString("device_id")
                requireIdentifier(deviceId)
                val label = profile.getString("label")
                requireValue(
                    label.isNotBlank() &&
                        label.codePointCount(0, label.length) <= 80 &&
                        !label.any { it.isISOControl() },
                )
                val endpoint = EnrollmentEndpoint.parse(profile.getString("endpoint"))
                requireValue(endpoint != null)
                val certPin = profile.getString("cert_pin")
                requireValidPins(certPin)
                val transport = EnrollmentTransport.fromWireName(profile.getString("transport"))
                requireValue(transport != null)
                val mtu = profile.get("vpn_mtu")
                requireValue(mtu is Int && mtu == ENROLLMENT_VPN_MTU)

                val keycloakJson = profile.getJSONObject("keycloak")
                requireFields(keycloakJson, keycloakFields, keycloakFields)
                val keycloakUrl = keycloakJson.getString("url")
                val realm = keycloakJson.getString("realm")
                val clientId = keycloakJson.getString("client_id")
                requireIdentifier(realm)
                requireIdentifier(clientId)
                val authority = KeycloakAuthority.from(keycloakUrl, realm, clientId)
                requireValue(authority != null)

                val echValue = profile.opt("ech_config")
                val echConfig =
                    if (echValue == null || echValue == JSONObject.NULL) {
                        null
                    } else {
                        if (echValue !is String) throw EnrollmentValidationException("Invalid enrollment profile")
                        requireValidEch(echValue)
                        echValue
                    }

                return EnrollmentProfile(
                    schema = schema,
                    deviceId = deviceId,
                    label = label,
                    endpoint = requireNotNull(endpoint),
                    certPin = certPin,
                    transport = requireNotNull(transport),
                    echConfig = echConfig,
                    vpnMtu = ENROLLMENT_VPN_MTU,
                    keycloak =
                        EnrollmentKeycloak(
                            url = OAuthConfiguration.normalizeKeycloakBaseUrl(keycloakUrl),
                            realm = realm,
                            clientId = clientId,
                        ),
                )
            } catch (_: Exception) {
                throw EnrollmentValidationException("Enrollment server returned an invalid profile")
            }
        }

        private fun requireFields(
            json: JSONObject,
            allowed: Set<String>,
            required: Set<String>,
        ) {
            val actual = json.keys().asSequence().toSet()
            requireValue(actual.all { it in allowed } && actual.containsAll(required))
        }

        private fun requireIdentifier(value: String) {
            requireValue(
                value.length in 1..64 &&
                    value.all { it.isAsciiLetterOrDigit() || it == '-' || it == '_' || it == '.' },
            )
        }

        private fun requireValidPins(value: String) {
            val pins = value.split(',')
            requireValue(
                pins.size in 1..8 &&
                    pins.all { pin ->
                        val trimmed = pin.trim()
                        trimmed.length == 64 && trimmed.all { it.isAsciiHexDigit() }
                    },
            )
        }

        private fun requireValidEch(value: String) {
            requireValue(value.length in 2..16384 && value.length % 2 == 0 && value.all { it.isAsciiHexDigit() })
        }

        private fun requireValue(valid: Boolean) {
            if (!valid) throw EnrollmentValidationException("Invalid enrollment profile")
        }

        private fun Char.isAsciiLetterOrDigit(): Boolean = this in 'a'..'z' || this in 'A'..'Z' || this in '0'..'9'

        private fun Char.isAsciiHexDigit(): Boolean = this in '0'..'9' || this in 'a'..'f' || this in 'A'..'F'
    }
}
