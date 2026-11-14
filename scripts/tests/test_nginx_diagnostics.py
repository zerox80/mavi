"""Surface useful failures without exposing private Nginx dumps or taking hosts."""
from pathlib import Path
import subprocess
import unittest

from scripts.server_installer.nginx_diagnostics import command_details, foreign_hosts


class NginxDiagnosticsTests(unittest.TestCase):
    def test_existing_exact_host_in_another_site_is_reported(self):
        dump = '''# configuration file /etc/nginx/sites-enabled/keycloak.conf:
server {
    listen 443 ssl;
    # server_name panel.example.com;
    server_name "auth.example.com"
                another.example.com;
}
# configuration file /etc/nginx/sites-available/mavi-vpn.conf:
server { server_name panel.example.com; }
'''
        result = foreign_hosts(dump, ['auth.example.com', 'panel.example.com'],
                               (Path('/etc/nginx/sites-available/mavi-vpn.conf'),))
        self.assertEqual(result, {'auth.example.com': '/etc/nginx/sites-enabled/keycloak.conf'})

    def test_default_and_wildcard_sites_can_coexist(self):
        dump = '# configuration file /etc/nginx/conf.d/default.conf:\nserver { server_name _ *.example.com; }\n'
        self.assertEqual(foreign_hosts(dump, ['auth.example.com'], ()), {})

    def test_error_details_keep_certbot_cause_and_redact_proxy_secret(self):
        error = subprocess.CalledProcessError(1, ['certbot'],
                                             stderr='Detail: Invalid response from auth.example.com\nsecret-token')
        detail = command_details(error, ('secret-token',))
        self.assertIn('Invalid response', detail)
        self.assertNotIn('secret-token', detail)
        self.assertIn('<redacted>', detail)

    def test_nginx_dump_is_never_used_as_error_output(self):
        error = subprocess.CalledProcessError(1, ['nginx', '-T'], output='private header contents')
        self.assertNotIn('private', command_details(error))

    def test_long_error_is_bounded(self):
        error = subprocess.CalledProcessError(1, ['certbot'], stderr='x' * 10000)
        self.assertLessEqual(len(command_details(error)), 3000)


if __name__ == '__main__':
    unittest.main()
