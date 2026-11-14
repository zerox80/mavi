#[cfg(target_os = "linux")]
use std::path::PathBuf;

#[cfg(target_os = "linux")]
fn daemon_binary() -> Result<PathBuf, String> {
    // Installed DEB/RPM packages contain both binaries. The sibling path also
    // supports testing an extracted, matching GUI/CLI build together.
    let sibling = std::env::current_exe()
        .ok()
        .and_then(|path| path.parent().map(|parent| parent.join("mavi-vpn")));
    sibling
        .into_iter()
        .chain([PathBuf::from("/usr/local/bin/mavi-vpn")])
        .find(|path| path.is_file())
        .ok_or_else(|| {
            "Install the Mavi VPN GUI package (.rpm or .deb); it includes the VPN service.".into()
        })
}

#[tauri::command]
pub(crate) async fn setup_vpn_service() -> Result<(), String> {
    #[cfg(target_os = "linux")]
    {
        let binary = daemon_binary()?;
        let result = tauri::async_runtime::spawn_blocking(move || {
            std::process::Command::new("pkexec")
                .arg("--disable-internal-agent")
                .arg(binary)
                .arg("setup-service")
                .output()
        })
        .await
        .map_err(|_| "VPN service setup could not finish".to_string())?
        .map_err(|_| {
            "Could not open the system authorization prompt. Install Polkit and try again."
                .to_string()
        })?;
        if matches!(result.status.code(), Some(126 | 127)) {
            return Err(
                "System authorization was cancelled or denied. Select Set up VPN service to retry."
                    .into(),
            );
        }
        if !result.status.success() {
            return Err(format!(
                "VPN service setup failed: {}",
                String::from_utf8_lossy(&result.stderr).trim()
            ));
        }
        match crate::ipc::send_ipc_request(&shared::ipc::IpcRequest::Status).await? {
            shared::ipc::IpcResponse::Status { .. } => Ok(()),
            shared::ipc::IpcResponse::Error(error) => Err(error),
            _ => Err("The VPN service returned an unexpected setup response".into()),
        }
    }
    #[cfg(not(target_os = "linux"))]
    Err("Use the Mavi VPN installer to set up the VPN service on this system.".into())
}
