use super::command::{test_support::RecordingRunner, CommandRunner, ProductionCommandRunner};
use super::routes;

#[test]
fn recovery_without_an_ipv4_table_still_removes_ipv6_bypasses() {
    if !super::kernel_tests::in_namespace(
        "network::host_route_recovery_tests::recovery_without_an_ipv4_table_still_removes_ipv6_bypasses",
    ) {
        return;
    }
    let mut runner = ProductionCommandRunner;
    // Keep loopback down and never add an IPv4 address or route: the kernel
    // need not create the IPv4 main table for this IPv6-only uplink.
    runner
        .run("ip", &["link", "add", "physical0", "type", "dummy"])
        .unwrap();
    runner
        .run("ip", &["link", "set", "physical0", "up"])
        .unwrap();
    runner
        .run(
            "ip",
            &[
                "-6",
                "addr",
                "add",
                "2001:db8::2/64",
                "dev",
                "physical0",
                "nodad",
            ],
        )
        .unwrap();
    runner
        .run(
            "ip",
            &[
                "-6",
                "route",
                "add",
                "default",
                "via",
                "2001:db8::1",
                "dev",
                "physical0",
            ],
        )
        .unwrap();
    let endpoint = "2001:db8:1::10".parse().unwrap();
    let original = routes::resolve_host_route(&mut runner, endpoint).unwrap();
    routes::add_host_route_exception(&mut runner, original).unwrap();

    routes::remove_stale_host_routes(&mut runner).unwrap();
    routes::remove_stale_host_routes(&mut runner).unwrap();
    assert_eq!(
        runner
            .output("ip", &["-j", "-6", "route", "show", "proto", "243"])
            .unwrap()
            .trim(),
        "[]"
    );
    let recovered = routes::resolve_host_route(&mut runner, endpoint).unwrap();
    assert_eq!(recovered.device, "physical0");
    assert_eq!(recovered.gateway.as_deref(), Some("2001:db8::1"));
}

#[test]
fn crash_recovery_removes_old_bypasses_and_preserves_operator_routes() {
    if !super::kernel_tests::in_namespace(
        "network::host_route_recovery_tests::crash_recovery_removes_old_bypasses_and_preserves_operator_routes",
    ) {
        return;
    }
    let mut runner = ProductionCommandRunner;
    runner
        .run("ip", &["link", "add", "physical0", "type", "dummy"])
        .unwrap();
    runner
        .run("ip", &["link", "set", "physical0", "up"])
        .unwrap();
    for (family, old_bypass, operator_route) in [
        ("-4", "203.0.113.10", "203.0.113.11/32"),
        ("-6", "2001:db8::10", "2001:db8::11/128"),
    ] {
        runner
            .run(
                "ip",
                &[
                    family,
                    "route",
                    "add",
                    operator_route,
                    "dev",
                    "physical0",
                    "proto",
                    "99",
                ],
            )
            .unwrap();
        routes::add_host_route_exception(
            &mut runner,
            routes::HostRoute {
                ip: old_bypass.parse().unwrap(),
                gateway: None,
                device: "physical0".into(),
                onlink: false,
            },
        )
        .unwrap(); // Drop the RAM ownership record, as after a crash.
    }
    routes::remove_stale_host_routes(&mut runner).unwrap();
    for (family, old_bypass, operator_route) in [
        ("-4", "203.0.113.10", "203.0.113.11"),
        ("-6", "2001:db8::10", "2001:db8::11"),
    ] {
        let remaining = runner
            .output("ip", &["-j", family, "route", "show", "dev", "physical0"])
            .unwrap();
        let remaining: Vec<serde_json::Value> = serde_json::from_str(&remaining).unwrap();
        // Bringing a device up can also install a kernel IPv6 link-local route.
        let has_destination = |destination| {
            remaining.iter().any(|route| {
                route["dst"]
                    .as_str()
                    .is_some_and(|dst| dst.starts_with(destination))
            })
        };
        assert!(!has_destination(old_bypass));
        assert!(has_destination(operator_route));
    }
}

#[test]
fn recovery_failure_is_propagated_instead_of_silently_retaining_a_bypass() {
    for (family, outputs) in [
        ("-4", vec![Ok("203.0.113.10 dev eth0 proto 243".into())]),
        (
            "-6",
            vec![
                Ok(String::new()),
                Ok("2001:db8::10 dev eth0 proto 243".into()),
            ],
        ),
    ] {
        let mut runner = RecordingRunner {
            outputs: outputs.into(),
            outcomes: [Err(anyhow::anyhow!("route flush denied"))].into(),
            ..RecordingRunner::default()
        };
        let error = routes::remove_stale_host_routes(&mut runner).unwrap_err();
        assert!(format!("{error:#}").contains("route flush denied"));
        assert_eq!(
            runner.calls.last().unwrap().1,
            [family, "route", "flush", "table", "main", "proto", "243"]
        );
    }
}

#[test]
fn recovery_skips_empty_tables_for_both_address_families() {
    let mut runner = RecordingRunner {
        outputs: [Ok(String::new()), Ok("\n".into())].into(),
        ..RecordingRunner::default()
    };
    routes::remove_stale_host_routes(&mut runner).unwrap();
    assert_eq!(
        runner.calls,
        ["-4", "-6"].map(|family| (
            "ip".to_string(),
            [family, "route", "show", "table", "main", "proto", "243"]
                .map(String::from)
                .to_vec()
        ))
    );
}

#[test]
fn recovery_propagates_route_listing_failures() {
    for family in ["-4", "-6"] {
        let mut outputs = std::collections::VecDeque::new();
        if family == "-6" {
            outputs.push_back(Ok(String::new()));
        }
        outputs.push_back(Err(anyhow::anyhow!("route listing denied")));
        let mut runner = RecordingRunner {
            outputs,
            ..RecordingRunner::default()
        };
        let error = routes::remove_stale_host_routes(&mut runner).unwrap_err();
        assert!(format!("{error:#}").contains("route listing denied"));
        assert_eq!(
            runner.calls.last().unwrap().1,
            [family, "route", "show", "table", "main", "proto", "243"]
        );
    }
}
