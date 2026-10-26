# Mavi VPN · Docker

[![Publish VPN image](https://github.com/zerox80/mavi-vpn/actions/workflows/publish-server.yml/badge.svg)](https://github.com/zerox80/mavi-vpn/actions/workflows/publish-server.yml)

A prebuilt [Mavi VPN server](https://github.com/zerox80/mavi-vpn) for Linux VPS hosts.
GitHub Actions compiles the Rust code; your server only downloads the image from GHCR.
All you need to run it is `compose.yaml` and your own `.env` file.

- Image: `ghcr.io/zerox80/mavi-vpn:latest`
- Architectures: Linux AMD64 (x86_64) and ARM64 (aarch64)
- Runs only the VPN server; you can connect it to an existing Keycloak server.
- Certificates and ECH keys are persisted in `./data`.
- No Rust, Cargo, or local Docker build is required on the VPS.

## Requirements

Linux with [Docker Engine and the Compose plugin](https://docs.docker.com/engine/install/),
permission to use Docker, and an available `/dev/net/tun` device.
The container uses host networking and requires `NET_ADMIN`, `NET_RAW`,
and `NET_BIND_SERVICE` for TUN, routing, firewall rules, and privileged ports.
Only one instance using these firewall chains may run on a host.

Allow `10443/UDP` in both the host and provider firewalls (or the port chosen
in `VPN_BIND_ADDR`). For the optional HTTP/2 listener, also allow its TCP port.
There are no Compose port mappings because the container uses `network_mode: host`.

## Installation

Use `.env.example` for both IPv4-only and IPv4 + IPv6 installations.
It explains all settings, including the IPv6 switches, directly in the file.
For IPv6, also complete the [host IPv6 setup](#enabling-ipv6) before starting.

```bash
git clone https://github.com/zerox80/mavi.git
cd mavi/docker/server
cp .env.example .env
chmod 600 .env
openssl rand -hex 32
nano .env
```

Enter the generated token in `.env` as `VPN_AUTH_TOKEN=...`.
Your actual `.env` contains credentials and stays local; it is excluded by
`.gitignore`. Additional options are documented in the comments in `.env.example`.

Enable IPv4 forwarding on the host once and persist the setting:

```bash
echo 'net.ipv4.ip_forward = 1' | sudo tee /etc/sysctl.d/99-mavi-vpn-ipv4.conf
sudo sysctl -p /etc/sysctl.d/99-mavi-vpn-ipv4.conf
```

For IPv6 internet access through the VPN, also complete [Enabling IPv6](#enabling-ipv6).

Then start the server:

```bash
docker compose pull
docker compose up -d
docker compose logs --tail=100 -f vpn-server
```

After startup, retrieve the certificate pin:

```bash
sudo cat data/cert_pin.txt
```

Enter the server address, port, token, and certificate pin in the client.
Standard QUIC without censorship resistance (CR) is used by default. To enable
CR, set `VPN_CENSORSHIP_RESISTANT=true` and select the matching mode in the client.

## Updates and rollbacks

```bash
git pull --ff-only
docker compose pull
docker compose up -d
```

Git does not replace `.env` or `data/` during this process. Copy new options from
`.env.example` as needed. Active VPN connections are interrupted when the
container is replaced and must reconnect.

`latest` contains the most recent successfully published build for both
architectures. The daily build does not install anything automatically on your
server; updates only take effect when you run the commands above.

Each published build also has its own tag,
`build-<run-id>-<attempt>`, listed in the run summary under
[Actions](https://github.com/zerox80/mavi-vpn/actions/workflows/publish-server.yml).
To update or roll back to a specific build, set `MAVI_IMAGE` in `.env` to the
image with that tag, then run `docker compose pull && docker compose up -d` again.
You can also pin an immutable image using
`MAVI_IMAGE=ghcr.io/zerox80/mavi-vpn@sha256:<digest>`.

Keep and back up `data/`: losing the existing certificates changes the
certificate pin for all clients.

## Migrating an existing installation

When moving from an existing `mavi-vpn-docker` checkout, reuse its `.env` and
entire `data/` directory in `mavi-vpn/docker/server/`. Stop and remove the old
VPN container first, as described below, to release its name, listener and TUN
device. Keep a backup of the original configuration and data.

1. Clone this repository into a new directory and copy `.env.example` to `.env`.
   Copy over your existing `VPN_*` values and use the same token. In particular,
   keep the CR mode, ports, IP networks, and IPv6 setting unchanged.
   Do not copy `COMPOSE_FILE` or `COMPOSE_PROFILES` from the old full stack.
2. Run `docker compose stop vpn-server` in the old `mavi-vpn/backend` directory.
   This releases the port, TUN device, and firewall chains used by the old server.
3. Copy the entire existing `backend/data` directory, including certificates,
   private keys, and ECH files, into this new repository as `data/`.
   Preserve ownership and file permissions, for example with `sudo cp -a`.
4. Remove the stopped VPN container by running `docker compose rm -f vpn-server`
   in its old directory to free up the `mavi-vpn` container name.
   Keep the old data directory as a backup.
5. Run `docker compose pull` and `docker compose up -d` in the new directory.

An existing Keycloak/Postgres stack can continue running separately. Enter its
reachable Keycloak URL and authentication options in the new `.env`.

## Memory on small VPS hosts

The memory-intensive Rust release build runs entirely on GitHub.
The running VPN's memory usage depends on connections and traffic;
no fixed minimum RAM requirement has been measured for this setup.

Compose limits the VPN container to `512m` by default through `MAVI_MEMORY_LIMIT`.
The combined RAM and swap limit has the same value, so the container cannot use
swap. If it exceeds the limit, the kernel may terminate the container; Docker
will then restart it. This does not prevent other processes from exhausting
the host's RAM. Increase the limit for heavy VPN traffic while leaving enough
memory for the operating system.

Check memory usage and the latest out-of-memory (OOM) status:

```bash
docker stats --no-stream mavi-vpn
docker inspect mavi-vpn --format '{{.State.OOMKilled}}'
```

Logs are limited to three files of 10 MB each. Keycloak and PostgreSQL are not
part of this Compose configuration; account for their resource usage separately
when planning capacity.

## Enabling IPv6

`VPN_DISABLE_IPV6=true` is the default to simplify initial setup.
Enabling IPv6 gives VPN clients internal addresses from `fd00::/64`; the server
uses NAT66 to send their traffic through the VPS's public IPv6 address.
Keep `VPN_NETWORK_V6` as an internal ULA subnet, not the VPS's public address
or provider-assigned prefix.

IPv6 **inside the tunnel** also works when clients connect to the server over
IPv4 (`VPN_BIND_ADDR=0.0.0.0:10443`). To accept VPN connections over IPv6 as well,
configure the optional IPv6 listener below.

Run the following commands in a Bash shell on the **Linux VPS host**, from the
`mavi-vpn/docker/server` directory. Because this deployment uses
[host networking](https://docs.docker.com/engine/network/drivers/host/), it needs
no Docker bridge IPv6 subnet, `daemon.json` IPv6 setting, or port mappings.

### 1. Check public IPv6 connectivity

Enable IPv6 in your VPS provider's network settings first. The current container
entrypoint selects its WAN interface using the IPv4 route to `8.8.8.8` and uses
that same interface for NAT66. These instructions assume IPv4 and IPv6 internet
access use that interface; separate IPv4/IPv6 uplinks need a different server
network setup.

```bash
MAVI_WAN=$(ip -4 route get 8.8.8.8 | awk '{for (i=1; i<=NF; i++) if ($i=="dev") {print $(i+1); exit}}')
printf 'WAN interface: %s\n' "$MAVI_WAN"
ip -6 addr show dev "${MAVI_WAN:?No IPv4 WAN interface found}" scope global
ip -6 route show default
ip -6 route get 2606:4700:4700::1111
curl -6 --fail --max-time 15 https://www.cloudflare.com/cdn-cgi/trace
```

Expect a public IPv6 address on the WAN interface, an IPv6 default route, and
an `ip=` line with the VPS's public IPv6 address in the curl output. The IPv6
route lookup should name the same WAN interface. If these checks fail, fix the
host/provider IPv6 configuration before enabling IPv6 in the VPN.

### 2. Enable forwarding on the host

The container cannot reliably write host sysctls, so apply these settings on the
host. On networks using router advertisements (RAs), set `accept_ra=2` **before**
enabling forwarding so the WAN continues learning its IPv6 default route.
The [Linux kernel documentation](https://docs.kernel.org/networking/ip-sysctl.html)
explains these settings.

Using `MAVI_WAN` from the previous step, persist and apply the settings:

```bash
sudo tee /etc/sysctl.d/99-mavi-vpn-ipv6.conf >/dev/null <<CONF
net.ipv6.conf.${MAVI_WAN:?Run the WAN check first}.accept_ra = 2
net.ipv6.conf.default.accept_ra = 2
net.ipv6.conf.all.forwarding = 1
CONF
sudo sysctl -p /etc/sysctl.d/99-mavi-vpn-ipv6.conf

sysctl net.ipv6.conf.all.forwarding "net.ipv6.conf.${MAVI_WAN}.accept_ra"
ip -6 route show default
curl -6 --fail --max-time 15 https://www.cloudflare.com/cdn-cgi/trace
```

Expect `forwarding = 1`, `accept_ra = 2`, and working IPv6 connectivity.
The sysctl file persists across reboots; also keep the IPv4 forwarding setting
from [Installation](#installation). If your network manager overrides RA settings,
configure it to keep accepting RAs while forwarding is enabled.

### 3. Configure IPv6 in `.env`

For a **new installation without an existing `.env`**, copy the shared template:

```bash
cp .env.example .env
chmod 600 .env
openssl rand -hex 32
nano .env
```

Paste the generated token into `VPN_AUTH_TOKEN`, then set the IPv6 values shown
below. To also accept connections to the server over IPv6, set
`VPN_BIND_ADDR=[::]:10443` as described in the listener step.

For an **existing installation**, keep your current `.env` and authentication
settings. The same `.env.example` documents all IPv6 options inline.
Open the existing file and update these values without replacing your token or
other settings:

```bash
nano .env
```

```dotenv
VPN_DISABLE_IPV6=false
VPN_NETWORK_V6=fd00::/64
VPN_IPV6_WAIT=30
VPN_DNS_V6=2606:4700:4700::1111
```

`VPN_DNS_V6` is optional; the address above is also the server's default.
`VPN_IPV6_WAIT` is the number of seconds to wait for an IPv6 address on the WAN
at startup. If none appears, the server logs a warning and continues IPv4-only.

#### Optional: accept VPN connections over IPv6

To listen on IPv6, set the following in `.env`. Brackets are required around an
IPv6 address when it is followed by a port:

```dotenv
VPN_BIND_ADDR=[::]:10443
# Only if you also want the optional HTTP/2 listener:
# VPN_HTTP2_BIND_ADDR=[::]:10443
```

`[::]` binds to all local IPv6 addresses. On Linux, the wildcard listener also
accepts IPv4 connections when `net.ipv6.bindv6only=0` (the kernel default). Check
the host setting before switching an existing IPv4 deployment:

```bash
sysctl net.ipv6.bindv6only
```

If it is `1`, the IPv6 listener will not also accept IPv4. Keep the IPv4 listener
if you only need IPv6 traffic inside the tunnel, or configure the host's socket
default for dual-stack use before recreating the container.

Use the VPS's public IPv6 address or a hostname with an `AAAA` record in the
client. For a combined address-and-port field, use `[2001:db8::1234]:10443`
(replace this documentation address with your real address). For separate host
and port fields, enter the IPv6 address and `10443` separately. Keep the existing
token, certificate pin, and matching transport/CR settings.

### 4. Allow the listener through the firewalls

For connections to the server over IPv6, allow inbound `10443/UDP` in both the
host firewall and the provider's **IPv6** firewall. Also allow `10443/TCP` if you
enabled HTTP/2. Use your configured port if it differs. IPv4 firewall rules alone
do not open IPv6 access. Allow essential ICMPv6, including neighbor discovery,
router advertisements where used, and Packet Too Big messages.

For a host already using UFW, verify `IPV6=yes` in `/etc/default/ufw`, then add
the listener rules ([UFW documentation](https://manpages.ubuntu.com/manpages/noble/man8/ufw.8.html)):

```bash
grep '^IPV6=' /etc/default/ufw
sudo ufw allow 10443/udp
# Only when HTTP/2 is enabled:
# sudo ufw allow 10443/tcp
sudo ufw status verbose
```

An active UFW configuration should show the matching `(v6)` rules. If you change
`IPV6` from `no` to `yes`, reload UFW and check again. Apply firewall changes
before the next step: the server installs its own NAT66 and forwarding chains
on startup.

### 5. Apply and verify

```bash
docker compose config --quiet
docker compose pull
docker compose up -d --force-recreate vpn-server
docker compose logs --tail=150 vpn-server

# Use your VPN_TUN_DEVICE value if you changed the default mavi0.
ip -6 addr show dev mavi0
docker compose exec vpn-server ip6tables -t nat -S MAVI_VPN6_NAT
docker compose exec vpn-server ip6tables -S MAVI_VPN6_FORWARD

# Only when VPN_BIND_ADDR=[::]:10443 is configured:
sudo ss -6 -lunp 'sport = :10443'
# Only when the IPv6 HTTP/2 listener is enabled:
# sudo ss -6 -ltnp 'sport = :10443'
```

Expect `NAT66 configured: fd00::/64 -> <WAN> (IPv6)` in the logs,
`fd00::1/64` on `mavi0`, and a `MASQUERADE` rule in `MAVI_VPN6_NAT`.
The listener check confirms the local socket; reconnect a client to verify
reachability through the host and provider firewalls.

After reconnecting, run these on the **VPN client** (on Windows, use `curl.exe`):

```bash
curl -4 --fail --max-time 15 https://www.cloudflare.com/cdn-cgi/trace
curl -6 --fail --max-time 15 https://www.cloudflare.com/cdn-cgi/trace
```

With full-tunnel routing, both `ip=` results should be the VPS's public addresses,
not the client's ISP addresses. This checks IPv6 internet access through the
tunnel even when the VPN connection itself uses IPv4.

### Troubleshooting and returning to IPv4-only

- **`IPv6 forwarding is not enabled`:** Reapply the host sysctl file from step 2,
  then recreate the container. Changing `.env` alone cannot enable forwarding.
- **`no global IPv6 ... continuing IPv4-only`:** Check the WAN address and route
  from step 1. If address assignment is only slow at boot, increase
  `VPN_IPV6_WAIT` (for example to `60`) and recreate the container.
- **Host IPv6 works, but client IPv6 fails:** Check the TUN address, NAT66 and
  forwarding chains above, reconnect the client, and check its IPv6 routes.
  After a firewall reload, restart `vpn-server` to reinstall its managed chains.
- **IPv4 connections fail after changing to `[::]`:** Check `net.ipv6.bindv6only`
  and the IPv4 firewall rules, or restore `VPN_BIND_ADDR=0.0.0.0:10443`.

To return to IPv4-only operation, set `VPN_DISABLE_IPV6=true` and
`VPN_BIND_ADDR=0.0.0.0:10443` in `.env`. If HTTP/2 is enabled, also restore
`VPN_HTTP2_BIND_ADDR=0.0.0.0:10443`. Then apply the change and reconnect clients:

```bash
docker compose up -d --force-recreate vpn-server
docker compose logs --tail=100 vpn-server
```

The server removes its managed IPv6 firewall chains during shutdown and skips
IPv6 tunnel setup on the next start. The host's own IPv6 connectivity and sysctl
configuration remain in place.

See the [server installation guide](https://github.com/zerox80/mavi-vpn/blob/main/docs/INSTALLATION.md)
for more details.

## Optional: HTTP/2 and Keycloak

To enable HTTP/2, set `VPN_HTTP2_BIND_ADDR=0.0.0.0:10443` and allow `10443/TCP`
through the firewalls. Select HTTP/2 in the client without CR, HTTP/3 framing,
or ECH. The UDP listener remains available alongside HTTP/2.

To use an existing Keycloak server, configure `VPN_KEYCLOAK_ENABLED=true`,
`VPN_KEYCLOAK_URL`, the realm, and the client ID in `.env`. Roles and scopes are
optional. A static `VPN_AUTH_TOKEN` is not required when Keycloak is enabled.
This repository does not include a Keycloak or Traefik stack; the full version
is available in the [source project](https://github.com/zerox80/mavi-vpn/tree/main/backend).

## Publishing the image (maintainers)

The deployment files were imported from [zerox80/mavi-vpn-docker](https://github.com/zerox80/mavi-vpn-docker).
They now live beside the server source in `docker/server/`; the Android build
is documented separately in [`Android-docker-build/`](../../Android-docker-build/README.md).

When switching publishing to this repository, disable the old repository's
`Publish VPN image` workflow so both repositories do not overwrite the same
`latest` tag. In the existing GHCR package settings, grant `zerox80/mavi`
Actions access with write permission if it is currently linked only to the
old repository.

The `Publish VPN server image` workflow runs when server/build inputs change on
`main`, is scheduled daily at 06:17 UTC, and can be started manually through
**Actions → Run workflow**. Server source and build input changes trigger a
build directly. The daily run also picks up new h2/h3/Quinn fork commits.

The workflow:

1. Records the triggering `main` commit from this repository.
2. Updates all seven `zerox80` fork packages to their latest branch commits
   and verifies their sources using the existing fork verification script.
3. Builds AMD64 and ARM64 natively on separate GitHub runners using the same
   source revision and updated `Cargo.lock`. No tests are run.
4. Publishes `latest` and the build tag only after both builds succeed.

The source code and image definition use the same recorded repository revision.
The publishing workflow refreshes `Cargo.lock` in its build checkout; the existing
`Update forks` workflow maintains the committed fork revisions. The build lockfile is saved as
an Actions artifact and included in the image at `/usr/share/mavi-vpn/Cargo.lock`.
The server revision is recorded in the OCI label `org.opencontainers.image.revision`;
the same revision is in `io.mavi-vpn.deployment.revision`.

GHCR authentication automatically uses `GITHUB_TOKEN` with `packages: write`.
No additional registry secrets are required.
**After the first publication, set the GHCR package visibility to Public:**
GitHub profile → Packages → `mavi-vpn` → Package settings → Change visibility → Public.
A public Git repository does not automatically make a new container package
public. This step allows `docker compose pull` to work without logging in to GitHub.
See the [GitHub Container Registry documentation](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).

## License

[MIT](LICENSE). The included VPN server comes from
[zerox80/mavi-vpn](https://github.com/zerox80/mavi-vpn).
