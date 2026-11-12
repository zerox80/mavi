package com.mavi.vpn

import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import org.json.JSONObject
import java.net.URI
import java.net.URLDecoder
import java.nio.charset.StandardCharsets
import java.security.MessageDigest

internal class EnrollmentRequest(
    val redeemUrl: HttpUrl,
    private val ticket: String,
) {
    val fingerprint: String = sha256("$redeemUrl\n$ticket")

    fun requestJson(): String =
        JSONObject()
            .put("ticket", ticket)
            .toString()

    override fun toString(): String = "EnrollmentRequest(redeemUrl=$redeemUrl, ticket=<redacted>)"

    private fun sha256(value: String): String =
        MessageDigest
            .getInstance("SHA-256")
            .digest(value.toByteArray(Charsets.UTF_8))
            .joinToString("") { "%02x".format(it) }
}

internal sealed interface EnrollmentRoute {
    data class Valid(
        val request: EnrollmentRequest,
    ) : EnrollmentRoute

    data class Invalid(
        val message: String,
    ) : EnrollmentRoute

    data object NotEnrollment : EnrollmentRoute
}

internal object EnrollmentDeepLink {
    private const val INVALID_LINK = "Invalid Mavi enrollment deep link"

    fun parse(link: String): EnrollmentRoute {
        val uri =
            try {
                URI(link)
            } catch (_: Exception) {
                return if (link.startsWith("mavivpn:", ignoreCase = true)) {
                    EnrollmentRoute.Invalid(INVALID_LINK)
                } else {
                    EnrollmentRoute.NotEnrollment
                }
            }
        if (!uri.scheme.equals("mavivpn", ignoreCase = true)) return EnrollmentRoute.NotEnrollment
        if (
            !uri.host.equals("enroll", ignoreCase = true) ||
            uri.rawUserInfo != null ||
            uri.port != -1 ||
            uri.rawPath.orEmpty().isNotEmpty() ||
            uri.rawFragment != null ||
            uri.rawQuery == null
        ) {
            return EnrollmentRoute.Invalid(INVALID_LINK)
        }

        var ticket: String? = null
        var panel: String? = null
        for (part in uri.rawQuery.split('&')) {
            val separator = part.indexOf('=')
            if (separator <= 0) return EnrollmentRoute.Invalid(INVALID_LINK)
            val name = decodeQueryComponent(part.substring(0, separator)) ?: return invalidLink()
            val value = decodeQueryComponent(part.substring(separator + 1)) ?: return invalidLink()
            when (name) {
                "ticket" -> if (ticket == null) ticket = value else return invalidLink()
                "panel" -> if (panel == null) panel = value else return invalidLink()
                else -> return invalidLink()
            }
        }

        val validTicket = ticket?.takeIf(::isValidTicket) ?: return EnrollmentRoute.Invalid("Invalid enrollment ticket")
        val panelBase = panel ?: return EnrollmentRoute.Invalid("Enrollment panel URL is missing")
        val redeemUrl = buildRedeemUrl(panelBase) ?: return EnrollmentRoute.Invalid("Invalid enrollment panel URL")
        return EnrollmentRoute.Valid(EnrollmentRequest(redeemUrl, validTicket))
    }

    private fun invalidLink(): EnrollmentRoute.Invalid = EnrollmentRoute.Invalid(INVALID_LINK)

    private fun decodeQueryComponent(value: String): String? =
        try {
            URLDecoder.decode(value, StandardCharsets.UTF_8)
        } catch (_: Exception) {
            null
        }

    private fun isValidTicket(ticket: String): Boolean =
        ticket.length in 32..128 &&
            ticket.all { it in 'a'..'z' || it in 'A'..'Z' || it in '0'..'9' || it == '-' || it == '_' }

    private fun buildRedeemUrl(panel: String): HttpUrl? {
        val uri =
            try {
                URI(panel)
            } catch (_: Exception) {
                return null
            }
        if (uri.rawUserInfo != null) return null
        val url = panel.toHttpUrlOrNull() ?: return null
        if (
            url.encodedQuery != null ||
            url.fragment != null ||
            url.username.isNotEmpty() ||
            url.password.isNotEmpty()
        ) {
            return null
        }
        val loopback =
            url.host
                .removePrefix("[")
                .removeSuffix("]")
                .lowercase()
        if (url.scheme != "https" && !(url.scheme == "http" && loopback in setOf("localhost", "127.0.0.1", "::1"))) {
            return null
        }
        val basePath = url.encodedPath.trimEnd('/')
        return url
            .newBuilder()
            .encodedPath("$basePath/api/v1/enrollment/redeem")
            .query(null)
            .fragment(null)
            .build()
    }
}
