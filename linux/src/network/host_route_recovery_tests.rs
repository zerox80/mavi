use super::command::{test_support::RecordingRunner, CommandRunner, ProductionCommandRunner};
use super::routes;

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
    let mut runner = RecordingRunner {
        outcomes: [Err(anyhow::anyhow!("route flush denied"))].into(),
        ..RecordingRunner::default()
    };
    assert!(routes::remove_stale_host_routes(&mut runner).is_err());
    assert_eq!(
        runner.calls[0].1,
        ["-4", "route", "flush", "table", "main", "proto", "243"]
    );
}
