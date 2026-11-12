"""Idempotent Keycloak Admin REST setup for the Mavi web panel."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import secrets
import sys
import time
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, build_opener

try:
    from .config import ConfigurationError, generate_secret, load_env, parse_bool, save_env, validate_domain, validate_username
except ImportError:  # Direct execution from the repository checkout.
    from config import ConfigurationError, generate_secret, load_env, parse_bool, save_env, validate_domain, validate_username

PANEL_CLIENT_ID = "mavi-panel"
PANEL_ROLES = ("panel-admin", "panel-user", "vpn-user")


class KeycloakError(RuntimeError):
    """Keycloak rejected or could not complete an administrative request."""


@dataclass(frozen=True)
class AdminResult:
    username: str
    created: bool
    temporary_password: str = ""


class KeycloakAdmin:
    def __init__(
        self,
        base_url: str,
        realm: str,
        admin_user: str,
        admin_password: str,
        opener: Any = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.realm = realm
        self.admin_user = admin_user
        self.admin_password = admin_password
        self.opener = opener or build_opener()
        self.sleep = sleep
        self.monotonic = monotonic
        self.token = ""
        self.realm_path = quote(realm, safe="")

    def _request(
        self,
        method: str,
        path: str,
        payload: Any = None,
        form: Mapping[str, str] | None = None,
        authenticated: bool = True,
        allow_not_found: bool = False,
    ) -> Any:
        headers = {"Accept": "application/json"}
        body = None
        if form is not None:
            body = urlencode(form).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if authenticated:
            if not self.token:
                raise KeycloakError("Keycloak authentication has not completed.")
            headers["Authorization"] = f"Bearer {self.token}"
        request = Request(self.base_url + path, data=body, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=30) as response:
                content = response.read()
        except HTTPError as exc:
            content = exc.read()
            if exc.code == 404 and allow_not_found:
                return None
            detail = self._error_detail(exc.code, content)
            raise KeycloakError(f"Keycloak {method} {path} failed: {detail}") from None
        except (URLError, TimeoutError, OSError) as exc:
            raise KeycloakError(f"Keycloak {method} {path} failed: {exc}") from exc
        if not content:
            return {}
        try:
            return json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise KeycloakError(f"Keycloak {method} {path} returned invalid JSON.") from exc

    @staticmethod
    def _error_detail(status: int, content: bytes) -> str:
        try:
            payload = json.loads(content)
            detail = payload.get("errorMessage") or payload.get("error_description") or payload.get("error")
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            detail = ""
        return f"HTTP {status}" + (f": {str(detail)[:300]}" if detail else "")

    def authenticate(self) -> None:
        response = self._request(
            "POST",
            "/realms/master/protocol/openid-connect/token",
            form={
                "client_id": "admin-cli",
                "grant_type": "password",
                "username": self.admin_user,
                "password": self.admin_password,
            },
            authenticated=False,
        )
        token = response.get("access_token", "")
        if not token:
            raise KeycloakError("The Keycloak master-admin response did not contain an access token.")
        self.token = token

    def wait_until_ready(self, timeout: int = 600) -> None:
        deadline = self.monotonic() + timeout
        last_error = "no response"
        while True:
            try:
                self.authenticate()
                realm = self._request("GET", f"/admin/realms/{self.realm_path}", allow_not_found=True)
                if realm is not None:
                    return
                last_error = f"realm {self.realm!r} is not imported yet"
            except KeycloakError as exc:
                last_error = str(exc)
            if self.monotonic() >= deadline:
                raise KeycloakError(f"Keycloak did not become ready within {timeout} seconds: {last_error}")
            self.sleep(min(5, max(0, deadline - self.monotonic())))

    def ensure_role(self, name: str) -> dict[str, Any]:
        path = f"/admin/realms/{self.realm_path}/roles/{quote(name, safe='')}"
        role = self._request("GET", path, allow_not_found=True)
        representation = {
            "name": name,
            "description": f"Mavi panel and VPN role: {name}",
            "composite": False,
            "clientRole": False,
        }
        if role is None:
            self._request("POST", f"/admin/realms/{self.realm_path}/roles", representation)
            role = self._request("GET", path)
        else:
            updated = {**role, **representation}
            self._request("PUT", path, updated)
        return role

    def ensure_default_roles(self, roles: list[dict[str, Any]]) -> None:
        base = f"/admin/realms/{self.realm_path}/roles"
        default_name = f"default-roles-{self.realm}"
        default = self._request("GET", f"{base}/{quote(default_name, safe='')}", allow_not_found=True)
        if default is None:
            raise KeycloakError(f"Keycloak realm role {default_name!r} is missing.")
        path = f"{base}/{quote(default_name, safe='')}/composites"
        existing = self._request("GET", path)
        names = {role.get("name") for role in existing}
        missing = [role for role in roles if role["name"] in {"panel-user", "vpn-user"} and role["name"] not in names]
        if missing:
            self._request("POST", path, missing)

    def _client_representation(self, panel_url: str, secret: str) -> dict[str, Any]:
        return {
            "clientId": PANEL_CLIENT_ID,
            "name": "Mavi Web Panel",
            "enabled": True,
            "protocol": "openid-connect",
            "publicClient": False,
            "bearerOnly": False,
            "standardFlowEnabled": True,
            "implicitFlowEnabled": False,
            "directAccessGrantsEnabled": False,
            "serviceAccountsEnabled": False,
            "frontchannelLogout": False,
            "rootUrl": panel_url,
            "baseUrl": "/",
            "redirectUris": [f"{panel_url}/auth/callback"],
            "webOrigins": [panel_url],
            "fullScopeAllowed": True,
            "defaultClientScopes": ["web-origins", "acr", "profile", "roles", "email"],
            "attributes": {
                "pkce.code.challenge.method": "S256",
                "oauth2.device.authorization.grant.enabled": "false",
                "backchannel.logout.revoke.offline.tokens": "false",
            },
            "secret": secret,
        }

    def ensure_client(self, panel_url: str, secret: str, adopt_existing_secret: bool = False) -> tuple[str, str]:
        base = f"/admin/realms/{self.realm_path}/clients"
        matches = self._request("GET", f"{base}?clientId={PANEL_CLIENT_ID}")
        existing = next((client for client in matches if client.get("clientId") == PANEL_CLIENT_ID), None)
        desired = self._client_representation(panel_url, secret)
        effective_secret = secret
        if existing is None:
            self._request("POST", base, desired)
            matches = self._request("GET", f"{base}?clientId={PANEL_CLIENT_ID}")
            existing = next(client for client in matches if client.get("clientId") == PANEL_CLIENT_ID)
        else:
            client_path = f"{base}/{existing['id']}"
            current = self._request("GET", f"{client_path}/client-secret")
            current_secret = current.get("value") if isinstance(current, dict) else None
            if not isinstance(current_secret, str) or not current_secret:
                raise KeycloakError("The existing mavi-panel client did not return a usable client secret.")
            if current_secret != secret and not adopt_existing_secret:
                raise KeycloakError(
                    "MAVI_PANEL_CLIENT_SECRET does not match the existing mavi-panel client. "
                    "No secret was changed; update backend/.env with the existing client secret."
                )
            effective_secret = current_secret
            representation = {**existing, **desired}
            representation.pop("secret", None)
            representation["attributes"] = {**existing.get("attributes", {}), **desired["attributes"]}
            self._request("PUT", client_path, representation)
        client_id = existing["id"]
        self._ensure_role_mapper(client_id)
        return client_id, effective_secret

    def _ensure_role_mapper(self, client_id: str) -> None:
        path = f"/admin/realms/{self.realm_path}/clients/{client_id}/protocol-mappers/models"
        mappers = self._request("GET", path)
        name = "mavi-panel-realm-roles"
        representation = {
            "name": name,
            "protocol": "openid-connect",
            "protocolMapper": "oidc-usermodel-realm-role-mapper",
            "consentRequired": False,
            "config": {
                "claim.name": "realm_access.roles",
                "jsonType.label": "String",
                "multivalued": "true",
                "id.token.claim": "true",
                "access.token.claim": "true",
                "userinfo.token.claim": "true",
            },
        }
        existing = next((mapper for mapper in mappers if mapper.get("name") == name), None)
        if existing:
            self._request("PUT", f"{path}/{existing['id']}", {**existing, **representation})
        else:
            self._request("POST", path, representation)

    def ensure_admin(self, username: str, email: str, password_factory: Callable[[], str]) -> AdminResult:
        base = f"/admin/realms/{self.realm_path}/users"
        matches = self._request("GET", f"{base}?username={quote(username, safe='')}&exact=true")
        user = next((item for item in matches if item.get("username") == username), None)
        created = user is None
        temporary_password = ""
        if created:
            self._request(
                "POST",
                base,
                {"username": username, "email": email, "enabled": True, "emailVerified": False},
            )
            matches = self._request("GET", f"{base}?username={quote(username, safe='')}&exact=true")
            user = next(item for item in matches if item.get("username") == username)
            temporary_password = password_factory()
            self._request(
                "PUT",
                f"{base}/{user['id']}/reset-password",
                {"type": "password", "value": temporary_password, "temporary": True},
            )
        role_path = f"{base}/{user['id']}/role-mappings/realm"
        current = self._request("GET", role_path)
        current_names = {role.get("name") for role in current}
        roles = [self.ensure_role(name) for name in PANEL_ROLES]
        missing = [role for role in roles if role["name"] not in current_names]
        if missing:
            self._request("POST", role_path, missing)
        return AdminResult(username, created, temporary_password)


def bootstrap_panel(
    values: dict[str, str],
    env_path: Path,
    *,
    create_admin: bool = False,
    admin_username: str = "panel-admin",
    admin_email: str = "",
    wait_timeout: int = 600,
    opener: Any = None,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> AdminResult | None:
    domain = validate_domain(values.get("DOMAIN_NAME", ""))
    base_url = values.get("VPN_KEYCLOAK_URL") or f"https://auth.{domain}"
    realm = values.get("VPN_KEYCLOAK_REALM", "mavi-vpn")
    configured_secret = values.get("MAVI_PANEL_CLIENT_SECRET")
    client_secret = configured_secret or generate_secret()
    api = KeycloakAdmin(
        base_url,
        realm,
        values.get("KEYCLOAK_ADMIN_USER", "admin"),
        values.get("KEYCLOAK_ADMIN_PASS", ""),
        opener=opener,
        sleep=sleep,
        monotonic=monotonic,
    )
    if not api.admin_password:
        raise ConfigurationError("KEYCLOAK_ADMIN_PASS is missing from backend/.env.")
    api.wait_until_ready(wait_timeout)
    roles = [api.ensure_role(name) for name in PANEL_ROLES]
    _, effective_secret = api.ensure_client(
        f"https://panel.{domain}", client_secret, adopt_existing_secret=not configured_secret
    )
    api.ensure_default_roles(roles)
    admin = None
    if create_admin:
        admin = api.ensure_admin(
            validate_username(admin_username),
            admin_email.strip(),
            lambda: secrets.token_urlsafe(18),
        )
    if not configured_secret:
        # Persist only the authoritative secret. If bootstrap fails before
        # this point, a retry can still adopt the secret from Keycloak.
        updated = {**values, "MAVI_PANEL_CLIENT_SECRET": effective_secret}
        save_env(env_path, updated)
        values.update(updated)
    return admin


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path("backend/.env"))
    parser.add_argument("--create-admin", action="store_true")
    parser.add_argument("--admin-username", default="panel-admin")
    parser.add_argument("--admin-email", default="")
    parser.add_argument("--wait-timeout", type=int, default=600)
    args = parser.parse_args(argv)
    try:
        values = load_env(args.env_file)
        if not parse_bool(values.get("MAVI_WEB_PANEL_ENABLED")):
            raise ConfigurationError("The web panel is not enabled in this .env file.")
        if args.create_admin and not args.admin_email.strip():
            raise ConfigurationError("--admin-email is required with --create-admin.")
        admin = bootstrap_panel(
            values,
            args.env_file,
            create_admin=args.create_admin,
            admin_username=args.admin_username,
            admin_email=args.admin_email,
            wait_timeout=args.wait_timeout,
        )
    except (ConfigurationError, KeycloakError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print("The panel client, realm roles, and default roles are ready.")
    if admin and admin.created:
        print(f"Created panel administrator: {admin.username}")
        print(f"Temporary password: {admin.temporary_password}")
    elif admin:
        print(f"Panel administrator already exists; password and existing roles were preserved: {admin.username}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
