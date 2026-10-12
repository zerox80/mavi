use super::*;
use clap::Parser;

#[tokio::test]
async fn ipv4_only_authentication_ignores_ipv6_capacity_and_preserves_quota() {
    // Cover explicit disablement and a failed runtime IPv6 TUN setup.
    for explicitly_disabled in [true, false] {
        let state = Arc::new(AppState::new_with_ipv6("10.8.0.0/24", "fd00::/126").unwrap());
        let mut config = Config::parse_from(["vpn", "--auth-token", "secret"]);
        config.disable_ipv6 = explicitly_disabled;
        let mut leases = Vec::new();
        for host in 1..=8 {
            let (ip4, ip6, _) = authenticate_client(
                "secret",
                Ipv4Addr::new(192, 0, 2, host).into(),
                &state,
                &config,
                None,
                explicitly_disabled,
            )
            .await
            .unwrap();
            assert!(ip6.is_none());
            let (tx, _rx) = tokio::sync::mpsc::channel(1);
            state.register_client(ip4, ip6, tx);
            leases.push((ip4, ip6));
        }
        assert_eq!(state.peers.len(), 8);
        assert!(state.peers_v6.is_empty());
        assert!(authenticate_client(
            "secret",
            "192.0.2.9".parse().unwrap(),
            &state,
            &config,
            None,
            explicitly_disabled,
        )
        .await
        .unwrap_err()
        .to_string()
        .contains("session limit"));
        let (ip4, ip6) = leases.remove(0);
        state.release_ips(ip4, ip6);
        state.release_ips(ip4, ip6);
        let replacement = authenticate_client(
            "secret",
            "192.0.2.10".parse().unwrap(),
            &state,
            &config,
            None,
            explicitly_disabled,
        )
        .await
        .unwrap();
        assert_eq!(replacement.0, ip4);
        assert!(replacement.1.is_none());
        assert_eq!(
            state.assign_ipv6().unwrap(),
            "fd00::2".parse::<Ipv6Addr>().unwrap()
        );
    }
}

#[tokio::test]
async fn dual_stack_exhaustion_still_rejects_and_reclaims_ipv4() {
    let state = Arc::new(AppState::new_with_ipv6("10.8.0.0/24", "fd00::/126").unwrap());
    let config = Config::parse_from(["vpn", "--auth-token", "secret"]);
    for host in 1..=2 {
        assert!(authenticate_client(
            "secret",
            Ipv4Addr::new(192, 0, 2, host).into(),
            &state,
            &config,
            None,
            true,
        )
        .await
        .unwrap()
        .1
        .is_some());
    }
    assert!(authenticate_client(
        "secret",
        "192.0.2.3".parse().unwrap(),
        &state,
        &config,
        None,
        true,
    )
    .await
    .unwrap_err()
    .to_string()
    .contains("IPv6 pool exhausted"));
    assert_eq!(state.assign_ip().unwrap(), Ipv4Addr::new(10, 8, 0, 4));
}
