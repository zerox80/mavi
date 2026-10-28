use super::*;
use crate::cli::secrets::tests::{sample_config, MemorySecretStore, UnavailableSecretStore};
use std::cell::RefCell;
use std::collections::VecDeque;

#[test]
fn daemon_start_identifies_the_config_owning_rotated_credentials() {
    let path = Path::new("/tmp/saved-vpn.json");
    let IpcRequest::StartWithKeycloak { config, keycloak } =
        start_request(sample_config(true), path)
    else {
        panic!("CLI Keycloak sessions must return runtime rotations");
    };
    assert_eq!(
        keycloak.connection_id,
        SecretAccounts::for_config(path).refresh_token
    );
    assert_eq!(keycloak.refresh_token, config.refresh_token.unwrap());
    assert!(matches!(
        start_request(sample_config(false), path),
        IpcRequest::Start(_)
    ));
}

#[tokio::test]
async fn rotated_refresh_is_saved_before_ack_and_restored_on_next_load() {
    let path = Path::new("/tmp/saved-vpn.json");
    let store = MemorySecretStore::default();
    let accounts = SecretAccounts::for_config(path);
    let mut saved = crate::cli::secrets::strip_secrets(&sample_config(true), &accounts, &store);
    let replies = RefCell::new(VecDeque::from([
        IpcResponse::RefreshTokenUpdate {
            connection_id: Some(accounts.refresh_token.clone()),
            refresh_token: Some("rotated".into()),
        },
        IpcResponse::Ok,
        IpcResponse::RefreshTokenUpdate {
            connection_id: None,
            refresh_token: None,
        },
    ]));
    sync_refresh_tokens(path, &store, |request| {
        if let IpcRequest::AcknowledgeRefreshTokenUpdate { refresh_token, .. } = request {
            assert_eq!(
                store
                    .get_secret(&accounts.refresh_token)
                    .unwrap()
                    .as_deref(),
                Some(refresh_token.as_str())
            );
        }
        std::future::ready(Ok(replies.borrow_mut().pop_front().unwrap()))
    })
    .await
    .unwrap();
    crate::cli::secrets::restore_secrets(&mut saved, &accounts, &store);
    assert_eq!(saved.refresh_token.as_deref(), Some("rotated"));
    assert!(replies.borrow().is_empty());
}

#[tokio::test]
async fn failed_storage_does_not_acknowledge_the_only_valid_refresh_token() {
    let path = Path::new("/tmp/saved-vpn.json");
    let mut requests = 0;
    assert!(sync_refresh_tokens(path, &UnavailableSecretStore, |_| {
        requests += 1;
        assert_eq!(requests, 1, "must not acknowledge a failed keyring write");
        std::future::ready(Ok(IpcResponse::RefreshTokenUpdate {
            connection_id: Some(SecretAccounts::for_config(path).refresh_token),
            refresh_token: Some("rotated".into()),
        }))
    })
    .await
    .is_err());
}

#[tokio::test]
async fn config_lock_serializes_concurrent_cli_starts() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("config.json");
    let first = lock(&path).await.unwrap();
    let second = lock(&path);
    tokio::pin!(second);
    assert!(
        tokio::time::timeout(Duration::from_millis(100), &mut second)
            .await
            .is_err()
    );
    drop(first);
    tokio::time::timeout(Duration::from_secs(1), second)
        .await
        .unwrap()
        .unwrap();
}
