"""The installer prints a usable admin URL without exposing its credential."""
from contextlib import redirect_stdout
import io
from pathlib import Path
import unittest

from scripts.server_installer.installer import print_keycloak_login, print_summary
from scripts.server_installer.config import CLASSIC, KEYCLOAK, InstallPlan, build_configuration


class KeycloakLoginOutputTests(unittest.TestCase):
    def test_exact_master_console_link_and_custom_account_are_visible(self):
        output = io.StringIO()
        with redirect_stdout(output):
            print_keycloak_login({'DOMAIN_NAME':'example.com', 'VPN_KEYCLOAK_URL':'https://auth.example.com/',
                                  'KEYCLOAK_ADMIN_USER':'operator', 'KEYCLOAK_ADMIN_PASS':'do-not-print-this'})
        self.assertIn('https://auth.example.com/admin/master/console/', output.getvalue())
        self.assertIn('operator (master realm)', output.getvalue())
        self.assertNotIn('do-not-print-this', output.getvalue())

    def test_custom_public_context_path_is_preserved(self):
        output = io.StringIO()
        with redirect_stdout(output):
            print_keycloak_login({'DOMAIN_NAME':'example.com', 'VPN_KEYCLOAK_URL':'https://example.com/auth/'})
        self.assertIn('https://example.com/auth/admin/master/console/', output.getvalue())

    def test_classic_setup_does_not_show_a_nonexistent_keycloak_login(self):
        plan = InstallPlan(CLASSIC); values, generated = build_configuration(plan)
        output = io.StringIO()
        with redirect_stdout(output): print_summary(plan, values, Path('backend/.env'), generated)
        self.assertNotIn('/admin/master/console/', output.getvalue())

    def test_keycloak_summary_includes_admin_console(self):
        plan = InstallPlan(KEYCLOAK, 'example.com'); values, generated = build_configuration(plan)
        output = io.StringIO()
        with redirect_stdout(output): print_summary(plan, values, Path('backend/.env'), generated)
        self.assertIn('https://auth.example.com/admin/master/console/', output.getvalue())


if __name__ == '__main__': unittest.main()
