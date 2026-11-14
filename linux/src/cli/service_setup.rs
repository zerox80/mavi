//! Explicitly authorized desktop setup, invoked through the system Polkit prompt.
use anyhow::{bail, Context, Result};
use nix::unistd::{chown, Group, Uid, User};
use std::os::unix::fs::{FileTypeExt, MetadataExt, PermissionsExt};
use std::path::Path;
use std::process::Command;

pub(super) fn run() -> Result<()> {
    if !Uid::effective().is_root() {
        bail!("VPN service setup needs system administrator approval");
    }
    let uid = std::env::var("PKEXEC_UID")
        .or_else(|_| std::env::var("SUDO_UID"))?
        .parse::<u32>()
        .context("Could not identify the desktop account")?;
    if uid == 0 {
        bail!("Open Mavi VPN from your normal desktop account to set up access");
    }
    let user = User::from_uid(Uid::from_raw(uid))?.context("Desktop account no longer exists")?;
    command("groupadd", &["--system", "--force", "mavivpn"])?;
    command("usermod", &["-a", "-G", "mavivpn", "--", &user.name])?;
    // Preserve any live VPN session: start an inactive service, never restart it.
    command("systemctl", &["enable", "--now", "mavi-vpn.service"])?;
    for _ in 0..100 {
        let token = shared::ipc::ipc_token_path();
        let socket = shared::ipc::ipc_socket_path();
        if token.exists() && socket.exists() {
            validate_ipc_path(&token, false)?;
            validate_ipc_path(&socket, true)?;
            let group = Group::from_name("mavivpn")?.context("VPN control group is missing")?;
            for (path, mode) in [(&token, 0o640), (&socket, 0o660)] {
                chown(path, None, Some(group.gid))?;
                std::fs::set_permissions(path, std::fs::Permissions::from_mode(mode))?;
            }
            std::fs::set_permissions(
                token.parent().context("Missing IPC directory")?,
                std::fs::Permissions::from_mode(0o711),
            )?;
            crate::daemon::access::grant_group_access(&token)?;
            crate::daemon::access::grant_group_access(&socket)?;
            println!("VPN service access is ready. No desktop logout is required.");
            return Ok(());
        }
        std::thread::sleep(std::time::Duration::from_millis(100));
    }
    bail!("The VPN service started but did not create its control socket. Check the service log.");
}

fn command(program: &str, args: &[&str]) -> Result<()> {
    let output = Command::new(program)
        .args(args)
        .env("PATH", "/usr/sbin:/usr/bin:/sbin:/bin")
        .output()?;
    if !output.status.success() {
        bail!(
            "{program} failed: {}",
            String::from_utf8_lossy(&output.stderr).trim()
        );
    }
    Ok(())
}

fn validate_ipc_path(path: &Path, socket: bool) -> Result<()> {
    let parent = std::fs::symlink_metadata(path.parent().context("Missing IPC directory")?)?;
    let file = std::fs::symlink_metadata(path)?;
    if !parent.is_dir()
        || parent.uid() != 0
        || parent.mode() & 0o022 != 0
        || file.uid() != 0
        || file.mode() & 0o007 != 0
        || (socket && !file.file_type().is_socket())
        || (!socket && !file.is_file())
    {
        bail!("The VPN service control files have unsafe ownership or permissions");
    }
    Ok(())
}
