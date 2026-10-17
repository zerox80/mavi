use super::*;
use crate::caller::SessionOwner;
use crate::state::PendingKeycloakRefreshToken;

fn user(sid: &str, session_id: u32) -> Caller {
    Caller {
        owner: SessionOwner {
            sid: sid.into(),
            session_id,
            logon_id: (session_id, 0),
        },
        privileged: false,
    }
}

#[tokio::test]
async fn refreshed_token_is_only_delivered_to_its_logon_owner() {
    let state = Arc::new(Mutex::new(VpnServiceState::new()));
    let alice = user("S-1-5-21-1", 1);
    let bob = user("S-1-5-21-2", 2);
    {
        let mut guard = state.lock().await;
        guard.mark_session_starting(
            &alice.owner.refresh_token_key(),
            crate::tests::test_config(),
            Some("same-profile-id"),
        );
        guard.session_owner = Some(alice.owner.clone());
        guard
            .runtime_handles()
            .publish_keycloak_refresh_token(PendingKeycloakRefreshToken {
                connection_id: "same-profile-id".into(),
                refresh_token: "alice-secret".into(),
            });
        guard.stop_session();
    }
    for caller in [&bob, &Caller::test_admin()] {
        let response = dispatch_as(
            ipc::IpcRequest::TakeRefreshTokenUpdate,
            &state,
            caller,
            || Some((bob.owner.sid.clone(), 2)),
        )
        .await;
        assert!(matches!(
            response,
            ipc::IpcResponse::RefreshTokenUpdate {
                connection_id: None,
                refresh_token: None
            }
        ));
        // Even a guessed exact acknowledgement must not retire Alice's token.
        let _ = dispatch_as(
            ipc::IpcRequest::AcknowledgeRefreshTokenUpdate {
                connection_id: "same-profile-id".into(),
                refresh_token: "alice-secret".into(),
            },
            &state,
            caller,
            || Some((bob.owner.sid.clone(), 2)),
        )
        .await;
    }
    let mut new_logon = user(&alice.owner.sid, 1);
    new_logon.owner.logon_id = (9999, 0);
    assert!(matches!(
        dispatch_as(
            ipc::IpcRequest::TakeRefreshTokenUpdate,
            &state,
            &new_logon,
            || Some((alice.owner.sid.clone(), 1))
        )
        .await,
        ipc::IpcResponse::RefreshTokenUpdate {
            connection_id: None,
            refresh_token: None
        }
    ));
    let _ = dispatch_as(
        ipc::IpcRequest::AcknowledgeRefreshTokenUpdate {
            connection_id: "same-profile-id".into(),
            refresh_token: "alice-secret".into(),
        },
        &state,
        &new_logon,
        || Some((alice.owner.sid.clone(), 1)),
    )
    .await;
    // Unauthorized reads did not consume the owner's pending secret.
    assert!(
        matches!(dispatch_as(ipc::IpcRequest::TakeRefreshTokenUpdate, &state, &alice, || Some((alice.owner.sid.clone(), 1))).await, ipc::IpcResponse::RefreshTokenUpdate { refresh_token: Some(token), .. } if token == "alice-secret")
    );
    assert!(matches!(
        dispatch_as(
            ipc::IpcRequest::AcknowledgeRefreshTokenUpdate {
                connection_id: "same-profile-id".into(),
                refresh_token: "alice-secret".into(),
            },
            &state,
            &alice,
            || Some((alice.owner.sid.clone(), 1))
        )
        .await,
        ipc::IpcResponse::Ok
    ));
    assert!(state
        .lock()
        .await
        .pending_keycloak_refresh_token(&alice.owner.refresh_token_key())
        .is_none());
}

#[tokio::test]
async fn queued_request_rechecks_console_after_acquiring_state_lock() {
    let state = Arc::new(Mutex::new(VpnServiceState::new()));
    let guard = state.lock().await;
    let console = Arc::new(std::sync::Mutex::new(Some(("S-1-5-21-1".into(), 1))));
    let task_state = state.clone();
    let task_console = console.clone();
    let task = tokio::spawn(async move {
        dispatch_as(
            ipc::IpcRequest::UpdateToken {
                token: "old-user-token".into(),
            },
            &task_state,
            &user("S-1-5-21-1", 1),
            || task_console.lock().unwrap().clone(),
        )
        .await
    });
    tokio::task::yield_now().await;
    *console.lock().unwrap() = Some(("S-1-5-21-2".into(), 2));
    drop(guard);
    assert!(matches!(task.await.unwrap(), ipc::IpcResponse::Error(_)));
    assert!(state.lock().await.current_token.lock().unwrap().is_empty());
}

#[tokio::test]
async fn cli_and_gui_rotations_are_delivered_to_their_own_stores_after_stop() {
    let state = Arc::new(Mutex::new(VpnServiceState::new()));
    let alice = user("S-1-5-21-1", 1);
    let bob = user("S-1-5-21-2", 2);
    {
        let mut guard = state.lock().await;
        // The GUI's older, unacknowledged update must not block the CLI fetch.
        for connection_id in ["gui-profile", ipc::WINDOWS_CLI_CONNECTION_ID] {
            guard.mark_session_starting(
                &alice.owner.refresh_token_key(),
                crate::tests::test_config(),
                Some(connection_id),
            );
            guard
                .runtime_handles()
                .publish_keycloak_refresh_token(PendingKeycloakRefreshToken {
                    connection_id: connection_id.into(),
                    refresh_token: format!("{connection_id}-secret"),
                });
            guard.stop_session();
        }
    }
    let cli_request = ipc::IpcRequest::TakeRefreshTokenUpdateFor {
        connection_id: ipc::WINDOWS_CLI_CONNECTION_ID.into(),
    };
    assert!(matches!(
        dispatch_as(cli_request.clone(), &state, &bob, || Some((
            bob.owner.sid.clone(),
            2
        )))
        .await,
        ipc::IpcResponse::RefreshTokenUpdate {
            connection_id: None,
            refresh_token: None
        }
    ));
    for (request, expected) in [
        (cli_request, ipc::WINDOWS_CLI_CONNECTION_ID),
        (ipc::IpcRequest::TakeRefreshTokenUpdate, "gui-profile"),
    ] {
        let response = dispatch_as(request, &state, &alice, || {
            Some((alice.owner.sid.clone(), 1))
        })
        .await;
        assert_eq!(
            response,
            ipc::IpcResponse::RefreshTokenUpdate {
                connection_id: Some(expected.into()),
                refresh_token: Some(format!("{expected}-secret")),
            }
        );
        let _ = dispatch_as(
            ipc::IpcRequest::AcknowledgeRefreshTokenUpdate {
                connection_id: expected.into(),
                refresh_token: format!("{expected}-secret"),
            },
            &state,
            &alice,
            || Some((alice.owner.sid.clone(), 1)),
        )
        .await;
    }
    assert!(state
        .lock()
        .await
        .pending_keycloak_refresh_token(&alice.owner.refresh_token_key())
        .is_none());
    // Conversely, GUI polling must skip a CLI update even when it is first.
    {
        let mut guard = state.lock().await;
        guard.mark_session_starting(
            &alice.owner.refresh_token_key(),
            crate::tests::test_config(),
            Some(ipc::WINDOWS_CLI_CONNECTION_ID),
        );
        guard
            .runtime_handles()
            .publish_keycloak_refresh_token(PendingKeycloakRefreshToken {
                connection_id: ipc::WINDOWS_CLI_CONNECTION_ID.into(),
                refresh_token: "another-cli-secret".into(),
            });
        guard.stop_session();
    }
    assert!(matches!(
        dispatch_as(
            ipc::IpcRequest::TakeRefreshTokenUpdate,
            &state,
            &alice,
            || Some((alice.owner.sid.clone(), 1))
        )
        .await,
        ipc::IpcResponse::RefreshTokenUpdate {
            connection_id: None,
            refresh_token: None
        }
    ));
    assert!(state
        .lock()
        .await
        .pending_keycloak_refresh_token_for(
            &alice.owner.refresh_token_key(),
            ipc::WINDOWS_CLI_CONNECTION_ID
        )
        .is_some());
}
