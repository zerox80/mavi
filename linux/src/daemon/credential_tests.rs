use super::*;
use shared::ipc::KeycloakRuntimeAuth;
use shared::kc_oauth::OAuthTokens;

#[tokio::test]
async fn cli_rotation_survives_stop_and_requires_acknowledging_the_latest_token() {
    let state = Arc::new(Mutex::new(DaemonState::new()));
    let id = "linux-cli:/tmp/config.json:refresh-token";
    let request = IpcRequest::StartWithKeycloak {
        config: super::tests::test_config(),
        keycloak: KeycloakRuntimeAuth {
            connection_id: id.into(),
            kc_url: "https://auth.example.com".into(),
            realm: "realm".into(),
            client_id: "client".into(),
            refresh_token: "initial".into(),
        },
    };
    assert_eq!(
        dispatch_request_with_hooks(request, &state, false, || Ok(())).await,
        IpcResponse::Ok
    );
    let publish = {
        let guard = state.lock().await;
        let config = guard.active_config.as_ref().unwrap();
        assert_eq!(config.refresh_token.as_deref(), Some("initial"));
        assert_eq!(config.kc_realm.as_deref(), Some("realm"));
        guard.refresh_updates.publisher(id.into())
    };
    let publish_rotation = |refresh: &str| {
        publish(&OAuthTokens {
            access_token: "access".into(),
            refresh_token: Some(refresh.into()),
        })
        .unwrap()
    };
    publish_rotation("rotated");
    assert_eq!(
        dispatch_request_with_hooks(IpcRequest::Stop, &state, false, || Ok(())).await,
        IpcResponse::Ok
    );
    let fetch = || IpcRequest::TakeRefreshTokenUpdateFor {
        connection_id: id.into(),
    };
    let expected = |refresh: &str| IpcResponse::RefreshTokenUpdate {
        connection_id: Some(id.into()),
        refresh_token: Some(refresh.into()),
    };
    for _ in 0..2 {
        assert_eq!(
            dispatch_request_with_hooks(fetch(), &state, false, || Ok(())).await,
            expected("rotated")
        );
    }
    publish_rotation("newer");
    dispatch_request_with_hooks(
        IpcRequest::AcknowledgeRefreshTokenUpdate {
            connection_id: id.into(),
            refresh_token: "rotated".into(),
        },
        &state,
        false,
        || Ok(()),
    )
    .await;
    assert_eq!(
        dispatch_request_with_hooks(fetch(), &state, false, || Ok(())).await,
        expected("newer")
    );
    dispatch_request_with_hooks(
        IpcRequest::AcknowledgeRefreshTokenUpdate {
            connection_id: id.into(),
            refresh_token: "newer".into(),
        },
        &state,
        false,
        || Ok(()),
    )
    .await;
    assert_eq!(
        dispatch_request_with_hooks(fetch(), &state, false, || Ok(())).await,
        IpcResponse::RefreshTokenUpdate {
            connection_id: None,
            refresh_token: None
        }
    );
}
