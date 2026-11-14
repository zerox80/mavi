# Mavi VPN Installation Guide

This guide describes Linux server deployment and client installation. The server can use a static token, Keycloak OIDC, or the web panel with Keycloak.

## 1. Linux Server Installation

### Guided installation

Use a repository checkout on a Linux server with Python 3. Run the launcher from the repository root:

```bash
sudo ./install-server.sh
```

The installer presents these choices:

```text
1) Web panel + Keycloak (Recommended)
2) Classic token setup
3) Advanced: Keycloak only
```

The modes have different authentication behavior:

- **Web panel + Keycloak:** The installer always enables Keycloak, even if an earlier configuration contained `VPN_KEYCLOAK_ENABLED=false`. It creates the panel database, Keycloak client, roles, and optional initial administrator. VPN clients use Keycloak and do not need `VPN_AUTH_TOKEN`.
- **Classic token setup:** The installer generates a static `VPN_AUTH_TOKEN`. Configure the token/PSK and certificate PIN manually on each client. This mode does not require MFA or Keycloak.
- **Keycloak only:** The installer enables the existing Keycloak realm and `mavi-client` flow without installing the panel. Create and manage VPN users in the Keycloak Administration Console as before.

When `backend/.env` exists, every interactive start offers **continue with saved settings**, **start setup questions again**, or **cancel**. Continuing keeps the saved mode and settings. Starting again lets you choose the mode, base domain and proxy anew while retaining existing credentials, custom network settings, data, certificates and private keys. Derived URLs and obsolete installer-managed proxy settings are recomputed so a mistyped domain cannot survive in the old Keycloak URL or panel endpoint. Invalid selections, domains, email addresses and usernames can be corrected at the prompt.

To go directly to the setup questions:

```bash
sudo ./install-server.sh --reconfigure
```

The wizard shows the resulting `auth.<domain>` and `panel.<domain>` hostnames before applying changes. Use the base domain, for example `example.com`, rather than entering `auth.example.com` when you want `auth.example.com` and `panel.example.com`. The existing `.env` is not changed until the new plan passes preflight and is approved. `--check` and cancelling leave it untouched. Reconfiguration is not an uninstall: it does not remove containers, databases or certificates. Review changes of mode/proxy on an already-running deployment.

Existing secrets are not regenerated. Missing required secrets are generated once with Python's `secrets` module. The inner TUN MTU is always `VPN_MTU=1280`; there are no MTU tests, MTU prompts, or PMTUD setup steps.

Before applying changes, the installer shows a summary and runs read-only checks for Linux, distribution, CPU architecture, memory, free storage, Docker and the `docker compose` plugin, `/dev/net/tun`, host addresses, relevant ports, and DNS names. DNS and address inspection do not use ping and do not prove public reachability. Missing distribution packages are installed only after a separate confirmation that displays the exact commands; the installer never executes a downloaded shell script.

For managed HTTPS, a missing `auth.<domain>` or `panel.<domain>` DNS record blocks
installation before `.env`, Nginx or containers are changed. The installer shows
the record to create using public interface addresses, excluding private/VPN and
container addresses. It then offers retry, setup editing, configuration-only
saving or cancellation. A local `/etc/hosts` entry does not replace public DNS;
an existing incorrect AAAA record must also be corrected at the DNS provider.

An existing host Nginx on TCP 80/443 is reused after verifying its host process
and `nginx -t`. Another proxy/container is identified and left running; choose
external proxy mode or resolve the conflict yourself. Docker-published ports are
checked even when a local bind appears to succeed. A permission error is reported
as a permission error and does not imply the port is occupied.

### Prebuilt images and optional source builds

The default guided setup uses `ghcr.io/zerox80/mavi-vpn:latest` for the VPN and
`ghcr.io/zerox80/mavi-vpn:panel-latest` for the panel, plus the selected upstream
Keycloak/PostgreSQL/proxy images. GitHub Actions builds AMD64 and ARM64 images;
the VPS does not compile Rust. The installer pulls all selected images before
writing configuration, then starts services with `--no-build --pull never`.
A pull failure stops the run without silently switching to compilation.
Image use removes build-time RAM usage; Keycloak and other running services
still need memory.

`MAVI_IMAGE` and `MAVI_PANEL_IMAGE` can select an alternative published tag/digest.
For an explicit local build, use `sudo ./install-server.sh --build-from-source`.
That selection is saved as `MAVI_BUILD_FROM_SOURCE=true` and adds the applicable
`docker-compose.build.yml` overrides. Reconfiguring defaults to prebuilt images
unless `--build-from-source` is supplied again. Compose data mounts and database
volumes remain in the same locations in both modes.

To run only the checks, without writing `.env`, installing packages, or changing containers:

```bash
./install-server.sh --check
```

Non-interactive installation uses the same defaults and requires explicit confirmation with `--yes`:

```bash
./install-server.sh --non-interactive --mode panel --domain example.com --acme-email admin@example.com --create-admin --yes
./install-server.sh --non-interactive --mode panel --domain example.com --proxy external --create-admin --admin-email admin@example.com --yes
./install-server.sh --non-interactive --mode classic --yes
./install-server.sh --non-interactive --mode keycloak --domain example.com --acme-email admin@example.com --yes
```

Use `--proxy external` instead of an ACME email when an existing reverse proxy instance terminates TLS. `--proxy` also accepts `nginx` (default) and `traefik` (legacy standalone). Use `--no-start` to write only the configuration. A new non-interactive installation needs `--mode`; Keycloak and panel modes also need `--domain`.

Non-interactive reruns keep saved settings unless `--reconfigure` is specified.
With `--reconfigure`, supply the new `--mode`, domain/proxy arguments and `--yes`:

```bash
sudo ./install-server.sh --non-interactive --reconfigure --mode panel --domain example.com --acme-email admin@example.com --yes
```

### Configuration and secret storage

The installer works in `backend/` and writes `backend/.env` atomically with mode `0600`. It changes an existing file only when effective settings are missing or need correction. Before such a change, it stores the previous file with mode `0600` in `backend/data/env-backups/`; an unchanged rerun creates no backup. The backup directory is mode `0700` and is covered by the existing `backend/data/` Git ignore rule.

Do not post `.env`, its backups, private keys, Keycloak responses, access tokens, or password-reset output. The installer prints only a newly generated temporary panel-admin password, and only when it creates that user.

The selected Compose files are stored in `.env`:

| Mode | `COMPOSE_FILE` |
| --- | --- |
| Classic | `docker-compose.yml` |
| Keycloak only | `docker-compose.yml:keycloak/docker-compose.yml` |
| Web panel | `docker-compose.yml:keycloak/docker-compose.yml:panel/docker-compose.yml` |

Panel mode also sets `MAVI_WEB_PANEL_ENABLED=true`, forces `VPN_KEYCLOAK_ENABLED=true`, and defaults `VPN_KEYCLOAK_REQUIRED_ROLE` to `vpn-user`. `MAVI_PANEL_VPN_ENDPOINT` is written explicitly because nested Compose interpolation is not portable. Keycloak and the panel always publish loopback-only HTTP ports (`127.0.0.1:18080` and `127.0.0.1:18081`) for the reverse proxy.

### Reverse proxy modes

Keycloak and panel subdomains can be served in three ways. New installations default to host Nginx.

#### Host Nginx with Let's Encrypt (default)

The installer detects installed Nginx/Certbot components and installs only missing packages after showing the commands. Enter accepts the `[Y/n]` package prompt; choosing No cancels cleanly. It writes a Mavi site configuration, requests Let's Encrypt certificates with `certbot --nginx`, and enables HTTP-to-HTTPS redirection and the renewal timer. Create DNS records for `auth.<domain>` and, when enabled, `panel.<domain>` that point to this server before running the installer; unresolved names must be fixed or saved for configuration-only setup before applying managed HTTPS.

Nginx terminates HTTPS publicly and forwards to the loopback HTTP ports that Keycloak (`127.0.0.1:18080`) and the panel (`127.0.0.1:18081`) publish. No `COMPOSE_PROFILES` value and no `TRAEFIK_*` variables are written in this mode. If Nginx setup fails, the `.env` file remains written and the installer reports which step to fix before rerunning.

Open these firewall ports:

- `10443/UDP`, or the configured `VPN_PORT`, for QUIC and HTTP/3 VPN transport.
- `80/TCP` and `443/TCP` for Nginx and Let's Encrypt.
- The TCP port in `VPN_HTTP2_BIND_ADDR` when the optional HTTP/2 transport is enabled. It may use the same numeric port as UDP.

The installer does not verify public firewall or NAT reachability. A successful local port bind and DNS lookup do not replace an external connection test.

#### External reverse proxy

Choose `--proxy external` when an existing reverse proxy terminates TLS. Forward `auth.<domain>` to `http://127.0.0.1:18080` and, when enabled, forward `panel.<domain>` directly to `http://127.0.0.1:18081`. Pass the original host, HTTPS scheme, client IP, and WebSocket headers.

For the panel, set `X-Mavi-Proxy-Token` to the value of `MAVI_PANEL_PROXY_TOKEN` from the protected `backend/.env`, replacing any incoming value. Set `X-Forwarded-For` to the client IP observed by the proxy, or append that IP as the final address. Keep the token in a private proxy configuration file. Managed Nginx and standalone Traefik configure these headers automatically.

#### Legacy standalone Traefik

The installer saves the proxy selection as `MAVI_PROXY_MODE` and reuses it on subsequent runs. For older configurations, `TRAEFIK_BIND_ADDR=0.0.0.0` selects standalone Traefik. A loopback bind or a legacy Traefik profile with no explicit bind selects external proxy mode and retains the Traefik profile. Older configurations with an ACME email and no Traefik settings keep managed Nginx. Other unmarked configurations default to external proxy mode.

### Keycloak and panel bootstrap

The Keycloak container imports `mavi-vpn` and the public `mavi-client` PKCE client on an empty database. Keycloak-only mode keeps the manual user-management flow. Its optional `VPN_KEYCLOAK_REQUIRED_ROLE` remains unchanged unless it was absent from a new configuration.

The import uses Keycloak 24's `defaultSignatureAlgorithm` and leaves built-in
client scopes enabled. PostgreSQL readiness is checked before starting Keycloak.
The installer detects fatal import/OOM failures from the current container start
instead of retrying authentication for ten minutes. It also distinguishes a
ready localhost Keycloak endpoint from a failing public proxy. Existing database
contents are not removed to fix an import problem. An interrupted panel setup can
resume initial administrator creation; an existing administrator is not reset.

All installer Compose commands select their environment and files explicitly.
For a manual Keycloak-only recovery, use:

```bash
cd backend
sudo docker compose --env-file .env -f docker-compose.yml -f keycloak/docker-compose.yml up -d keycloak
```

For panel mode, the installer starts the VPN, Keycloak database, and Keycloak first (plus Traefik only in the legacy Traefik mode). It then waits for the master-admin login and imported realm and uses the Keycloak Admin REST API before starting the panel. The bootstrap performs these idempotent operations:

- Creates or updates the `panel-admin`, `panel-user`, and `vpn-user` realm roles.
- Configures the confidential `mavi-panel` client with the sole redirect URI `https://panel.<domain>/auth/callback`, authorization-code flow with PKCE S256, and implicit and direct grants disabled.
- Adds a realm-role mapper that includes the complete realm-role set in access and ID tokens.
- Adds `panel-user` and `vpn-user` to the realm's default roles for new users without deleting existing role mappings.
- Optionally creates the initial administrator with a random temporary password and assigns `panel-admin`, `panel-user`, and `vpn-user`. An existing administrator keeps their password and current roles; the bootstrap adds only missing required roles.

An existing Keycloak client secret is never reset. If the installer generated a new `.env` secret for an already existing panel client, it adopts the existing Keycloak secret into the protected `.env`. If an explicitly stored `.env` secret does not match, bootstrap stops without changing the Keycloak secret.

The bootstrap can be repeated safely after a failed first start:

```bash
cd /path/to/mavi-vpn
python3 scripts/server_installer/keycloak_bootstrap.py --env-file backend/.env
```

To create the initial administrator later, append `--create-admin --admin-username panel-admin --admin-email admin@example.com`. Repeating this command for an existing username does not reset the password. After a successful manual bootstrap, start the panel with `cd backend && docker compose up -d`.

### Starting and restarting

The installer performs the initial build and ordered start. After it finishes, restart the complete selected stack from `backend/` with exactly:

```bash
cd /path/to/mavi-vpn/backend
docker compose up -d
```

Do not add `--profile` or `-f`: Docker Compose reads `COMPOSE_FILE` and `COMPOSE_PROFILES` automatically from `backend/.env`. This command also starts the panel dependencies after the one-time bootstrap has completed. To rebuild local images after a source update, run `docker compose build --pull` before `docker compose up -d`.

If an existing panel installation has no `MAVI_PANEL_PROXY_TOKEN`, rerun `./install-server.sh` from the repository root when upgrading. The installer generates the token and updates managed Nginx. For an external reverse proxy, apply the panel headers described in [External reverse proxy](#external-reverse-proxy) and forward the panel hostname directly to its loopback port so the rate limiter uses each client's IP.

### Client credentials

For classic mode, read the generated token and certificate PIN on the server and enter them manually in the client:

```bash
cd /path/to/mavi-vpn/backend
grep '^VPN_AUTH_TOKEN=' .env
cat data/cert_pin.txt
```

When censorship-resistant mode uses ECH, the optional client value is in `data/ech_config_hex.txt`. Panel mode distributes the endpoint, certificate PIN, transport, ECH configuration, Keycloak realm, and MTU through its enrollment flow; the static token is not used.

The installer prints the complete Keycloak administrator login URL in its summary
and after startup: `https://auth.<domain>/admin/master/console/`. The initial
global administrator belongs to the `master` realm; its username is
`KEYCLOAK_ADMIN_USER` (default `admin`) and its initial password is stored as
`KEYCLOAK_ADMIN_PASS` in `backend/.env`. This account is separate from panel/VPN
users in the `mavi-vpn` realm. The installer does not print the password.

For Keycloak-only mode, use that administrator URL, select the `mavi-vpn` realm,
and create users there. The imported `mavi-client` uses PKCE S256 and the existing
desktop and Android callback URIs. Assign `vpn-user` when
`VPN_KEYCLOAK_REQUIRED_ROLE=vpn-user` is configured.

Certificates, ECH keys, and the VPN private key persist in `backend/data/`. The installer never regenerates them. Back up this directory before deleting containers or volumes.

### Host forwarding and IPv6

The VPN gives clients IPv4 addresses and internal ULA IPv6 addresses from `fd00::/64`, then uses NAT on the host network. Enable forwarding on the host because the hardened container cannot change host sysctls:

```bash
sudo sysctl -w net.ipv4.ip_forward=1
sudo sysctl -w net.ipv6.conf.all.forwarding=1
```

On RA-based clouds such as AWS Lightsail, allow Router Advertisements on the WAN interface while forwarding is enabled, then persist the settings:

```bash
WAN=$(ip route get 8.8.8.8 | awk '{print $5; exit}')
sudo tee /etc/sysctl.d/99-mavi-vpn.conf >/dev/null <<CONF
net.ipv4.ip_forward = 1
net.ipv6.conf.all.forwarding = 1
net.ipv6.conf.${WAN}.accept_ra = 2
net.ipv6.conf.default.accept_ra = 2
CONF
sudo sysctl --system
```

Do not set `VPN_NETWORK_V6` to a provider-assigned public prefix. For an intentionally IPv4-only deployment, set `VPN_DISABLE_IPV6=true`; this also skips the startup wait for a global IPv6 address.

Inspect startup and routing failures without dumping secrets:

```bash
cd /path/to/mavi-vpn/backend
docker compose ps
docker compose logs vpn-server
docker compose logs keycloak
docker compose logs panel
cat /proc/sys/net/ipv4/ip_forward
cat /proc/sys/net/ipv6/conf/all/forwarding
```

---

## 2. Windows Client Installation

### Prerequisites
* **Rust (Cargo)** — https://rustup.rs
* **Python 3** (for the install scripts)
* **Administrator privileges** (for the Windows Service)

### Option A: Automated Install (Recommended)

Open **PowerShell as Administrator** in the project root:

```powershell
# 1. Install CLI + Windows Service
python install_cli_windows.py

# 2. Install Tauri GUI (optional)
python install_gui_windows.py
```

The scripts handle everything: building, installing to `C:\Program Files\MaviVPN`, registering and starting the Windows Service, PATH setup, and Desktop shortcuts.

### Option B: Manual Build

```powershell
# Build CLI + Service
cargo build --release -p windows-vpn

# In an Administrator PowerShell, create a NEW protected installation directory.
# Do not register the executable directly from the build tree or Downloads.
$installDir = Join-Path ([Environment]::GetFolderPath('ProgramFiles')) 'MaviVPN'
New-Item -ItemType Directory -Path $installDir -ErrorAction Stop | Out-Null
Copy-Item .\target\release\mavi-vpn-service.exe, .\target\release\mavi-vpn-client.exe -Destination $installDir
icacls.exe $installDir /setowner '*S-1-5-32-544' /T
if ($LASTEXITCODE -ne 0) { throw 'Cannot set installation owner' }
& "$installDir\mavi-vpn-service.exe" install
if ($LASTEXITCODE -ne 0) { throw 'Service installation refused' }
net start MaviVPNService

# Build GUI (requires cargo-tauri)
cargo install tauri-cli
cd gui && cargo tauri build
```

The service runs as LocalSystem. Manual registration checks the executable and
every ancestor for trusted ownership, reparse points, and permissions that allow
ordinary users to modify or replace the service path. The installation directory
must also prevent untrusted creation of sibling files. An unsafe path is refused
without creating a service. If `MaviVPN` already exists, have an administrator
verify its ownership and permissions before reusing it; do not simply grant broad
write access or move a user-owned build directory into Program Files.

For an existing manual installation, updating the binary does not relocate the
saved service path. Use elevated Windows service management tools to stop and
remove the old registration, then install trusted binaries from a new protected
directory using the steps above.

### Usage

**GUI:** Launch "Mavi VPN" from the Start Menu or Desktop shortcut. Add or edit a saved connection, then configure endpoint, certificate PIN, PSK or Keycloak, and optional VPN MTU in that connection editor. Select the connection and click Connect.

**CLI:**
```powershell
mavi-vpn-client start     # Connect (prompts for config on first run)
mavi-vpn-client stop      # Disconnect
mavi-vpn-client status    # Check connection status
```

### Troubleshooting
The Windows service and GUI write rotating log files:

```powershell
# Windows Service log
$env:PROGRAMDATA\mavi-vpn\logs\mavi-vpn-service.log

# Tauri GUI log
$env:LOCALAPPDATA\MaviVPN\logs\mavi-vpn-gui.log
```

Run the service in console mode from an Administrator PowerShell for foreground
debugging:

```powershell
$env:RUST_LOG = "debug"
.\mavi-vpn-service.exe --console

# Or follow the installed service and GUI logs:
Get-Content -Wait "$env:PROGRAMDATA\mavi-vpn\logs\mavi-vpn-service.log"
Get-Content -Wait "$env:LOCALAPPDATA\MaviVPN\logs\mavi-vpn-gui.log"
```

Logs must not contain access tokens, refresh tokens, JWT bodies, private keys,
or full token exchange responses. Redact logs before sharing.

---

## 3. Linux Client Installation

### Prerequisites
* **Rust (Cargo)** — https://rustup.rs
* **Python 3** (for the install scripts)
* **root/sudo** (for TUN device and routing)

### Option A: Automated Install (Recommended)

```bash
# 1. Install CLI + optional systemd service
python3 install_cli_linux.py

# 2. Install Tauri GUI (optional)
python3 install_gui_linux.py
```

The GUI installer auto-detects your distro and installs via RPM (Fedora/RHEL), DEB (Debian/Ubuntu), AppImage, or raw binary fallback.

### Option B: Manual Build

```bash
# Build CLI
cargo build --release -p linux-vpn

# Install binary
sudo install -m 755 target/release/mavi-vpn /usr/local/bin/

# Allow the current desktop user to control the root daemon
sudo groupadd --system --force mavivpn
sudo usermod -aG mavivpn $USER
# Log out and back in before using daemon mode without sudo.

# Install systemd service (optional)
sudo cp linux/mavi-vpn.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mavi-vpn
```

### Usage

**Direct mode** (no daemon, runs in foreground):
```bash
sudo mavi-vpn                          # Interactive config prompt
sudo mavi-vpn -c /path/to/config.json  # With config file
```

**Daemon mode** (for GUI and remote CLI control):
```bash
sudo mavi-vpn daemon &    # or: sudo systemctl start mavi-vpn
mavi-vpn start            # Connect via daemon
mavi-vpn stop             # Disconnect
mavi-vpn status           # Check connection status
mavi-vpn-gui              # Launch GUI
```

### Config File Location
The CLI searches in order: `./config.json` → `~/.config/mavi-vpn/config.json` → `/etc/mavi-vpn/config.json`

### Troubleshooting
```bash
RUST_LOG=debug sudo mavi-vpn           # Debug direct mode
sudo journalctl -u mavi-vpn -f         # Debug systemd daemon
tail -f ~/.local/state/mavi-vpn/logs/mavi-vpn-gui.log  # Debug GUI
```

For persistent systemd debug logging, create an override:

```bash
sudo systemctl edit mavi-vpn
```

```ini
[Service]
Environment=RUST_LOG=debug,linux_vpn=debug,shared=debug
```

Then reload and restart:

```bash
sudo systemctl daemon-reload
sudo systemctl restart mavi-vpn
sudo journalctl -u mavi-vpn -f
```

GUI logs are written to `$XDG_STATE_HOME/mavi-vpn/logs/mavi-vpn-gui.log` when
`XDG_STATE_HOME` is set, otherwise to
`~/.local/state/mavi-vpn/logs/mavi-vpn-gui.log`.

Logs must not contain access tokens, refresh tokens, JWT bodies, private keys,
or full token exchange responses. Redact logs before sharing.

---

## 4. Android Client Installation

The Android client is built as a native Kotlin app with a bundled Rust core for the VPN logic.

### Prerequisites
* **WSL 2** on Windows, with a Linux distribution such as Ubuntu.
* **Docker** with Compose and a running Linux engine. Enable Docker Desktop's
  WSL integration, or install Docker Engine and Compose in WSL.
* Internet access for SDK/Rust dependencies and the current fork commits.

Java, Android SDK/NDK, Rust and cargo-ndk are installed inside the Docker image.

### Step-by-Step Guide

**1. Configure the Backend:**
Ensure your Keycloak client is configured with `com.mavi.vpn://oauth/callback` for debug builds. Release builds must use a verified HTTPS App Link; build with `-Pmavi.oauthRedirectUri=https://<verified-domain>/<callback-path>` and register that exact URI in Keycloak.

**2. Build the App:**
1. From PowerShell in the repository root, run `.\Android-docker-build\build.ps1`.
   This invokes Docker through WSL. In WSL/Linux, run `bash Android-docker-build/build.sh`.
2. The container updates and verifies the h2/h3/Quinn forks and builds the app
   with the native Rust libraries for all four architectures.
3. Install `Android-docker-build/output/apk/debug/app-debug.apk`.

See [Android Docker build](../Android-docker-build/README.md) for release builds,
tests and cache management.

**3. Run and Login:**
1. Install the APK on your Android device.
2. Open **MAVI VPN**.
3. Use the **"Login with Keycloak"** button.
4. If it's your first time, click **"Edit Keycloak Server"** to enter your server URL and realm.
5. After login, you'll be redirected back to the app with your token automatically filled.
6. Enter your **Server Endpoint** and **Certificate PIN**.
7. Click **CONNECT**.

**4. Split Tunneling:**
Go to **Settings** (Gear icon) to select which apps should bypass the VPN (Exclude mode) or which apps alone should use it (Include mode).
