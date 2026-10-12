use super::handle_action_response;
use shared::ipc::{IpcResponse, VpnState};

#[test]
fn advertised_config_options_dispatch_direct_connections_with_the_selected_file() {
    for command in [None, Some("connect"), Some("up")] {
        for option in ["-c", "--config"] {
            let mut args = Vec::new();
            if let Some(command) = command {
                args.push(command.to_owned());
            }
            args.extend([option.to_owned(), "/tmp/saved-vpn.json".to_owned()]);
            assert!(matches!(super::command_name(&args), "connect" | "up"));
            assert_eq!(
                super::config_path(&args).unwrap(),
                Some(std::path::PathBuf::from("/tmp/saved-vpn.json"))
            );
        }
    }
    for command in ["start", "stop", "daemon", "status", "repair", "--help"] {
        assert_eq!(super::command_name(&[command.into()]), command);
    }
}

#[test]
fn missing_config_option_values_fail_instead_of_prompting_for_another_connection() {
    for args in [
        vec!["-c".into()],
        vec!["connect".into(), "--config".into(), "--help".into()],
    ] {
        assert!(super::config_path(&args).is_err());
    }
}

#[test]
fn unknown_repair_options_fail_before_any_network_changes() {
    for args in [
        vec!["--legacy".into()],
        vec!["--legacy-ipv6".into(), "extra".into()],
    ] {
        let error = super::run_repair(&args).unwrap_err();
        assert!(error
            .to_string()
            .contains("Usage: mavi-vpn repair [--legacy-ipv6]"));
    }
}

#[test]
fn action_acknowledgement_succeeds() {
    assert!(handle_action_response(Ok(IpcResponse::Ok), "Accepted").is_ok());
}

#[test]
fn daemon_rejections_fail_with_the_original_message() {
    for message in [
        "Unauthorized",
        "VPN already running",
        "Network repair failed",
    ] {
        let error =
            handle_action_response(Ok(IpcResponse::Error(message.into())), "Accepted").unwrap_err();
        assert!(error.to_string().contains(message));
    }
}

#[test]
fn ipc_failures_preserve_the_cause_and_daemon_hint() {
    for message in ["permission denied", "connection refused", "Decode error"] {
        let error = handle_action_response(Err(anyhow::anyhow!(message)), "Accepted").unwrap_err();
        let diagnostic = format!("{error:#}");
        assert!(diagnostic.contains(message));
        assert!(diagnostic.contains("Is the daemon running?"));
    }
}

#[test]
fn unrelated_responses_do_not_acknowledge_an_action() {
    for response in [
        IpcResponse::Status {
            running: false,
            endpoint: None,
            state: VpnState::Stopped,
            last_error: None,
            assigned_ip: None,
        },
        IpcResponse::RefreshTokenUpdate {
            connection_id: None,
            refresh_token: None,
        },
    ] {
        let error = handle_action_response(Ok(response), "Accepted").unwrap_err();
        assert!(error.to_string().contains("Unexpected response"));
    }
}
