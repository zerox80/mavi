package com.mavi.vpn.data

import com.mavi.vpn.KeycloakAuthority
import com.mavi.vpn.OAuthTokens
import com.mavi.vpn.enrollmentProfile
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class EnrollmentImportTest {
    private val memory = MemoryPreferences()
    private val prefs = PrefsManager(memory.prefs, memory.secrets)

    @Test
    fun importsCompleteProfileWithoutChangingPskOrSplitTunneling() {
        prefs.savedPresharedKey = "keep-this-key"
        prefs.savedSplitMode = "include"
        prefs.savedSplitPackages = "com.example.one,com.example.two"
        val sessions = KeycloakSessionPreferences(memory.prefs, memory.secrets)
        val oldAuthority = KeycloakAuthority.from("https://old.example.com", "old-realm", "old-client")!!
        sessions.updateAuthority(oldAuthority.baseUrl, oldAuthority.realm, oldAuthority.clientId)
        assertTrue(sessions.beginLogin(oldAuthority, "state", "verifier"))
        val pending = sessions.consumeLogin("state")!!
        assertTrue(sessions.replace(pending.snapshot, OAuthTokens("old-access", "old-refresh"), false))
        assertNotNull(sessions.snapshot().tokens)

        val profile = enrollmentProfile()
        prefs.importEnrollment(profile)

        assertEquals("vpn.example.com", prefs.savedIp)
        assertEquals("10443", prefs.savedPort)
        assertEquals(profile.certPin, prefs.savedPin)
        assertEquals("00ff", prefs.savedEchConfig)
        assertTrue(prefs.savedUseKeycloak)
        assertEquals("https://auth.example.com", prefs.savedKcUrl)
        assertEquals("mavi-vpn", prefs.savedKcRealm)
        assertEquals("mavi-client", prefs.savedKcClientId)
        assertTrue(prefs.savedCensorshipResistant)
        assertTrue(prefs.savedHttp3Framing)
        assertFalse(prefs.savedHttp2Framing)
        assertEquals(1280, prefs.savedVpnMtu)
        assertEquals("include", prefs.savedSplitMode)
        assertEquals("com.example.one,com.example.two", prefs.savedSplitPackages)
        assertEquals("keep-this-key", prefs.savedPresharedKey)
        assertNull(sessions.snapshot().tokens)
    }
}
