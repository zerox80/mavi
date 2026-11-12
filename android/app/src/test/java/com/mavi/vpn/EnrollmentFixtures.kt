package com.mavi.vpn

import org.json.JSONObject

internal fun enrollmentProfileJson(): JSONObject =
    JSONObject(
        """
        {
          "schema": "mavi.connection.v1",
          "device_id": "0b858e3f-a9c5-4ae5-a859-b556e6ba70c0",
          "label": "Alice laptop",
          "endpoint": "vpn.example.com:10443",
          "cert_pin": "${"ab".repeat(32)}",
          "transport": "http3_censorship_resistant",
          "ech_config": "00ff",
          "vpn_mtu": 1280,
          "keycloak": {
            "url": "https://auth.example.com/",
            "realm": "mavi-vpn",
            "client_id": "mavi-client"
          }
        }
        """.trimIndent(),
    )

internal fun enrollmentResponseJson(): JSONObject = JSONObject().put("profile", enrollmentProfileJson())

internal fun enrollmentProfile(): EnrollmentProfile = EnrollmentProfile.parse(enrollmentResponseJson().toString())
