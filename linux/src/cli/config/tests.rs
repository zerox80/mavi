use super::*;
use crate::cli::secrets::tests::{sample_config, MemorySecretStore, UnavailableSecretStore};

#[cfg(unix)]
#[test]
fn save_config_replaces_symlink_without_touching_target() -> Result<()> {
    use std::os::unix::fs::{symlink, PermissionsExt};

    let temp = tempfile::tempdir()?;
    let config_path = temp.path().join("mavi-vpn.json");
    let symlink_target = temp.path().join("target.json");
    std::fs::write(&symlink_target, "do-not-overwrite")?;
    symlink(&symlink_target, &config_path)?;

    save_config(
        &sample_config(true),
        &config_path,
        &MemorySecretStore::default(),
    )?;

    assert!(!std::fs::symlink_metadata(&config_path)?
        .file_type()
        .is_symlink());
    assert_eq!(
        std::fs::read_to_string(&symlink_target)?,
        "do-not-overwrite"
    );
    let mode = std::fs::metadata(&config_path)?.permissions().mode() & 0o777;
    assert_eq!(mode, 0o600);
    Ok(())
}

#[test]
fn saved_config_file_contains_no_tokens() -> Result<()> {
    let temp = tempfile::tempdir()?;
    let config_path = temp.path().join("mavi-vpn.json");
    let store = MemorySecretStore::default();

    save_config(&sample_config(true), &config_path, &store)?;

    let on_disk = std::fs::read_to_string(&config_path)?;
    assert!(!on_disk.contains("access-token"));
    assert!(!on_disk.contains("refresh-token"));
    let loaded = load_config(&config_path, &store).expect("config loads");
    assert_eq!(loaded.token, "access-token");
    assert_eq!(loaded.refresh_token.as_deref(), Some("refresh-token"));
    Ok(())
}

#[test]
fn loading_a_legacy_plaintext_config_migrates_its_tokens() -> Result<()> {
    let temp = tempfile::tempdir()?;
    let config_path = temp.path().join("mavi-vpn.json");
    std::fs::write(
        &config_path,
        serde_json::to_string_pretty(&sample_config(true))?,
    )?;
    let store = MemorySecretStore::default();

    let loaded = load_config(&config_path, &store).expect("config loads");

    assert_eq!(loaded.token, "access-token");
    assert_eq!(loaded.refresh_token.as_deref(), Some("refresh-token"));
    let on_disk = std::fs::read_to_string(&config_path)?;
    assert!(!on_disk.contains("access-token"));
    assert!(!on_disk.contains("refresh-token"));
    Ok(())
}

#[test]
fn legacy_refresh_token_is_dropped_from_disk_without_a_keyring() -> Result<()> {
    let temp = tempfile::tempdir()?;
    let config_path = temp.path().join("mavi-vpn.json");
    std::fs::write(
        &config_path,
        serde_json::to_string_pretty(&sample_config(true))?,
    )?;

    let loaded = load_config(&config_path, &UnavailableSecretStore).expect("config loads");

    // The current run can still use the tokens it read...
    assert_eq!(loaded.refresh_token.as_deref(), Some("refresh-token"));
    // ...but they no longer persist on disk.
    let on_disk = std::fs::read_to_string(&config_path)?;
    assert!(!on_disk.contains("access-token"));
    assert!(!on_disk.contains("refresh-token"));
    Ok(())
}

#[test]
fn runtime_rotation_is_restored_from_the_selected_configs_keyring() -> Result<()> {
    let temp = tempfile::tempdir()?;
    let config_path = temp.path().join("custom-profile.json");
    let other_path = temp.path().join("mavi-vpn.json");
    let store = MemorySecretStore::default();
    save_config(&sample_config(true), &config_path, &store)?;
    save_config(&sample_config(true), &other_path, &store)?;
    let on_disk = std::fs::read(&config_path)?;
    let persist = secrets::token_persistence(&config_path, store.clone());

    // Reload while the callback is still alive: persistence must happen during
    // refresh, without relying on a graceful VPN shutdown.
    persist(&shared::kc_oauth::OAuthTokens {
        access_token: "new-access-token".into(),
        refresh_token: Some("rotated-refresh-token".into()),
    })?;
    let loaded = load_config(&config_path, &store).expect("config loads");
    assert_eq!(loaded.token, "new-access-token");
    assert_eq!(
        loaded.refresh_token.as_deref(),
        Some("rotated-refresh-token")
    );
    assert_eq!(std::fs::read(&config_path)?, on_disk);
    let other = load_config(&other_path, &store).expect("other config loads");
    assert_eq!(other.token, "access-token");
    assert_eq!(other.refresh_token.as_deref(), Some("refresh-token"));

    // Non-rotating responses must keep the last usable refresh credential.
    persist(&shared::kc_oauth::OAuthTokens {
        access_token: "newest-access-token".into(),
        refresh_token: None,
    })?;
    let loaded = load_config(&config_path, &store).expect("config loads again");
    assert_eq!(loaded.token, "newest-access-token");
    assert_eq!(
        loaded.refresh_token.as_deref(),
        Some("rotated-refresh-token")
    );
    Ok(())
}

#[test]
fn runtime_keyring_failure_does_not_write_tokens_to_the_config() -> Result<()> {
    let temp = tempfile::tempdir()?;
    let config_path = temp.path().join("mavi-vpn.json");
    save_config(&sample_config(true), &config_path, &UnavailableSecretStore)?;
    let on_disk = std::fs::read(&config_path)?;
    let persist = secrets::token_persistence(&config_path, UnavailableSecretStore);

    assert!(persist(&shared::kc_oauth::OAuthTokens {
        access_token: "new-access-token".into(),
        refresh_token: Some("rotated-refresh-token".into()),
    })
    .is_err());
    assert_eq!(std::fs::read(&config_path)?, on_disk);
    Ok(())
}
