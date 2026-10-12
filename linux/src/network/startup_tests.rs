use super::*;

#[test]
fn direct_start_recovers_before_dns_dependent_startup() {
    let mut recovered = false;
    prepare_direct_connection_with(false, || {
        recovered = true;
        Ok(())
    })
    .unwrap();
    assert!(recovered);
}

#[test]
fn direct_start_preserves_an_existing_tunnel_and_its_dns() {
    let error = prepare_direct_connection_with(true, || {
        panic!("Must not clean up another active session's networking")
    })
    .unwrap_err();
    assert!(error.to_string().contains("mavi0 already exists"));
}

#[test]
fn recovery_errors_stop_direct_startup() {
    let error = prepare_direct_connection_with(false, || anyhow::bail!("resolver recovery failed"))
        .unwrap_err();
    assert!(error.to_string().contains("resolver recovery failed"));
}
