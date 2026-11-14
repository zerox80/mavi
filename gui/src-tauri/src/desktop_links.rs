//! Reuse one desktop identity for the application menu and enrollment links.
use std::path::Path;
use std::process::Command;
use tauri::Manager;
use tauri_plugin_deep_link::DeepLinkExt;

const DESKTOP_ID: &str = "Mavi VPN.desktop";
const LEGACY_ID: &str = "mavi-vpn-gui-handler.desktop";
const GENERATED_MARKER: &str = "X-Mavi-Generated=true";

pub(crate) fn register(app: &tauri::App) -> Result<(), String> {
    let executable = std::env::current_exe().map_err(|error| error.to_string())?;
    let packaged = executable == Path::new("/usr/bin/mavi-vpn-gui")
        && Path::new("/usr/share/applications/Mavi VPN.desktop").is_file();
    let applications = app
        .path()
        .data_dir()
        .map_err(|error| error.to_string())?
        .join("applications");
    if !packaged {
        // Portable/dev builds still need the plugin to construct an Exec entry.
        app.deep_link()
            .register_all()
            .map_err(|error| error.to_string())?;
    }
    reconcile_entries(&applications, packaged).map_err(|error| error.to_string())?;
    let result = Command::new("xdg-mime")
        .args(["default", DESKTOP_ID, "x-scheme-handler/mavivpn"])
        .status()
        .map_err(|error| error.to_string())?;
    if !result.success() {
        return Err("Could not select Mavi VPN as the enrollment link handler".into());
    }
    // Removing the old desktop file must also remove its MIME cache entry.
    let _ = Command::new("update-desktop-database")
        .arg(&applications)
        .status();
    Ok(())
}

fn reconcile_entries(applications: &Path, packaged: bool) -> std::io::Result<()> {
    std::fs::create_dir_all(applications)?;
    let canonical = applications.join(DESKTOP_ID);
    let legacy = applications.join(LEGACY_ID);
    if packaged {
        // Only remove our generated override, preserving custom desktop entries.
        if std::fs::read_to_string(&canonical)
            .is_ok_and(|text| text.lines().any(|line| line == GENERATED_MARKER))
        {
            std::fs::remove_file(&canonical)?;
        }
    }
    let text = match std::fs::read_to_string(&legacy) {
        Ok(text) => text,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(()),
        Err(error) => return Err(error),
    };
    // The previous plugin version created this particular filename. Leave any
    // unrelated handler alone instead of deleting arbitrary user customization.
    if !text.lines().any(|line| line == "Name=Mavi VPN")
        || !text
            .lines()
            .any(|line| line.starts_with("MimeType=") && line.contains("x-scheme-handler/mavivpn"))
    {
        return Ok(());
    }
    if !packaged {
        let icon = if text.lines().any(|line| line.starts_with("Icon=")) {
            ""
        } else {
            "Icon=mavi-vpn-gui\n"
        };
        std::fs::write(&canonical, format!("{text}\n{icon}{GENERATED_MARKER}\n"))?;
    }
    std::fs::remove_file(legacy)
}

#[cfg(test)]
mod tests {
    use super::*;
    const LEGACY: &str = "[Desktop Entry]\nName=Mavi VPN\nExec=\"/usr/bin/mavi-vpn-gui\" %u\nMimeType=x-scheme-handler/mavivpn\n";

    #[test]
    fn packaged_app_removes_the_generated_duplicate_and_is_idempotent() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join(LEGACY_ID), LEGACY).unwrap();
        std::fs::write(
            dir.path().join(DESKTOP_ID),
            format!("{LEGACY}{GENERATED_MARKER}\n"),
        )
        .unwrap();
        reconcile_entries(dir.path(), true).unwrap();
        reconcile_entries(dir.path(), true).unwrap();
        assert!(!dir.path().join(LEGACY_ID).exists());
        assert!(!dir.path().join(DESKTOP_ID).exists());
    }

    #[test]
    fn portable_build_reuses_the_packaged_desktop_identity_and_keeps_url_argument() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join(LEGACY_ID), LEGACY).unwrap();
        reconcile_entries(dir.path(), false).unwrap();
        let text = std::fs::read_to_string(dir.path().join(DESKTOP_ID)).unwrap();
        assert!(text.contains(" %u\n"));
        assert!(text.contains(GENERATED_MARKER));
        assert!(!dir.path().join(LEGACY_ID).exists());
    }

    #[test]
    fn does_not_remove_custom_canonical_entries_or_unrelated_handlers() {
        let dir = tempfile::tempdir().unwrap();
        let custom = "[Desktop Entry]\nName=Other application\n";
        std::fs::write(dir.path().join(DESKTOP_ID), custom).unwrap();
        std::fs::write(dir.path().join(LEGACY_ID), custom).unwrap();
        reconcile_entries(dir.path(), true).unwrap();
        assert_eq!(
            std::fs::read_to_string(dir.path().join(DESKTOP_ID)).unwrap(),
            custom
        );
        assert!(dir.path().join(LEGACY_ID).exists());
    }
}
