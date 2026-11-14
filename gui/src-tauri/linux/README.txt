Mavi VPN GUI for Linux

1. Extract the linux-gui ZIP downloaded from GitHub Actions.
2. Install the .rpm package on Fedora/RHEL or the .deb package on Ubuntu/Debian
   using your desktop's software/package installer.
3. Open Mavi VPN from the application menu. If a system authorization prompt
   appears, approve it to enable VPN access for your desktop account.
4. Enroll or add a connection, then select Connect.

The GUI package includes the VPN daemon and its system service. You do not need
the separate linux-cli download, Rust, Python, a terminal command, or a logout.
If you cancel authorization, select "Set up VPN service" in the app to retry.

Requires a Linux desktop with systemd, Polkit, and WebKitGTK 4.1. The package
manager installs declared runtime dependencies. The ZIP itself is an artifact
container; install the matching package rather than treating the ZIP as an app.
