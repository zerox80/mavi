use super::*;
use std::cell::Cell;

#[derive(Default)]
struct FlakyDeletes {
    inner: MemorySecretStore,
    fail: Cell<bool>,
}

impl SecretStore for FlakyDeletes {
    fn set_secret(&self, account: &str, secret: &str) -> Result<(), String> {
        self.inner.set_secret(account, secret)
    }

    fn get_secret(&self, account: &str) -> Result<Option<String>, String> {
        self.inner.get_secret(account)
    }

    fn delete_secret(&self, account: &str) -> Result<(), String> {
        if self.fail.get() {
            return Err("Keyring temporarily locked".into());
        }
        self.inner.delete_secret(account)
    }
}

fn profile_prefs() -> Prefs {
    Prefs {
        connections: vec![SavedConn {
            id: "one".into(),
            token: Some("saved-psk".into()),
            kc_auth: Some(true),
            kc_url: Some("https://auth.example.com".into()),
            ..SavedConn::default()
        }],
        ..Prefs::default()
    }
}

fn pending_accounts(dir: &Path) -> Vec<String> {
    let content = std::fs::read_to_string(dir.join("prefs.json")).unwrap();
    let saved: serde_json::Value = serde_json::from_str(&content).unwrap();
    saved["pending_credential_deletions"]
        .as_array()
        .into_iter()
        .flatten()
        .map(|account| account.as_str().unwrap().to_owned())
        .collect()
}

#[test]
fn failed_deletions_survive_repeated_failures_and_retry_on_save_or_load() {
    for retry_on_load in [false, true] {
        let dir = tempfile::tempdir().unwrap();
        let store = FlakyDeletes::default();
        let mut prefs = profile_prefs();
        save_prefs_to_dir_with_store(dir.path(), &mut prefs, &store).unwrap();
        let accounts = [
            connection_token_account("one"),
            connection_refresh_token_account("one"),
            prefs.connections[0].refresh_account().unwrap(),
        ];
        for account in &accounts[1..] {
            store.set_secret(account, "saved-refresh").unwrap();
        }

        prefs.connections.clear();
        store.fail.set(true);
        save_prefs_to_dir_with_store(dir.path(), &mut prefs, &store).unwrap();
        // Reload fresh state while cleanup still fails, then save it again.
        let mut reloaded = load_prefs_from_dir_with_store(dir.path(), &store).unwrap();
        assert!(reloaded.connections.is_empty());
        save_prefs_to_dir_with_store(dir.path(), &mut reloaded, &store).unwrap();
        let pending = pending_accounts(dir.path());
        for account in &accounts {
            assert!(pending.contains(account));
            assert!(store.inner.secret(account).is_some());
        }
        let content = std::fs::read_to_string(dir.path().join("prefs.json")).unwrap();
        assert!(!content.contains("saved-psk"));
        assert!(!content.contains("saved-refresh"));

        store.fail.set(false);
        if retry_on_load {
            load_prefs_from_dir_with_store(dir.path(), &store).unwrap();
        } else {
            save_prefs_to_dir_with_store(dir.path(), &mut reloaded, &store).unwrap();
        }
        for account in &accounts {
            assert!(store.inner.secret(account).is_none());
        }
        assert!(pending_accounts(dir.path()).is_empty());
    }
}

#[test]
fn failed_authority_cleanup_retries_without_deleting_current_credentials() {
    let dir = tempfile::tempdir().unwrap();
    let store = FlakyDeletes::default();
    let mut prefs = profile_prefs();
    save_prefs_to_dir_with_store(dir.path(), &mut prefs, &store).unwrap();
    let previous_account = prefs.connections[0].refresh_account().unwrap();
    store.set_secret(&previous_account, "old-refresh").unwrap();

    prefs.connections[0].kc_realm = Some("new-realm".into());
    let current_account = prefs.connections[0].refresh_account().unwrap();
    store
        .set_secret(&current_account, "current-refresh")
        .unwrap();
    store.fail.set(true);
    save_prefs_to_dir_with_store(dir.path(), &mut prefs, &store).unwrap();
    assert_eq!(pending_accounts(dir.path()), vec![previous_account.clone()]);

    store.fail.set(false);
    load_prefs_from_dir_with_store(dir.path(), &store).unwrap();
    assert!(store.inner.secret(&previous_account).is_none());
    assert_eq!(
        store.inner.secret(&current_account).as_deref(),
        Some("current-refresh")
    );
    assert_eq!(
        store
            .inner
            .secret(&connection_token_account("one"))
            .as_deref(),
        Some("saved-psk")
    );
}

#[test]
fn reintroduced_profile_cancels_pending_deletions_for_its_accounts() {
    let dir = tempfile::tempdir().unwrap();
    let store = FlakyDeletes::default();
    let mut prefs = profile_prefs();
    save_prefs_to_dir_with_store(dir.path(), &mut prefs, &store).unwrap();
    let original = prefs.clone();
    let refresh = prefs.connections[0].refresh_account().unwrap();
    let legacy_refresh = connection_refresh_token_account("one");
    store.set_secret(&refresh, "current-refresh").unwrap();
    store.set_secret(&legacy_refresh, "legacy-refresh").unwrap();
    prefs.connections.clear();
    store.fail.set(true);
    save_prefs_to_dir_with_store(dir.path(), &mut prefs, &store).unwrap();

    store.fail.set(false);
    let mut restored = original;
    restored.connections[0].token = Some("new-psk".into());
    save_prefs_to_dir_with_store(dir.path(), &mut restored, &store).unwrap();
    let loaded = load_prefs_from_dir_with_store(dir.path(), &store).unwrap();
    assert_eq!(loaded.connections[0].token.as_deref(), Some("new-psk"));
    assert_eq!(
        store.inner.secret(&refresh).as_deref(),
        Some("current-refresh")
    );
    assert_eq!(
        store.inner.secret(&legacy_refresh).as_deref(),
        Some("legacy-refresh")
    );
    assert!(pending_accounts(dir.path()).is_empty());
    assert!(store.inner.deleted().is_empty());
}

#[test]
fn failed_cleanup_checkpoint_keeps_queue_for_an_idempotent_retry() {
    let dir = tempfile::tempdir().unwrap();
    let store = MemorySecretStore::default();
    let mut prefs = profile_prefs();
    save_prefs_to_dir_with_store(dir.path(), &mut prefs, &store).unwrap();
    prefs.connections.clear();
    let mut writes = 0;
    save_prefs_with_writer(dir.path(), &mut prefs, &store, |path, content| {
        writes += 1;
        if writes == 2 {
            return Err("Disk full during cleanup checkpoint".into());
        }
        write_prefs_atomically(path, content).map_err(|e| e.to_string())
    })
    .unwrap();
    assert_eq!(writes, 2);
    assert!(!pending_accounts(dir.path()).is_empty());
    assert!(store.secret(&connection_token_account("one")).is_none());
    let loaded = load_prefs_from_dir_with_store(dir.path(), &store).unwrap();
    assert!(loaded.connections.is_empty());
    assert!(pending_accounts(dir.path()).is_empty());
}

#[test]
fn frontend_cannot_inject_pending_credential_deletions() {
    let dir = tempfile::tempdir().unwrap();
    let store = MemorySecretStore::default();
    store.set_secret("unrelated-account", "keep-me").unwrap();
    let mut prefs: Prefs = serde_json::from_value(serde_json::json!({
        "pending_credential_deletions": ["unrelated-account"]
    }))
    .unwrap();
    save_prefs_to_dir_with_store(dir.path(), &mut prefs, &store).unwrap();
    assert_eq!(
        store.secret("unrelated-account").as_deref(),
        Some("keep-me")
    );
    assert!(pending_accounts(dir.path()).is_empty());
    assert!(serde_json::to_value(&prefs)
        .unwrap()
        .get("pending_credential_deletions")
        .is_none());
}

#[cfg(unix)]
#[test]
fn atomic_prefs_write_preserves_existing_permissions() {
    use std::os::unix::fs::PermissionsExt;
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("prefs.json");
    for mode in [0o600, 0o640] {
        std::fs::write(&path, "old").unwrap();
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(mode)).unwrap();
        write_prefs_atomically(&path, "new").unwrap();
        assert_eq!(std::fs::read_to_string(&path).unwrap(), "new");
        assert_eq!(
            std::fs::metadata(&path).unwrap().permissions().mode() & 0o777,
            mode
        );
    }
}

#[cfg(unix)]
#[test]
fn new_prefs_file_is_private() {
    use std::os::unix::fs::PermissionsExt;
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("prefs.json");
    write_prefs_atomically(&path, "new").unwrap();
    assert_eq!(
        std::fs::metadata(&path).unwrap().permissions().mode() & 0o077,
        0
    );
}
