"""Contracts between the shipped realm, supported Keycloak and JWT consumers."""
import json
from pathlib import Path
import unittest


class KeycloakRealmTests(unittest.TestCase):
    def test_import_uses_supported_realm_signature_setting(self):
        path = Path(__file__).resolve().parents[2] / "backend/keycloak/mavi-vpn-realm.json"
        realm = json.loads(path.read_text())
        self.assertEqual(realm["defaultSignatureAlgorithm"], "RS256")
        self.assertNotIn("accessTokenSignatureAlgorithm", realm)
        self.assertNotIn("idTokenSignatureAlgorithm", realm)
        # An explicit empty list suppresses Keycloak's built-in roles/profile/email scopes.
        self.assertNotIn("clientScopes", realm)
        self.assertEqual(realm["realm"], "mavi-vpn")
        self.assertEqual(realm["users"], [])


if __name__ == "__main__":
    unittest.main()
