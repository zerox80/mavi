use crate::daemon::{dispatch_request_with_hooks, DaemonState};
use shared::ipc::{Config, IpcRequest, IpcResponse};
use std::sync::{atomic::Ordering, Arc};
use tokio::sync::Mutex;

fn config() -> Config {
    Config {
        endpoint: "vpn.example.test:443".into(),
        token: "seed".into(),
        cert_pin: "00".repeat(32),
        censorship_resistant: false,
        http3_framing: false,
        http2_framing: false,
        kc_auth: Some(true),
        kc_url: Some("https://auth.example.test".into()),
        kc_realm: None,
        kc_client_id: None,
        refresh_token: None,
        ech_config: None,
        vpn_mtu: None,
    }
}

async fn dispatch(state: &Arc<Mutex<DaemonState>>, request: IpcRequest) -> IpcResponse {
    // Exercise the actual dispatcher, without spawning a VPN or changing routes.
    dispatch_request_with_hooks(request, state, false, || Ok(())).await
}

async fn start(state: &Arc<Mutex<DaemonState>>, id: &str) {
    assert_eq!(
        dispatch(
            state,
            IpcRequest::StartScoped {
                session_id: id.into(),
                config: config(),
            }
        )
        .await,
        IpcResponse::Ok
    );
}

#[tokio::test]
async fn old_refresh_cannot_update_or_stop_a_replacement_even_at_the_same_endpoint() {
    for scoped_replacement in [false, true] {
        let state = Arc::new(Mutex::new(DaemonState::new()));
        start(&state, "old").await;
        assert_eq!(dispatch(&state, IpcRequest::Stop).await, IpcResponse::Ok);
        if scoped_replacement {
            start(&state, "new").await;
        } else {
            let mut psk = config();
            psk.kc_auth = Some(false);
            assert_eq!(
                dispatch(&state, IpcRequest::Start(psk)).await,
                IpcResponse::Ok
            );
        }
        // Models a refresh already in flight when another client replaces it.
        for request in [
            IpcRequest::UpdateSessionToken {
                session_id: "old".into(),
                token: "stale-session-token".into(),
            },
            IpcRequest::StopSession {
                session_id: "old".into(),
            },
            IpcRequest::SessionStatus {
                session_id: "old".into(),
            },
        ] {
            assert!(matches!(
                dispatch(&state, request).await,
                IpcResponse::Error(_)
            ));
        }
        let guard = state.lock().await;
        assert!(guard.vpn_running.load(Ordering::SeqCst));
        assert_eq!(*guard.current_token.lock().unwrap(), "seed");
    }
}

#[tokio::test]
async fn current_session_can_refresh_and_stop_but_terminal_sessions_cannot() {
    let state = Arc::new(Mutex::new(DaemonState::new()));
    start(&state, "current").await;
    assert!(matches!(
        dispatch(
            &state,
            IpcRequest::SessionStatus {
                session_id: "current".into(),
            }
        )
        .await,
        IpcResponse::Status { .. }
    ));
    assert_eq!(
        dispatch(
            &state,
            IpcRequest::UpdateSessionToken {
                session_id: "current".into(),
                token: "fresh".into(),
            }
        )
        .await,
        IpcResponse::Ok
    );
    assert_eq!(*state.lock().await.current_token.lock().unwrap(), "fresh");
    assert_eq!(
        dispatch(
            &state,
            IpcRequest::StopSession {
                session_id: "current".into(),
            }
        )
        .await,
        IpcResponse::Ok
    );
    assert!(matches!(
        dispatch(
            &state,
            IpcRequest::SessionStatus {
                session_id: "current".into(),
            }
        )
        .await,
        IpcResponse::Error(_)
    ));
    start(&state, "failed").await;
    state
        .lock()
        .await
        .vpn_running
        .store(false, Ordering::SeqCst);
    assert!(matches!(
        dispatch(
            &state,
            IpcRequest::UpdateSessionToken {
                session_id: "failed".into(),
                token: "late".into(),
            }
        )
        .await,
        IpcResponse::Error(_)
    ));
}

#[tokio::test]
async fn rejected_start_does_not_take_over_the_running_session() {
    let state = Arc::new(Mutex::new(DaemonState::new()));
    start(&state, "current").await;
    assert!(matches!(
        dispatch(
            &state,
            IpcRequest::StartScoped {
                session_id: "rejected".into(),
                config: config(),
            }
        )
        .await,
        IpcResponse::Error(_)
    ));
    assert!(matches!(
        dispatch(
            &state,
            IpcRequest::SessionStatus {
                session_id: "current".into(),
            }
        )
        .await,
        IpcResponse::Status { .. }
    ));
}
