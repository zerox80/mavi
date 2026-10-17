use super::*;
use crate::client_config::{load_config_from_path, save_config_to_path};
use crate::ipc::Config;
use crate::secrets::{config_token_account, tests::MemorySecretStore};
use std::cell::{Cell, RefCell};

fn update(token: &str) -> IpcResponse {
    IpcResponse::RefreshTokenUpdate {
        connection_id: Some(WINDOWS_CLI_CONNECTION_ID.into()),
        refresh_token: Some(token.into()),
    }
}

fn no_update() -> IpcResponse {
    IpcResponse::RefreshTokenUpdate {
        connection_id: None,
        refresh_token: None,
    }
}

fn fetch_request() -> IpcRequest {
    IpcRequest::TakeRefreshTokenUpdateFor {
        connection_id: WINDOWS_CLI_CONNECTION_ID.into(),
    }
}

#[tokio::test]
async fn credential_transactions_are_serialized_and_cancelled_waiters_release_the_file() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("config.json");
    let first = lock_credentials(&path).await.unwrap();
    assert!(
        tokio::time::timeout(Duration::from_millis(100), lock_credentials(&path))
            .await
            .is_err()
    );
    drop(first);
    let second = tokio::time::timeout(Duration::from_secs(1), lock_credentials(&path))
        .await
        .unwrap()
        .unwrap();
    drop(second);
    assert!(path.with_file_name("keycloak-credentials.lock").exists());
}

#[tokio::test]
async fn restart_loads_the_service_rotation_from_the_cli_keyring() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("config.json");
    let store = MemorySecretStore::default();
    let config = Config {
        endpoint: "vpn.example.test:443".into(),
        token: "access-token".into(),
        cert_pin: "pin".into(),
        censorship_resistant: false,
        http3_framing: false,
        http2_framing: false,
        kc_auth: Some(true),
        kc_url: Some("https://auth.example.test".into()),
        kc_realm: None,
        kc_client_id: None,
        refresh_token: Some("consumed-refresh-token".into()),
        ech_config: None,
        vpn_mtu: None,
    };
    save_config_to_path(&path, &config, &store).unwrap();
    let pending = Cell::new(true);
    sync_refresh_tokens(&store, |request| {
        let response = match request {
            request if request == fetch_request() => {
                if pending.get() {
                    update("rotated-secret")
                } else {
                    no_update()
                }
            }
            IpcRequest::AcknowledgeRefreshTokenUpdate {
                connection_id,
                refresh_token,
            } => {
                assert_eq!(connection_id, WINDOWS_CLI_CONNECTION_ID);
                assert_eq!(refresh_token, "rotated-secret");
                assert_eq!(
                    store.secret(config_refresh_token_account()).as_deref(),
                    Some("rotated-secret")
                );
                pending.set(false);
                IpcResponse::Ok
            }
            other => panic!("Unexpected request: {other:?}"),
        };
        std::future::ready(Ok(response))
    })
    .await
    .unwrap();

    let restarted = load_config_from_path(&path, &store).unwrap().unwrap();
    assert_eq!(restarted.refresh_token.as_deref(), Some("rotated-secret"));
    assert_eq!(restarted.token, "access-token");
    assert_eq!(
        store.secret(config_token_account()).as_deref(),
        Some("access-token")
    );
    assert!(!std::fs::read_to_string(path)
        .unwrap()
        .contains("rotated-secret"));
    assert!(!pending.get());
}

struct FailingStore;

impl SecretStore for FailingStore {
    fn set_secret(&self, _: &str, _: &str) -> Result<()> {
        anyhow::bail!("keyring unavailable")
    }
    fn get_secret(&self, _: &str) -> Result<Option<String>> {
        unreachable!()
    }
    fn delete_secret(&self, _: &str) -> Result<()> {
        unreachable!()
    }
}

#[tokio::test]
async fn storage_failure_never_acknowledges_the_service_rotation() {
    let error = sync_refresh_tokens(&FailingStore, |request| {
        assert_eq!(request, fetch_request());
        std::future::ready(Ok(update("retryable-secret")))
    })
    .await
    .unwrap_err();
    assert!(format!("{error:#}").contains("keyring unavailable"));
}

#[tokio::test]
async fn lost_acknowledgement_retries_storage_and_acknowledgement() {
    let store = MemorySecretStore::default();
    for fail_ack in [true, false] {
        let pending = Cell::new(true);
        let result = sync_refresh_tokens(&store, |request| {
            let response = match request {
                request if request == fetch_request() => Ok(if pending.get() {
                    update("rotated-secret")
                } else {
                    no_update()
                }),
                IpcRequest::AcknowledgeRefreshTokenUpdate { .. } => {
                    assert_eq!(
                        store.secret(config_refresh_token_account()).as_deref(),
                        Some("rotated-secret")
                    );
                    if fail_ack {
                        Err(anyhow::anyhow!("pipe disconnected"))
                    } else {
                        pending.set(false);
                        Ok(IpcResponse::Ok)
                    }
                }
                other => panic!("Unexpected request: {other:?}"),
            };
            std::future::ready(response)
        })
        .await;
        assert_eq!(result.is_err(), fail_ack);
        assert_eq!(pending.get(), fail_ack);
    }
}

#[tokio::test]
async fn another_rotation_during_acknowledgement_is_also_saved() {
    let store = MemorySecretStore::default();
    let pending = RefCell::new(Some("rotation-1"));
    sync_refresh_tokens(&store, |request| {
        let response = match request {
            request if request == fetch_request() => {
                pending.borrow().map_or_else(no_update, update)
            }
            IpcRequest::AcknowledgeRefreshTokenUpdate { refresh_token, .. } => {
                *pending.borrow_mut() = if refresh_token == "rotation-1" {
                    Some("rotation-2")
                } else {
                    None
                };
                IpcResponse::Ok
            }
            other => panic!("Unexpected request: {other:?}"),
        };
        std::future::ready(Ok(response))
    })
    .await
    .unwrap();
    assert_eq!(
        store.secret(config_refresh_token_account()).as_deref(),
        Some("rotation-2")
    );
}

#[tokio::test]
async fn no_update_preserves_existing_cli_credentials() {
    let store = MemorySecretStore::default();
    store
        .set_secret(config_refresh_token_account(), "existing-secret")
        .unwrap();
    sync_refresh_tokens(&store, |request| {
        assert_eq!(request, fetch_request());
        std::future::ready(Ok(no_update()))
    })
    .await
    .unwrap();
    assert_eq!(
        store.secret(config_refresh_token_account()).as_deref(),
        Some("existing-secret")
    );
}

#[tokio::test]
async fn malformed_foreign_and_rejected_updates_are_not_saved_or_acknowledged() {
    for response in [
        update(""),
        update("   "),
        IpcResponse::RefreshTokenUpdate {
            connection_id: Some("gui-profile".into()),
            refresh_token: Some("gui-secret".into()),
        },
        IpcResponse::RefreshTokenUpdate {
            connection_id: None,
            refresh_token: Some("secret".into()),
        },
        IpcResponse::Error("access denied".into()),
        IpcResponse::Ok,
    ] {
        let response = RefCell::new(Some(response));
        let store = MemorySecretStore::default();
        assert!(sync_refresh_tokens(&store, |request| {
            assert_eq!(request, fetch_request());
            std::future::ready(Ok(response.borrow_mut().take().unwrap()))
        })
        .await
        .is_err());
        assert!(store.secret(config_refresh_token_account()).is_none());
    }
}

#[tokio::test]
async fn failed_fetch_and_rejected_acknowledgement_are_reported() {
    let store = MemorySecretStore::default();
    assert!(
        sync_refresh_tokens(&store, |_| std::future::ready(Err(anyhow::anyhow!(
            "pipe unavailable"
        ))))
        .await
        .is_err()
    );
    for response in [IpcResponse::Error("retry".into()), no_update()] {
        let response = RefCell::new(Some(response));
        assert!(sync_refresh_tokens(&store, |request| {
            std::future::ready(Ok(if request == fetch_request() {
                update("saved-secret")
            } else {
                response.borrow_mut().take().unwrap()
            }))
        })
        .await
        .is_err());
        assert_eq!(
            store.secret(config_refresh_token_account()).as_deref(),
            Some("saved-secret")
        );
    }
}
