use super::*;
use shared::enrollment::{EnrollmentKeycloak, EnrollmentTransport, ENROLLMENT_SCHEMA_V1};

fn enrollment_profile() -> EnrollmentProfile {
    EnrollmentProfile {
        schema: ENROLLMENT_SCHEMA_V1.into(),
        device_id: "panel-device-id".into(),
        label: "  Enrolled laptop  ".into(),
        endpoint: "vpn.example.com:10443".into(),
        cert_pin: format!("{}, {}", "ab".repeat(32), "cd".repeat(32)),
        transport: EnrollmentTransport::Http3CensorshipResistant,
        ech_config: Some("00ff".into()),
        vpn_mtu: shared::DEFAULT_TUN_MTU,
        keycloak: EnrollmentKeycloak {
            url: "https://AUTH.example.com:443/auth/".into(),
            realm: "mavi-vpn".into(),
            client_id: "mavi-client".into(),
        },
    }
}

#[test]
fn mapping_normalizes_editor_fields_without_credentials() {
    let connection = saved_conn_from_enrollment(enrollment_profile(), "local-id".into()).unwrap();

    assert_eq!(connection.id, "local-id");
    assert_eq!(connection.label, "Enrolled laptop");
    assert_eq!(connection.endpoint, "vpn.example.com:10443");
    assert_eq!(
        connection.cert_pin,
        format!("{},{}", "ab".repeat(32), "cd".repeat(32))
    );
    assert_eq!(
        connection.kc_url.as_deref(),
        Some("https://auth.example.com/auth")
    );
    assert_eq!(connection.kc_realm.as_deref(), Some("mavi-vpn"));
    assert_eq!(connection.kc_client_id.as_deref(), Some("mavi-client"));
    assert_eq!(connection.vpn_mtu, Some(1280));
    assert!(connection.http3_framing);
    assert!(connection.censorship_resistant);
    assert!(!connection.http2_framing);
    assert!(connection.token.is_none());
}

#[test]
fn import_appends_and_selects_a_unique_profile_without_touching_old_credentials() {
    let dir = tempfile::tempdir().unwrap();
    let store = MemorySecretStore::default();
    let mut prefs = Prefs {
        connections: vec![SavedConn {
            id: "existing".into(),
            label: "Existing".into(),
            endpoint: "old.example.com:443".into(),
            token: Some("old-access".into()),
            cert_pin: "old-pin".into(),
            kc_auth: Some(true),
            kc_url: Some("https://old-auth.example.com".into()),
            kc_realm: Some("old-realm".into()),
            kc_client_id: Some("old-client".into()),
            ..SavedConn::default()
        }],
        active_id: Some("existing".into()),
        ..Prefs::default()
    };
    save_prefs_to_dir_with_store(dir.path(), &mut prefs, &store).unwrap();
    let old_refresh = prefs.connections[0].refresh_account().unwrap();
    store.set_secret(&old_refresh, "old-refresh").unwrap();

    let imported =
        import_enrollment_to_dir_with_store(dir.path(), enrollment_profile(), &store).unwrap();

    assert_eq!(imported.connections.len(), 2);
    let old = &imported.connections[0];
    let new = &imported.connections[1];
    assert_eq!(old.id, "existing");
    assert_eq!(old.token.as_deref(), Some("old-access"));
    assert_eq!(new.id.len(), 32);
    assert!(new.id.bytes().all(|byte| byte.is_ascii_hexdigit()));
    assert_ne!(new.id, "existing");
    assert_ne!(new.id, "panel-device-id");
    assert_eq!(imported.active_id.as_deref(), Some(new.id.as_str()));
    assert!(new.token.is_none());
    assert_eq!(
        store
            .secret(&connection_token_account("existing"))
            .as_deref(),
        Some("old-access")
    );
    assert_eq!(store.secret(&old_refresh).as_deref(), Some("old-refresh"));
    assert!(store.secret(&connection_token_account(&new.id)).is_none());
    assert!(store.secret(&new.refresh_account().unwrap()).is_none());
    assert!(store.deleted().is_empty());
}

#[test]
fn invalid_frontend_profile_cannot_replace_saved_connections() {
    let dir = tempfile::tempdir().unwrap();
    let store = MemorySecretStore::default();
    let mut profile = enrollment_profile();
    profile.keycloak.url = "https://user@auth.example.com".into();

    let error = import_enrollment_to_dir_with_store(dir.path(), profile, &store)
        .err()
        .expect("enrollment must reject unreadable preferences");

    assert!(!error.is_empty());
    assert!(!dir.path().join("prefs.json").exists());
}
