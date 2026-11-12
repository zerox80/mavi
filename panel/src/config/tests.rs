use super::*;
use std::collections::HashMap;

fn values() -> HashMap<&'static str, String> {
    HashMap::from([
        (
            "MAVI_PANEL_DATABASE_URL",
            "postgres://localhost/mavi".into(),
        ),
        ("MAVI_PANEL_KEYCLOAK_URL", "https://auth.example".into()),
        ("MAVI_PANEL_KEYCLOAK_REALM", "mavi-vpn".into()),
        ("MAVI_PANEL_CLIENT_ID", "mavi-panel".into()),
        ("MAVI_PANEL_CLIENT_SECRET", "test-secret".into()),
        ("MAVI_PANEL_PUBLIC_URL", "https://panel.example".into()),
        ("MAVI_PANEL_VPN_ENDPOINT", "vpn.example:10443".into()),
    ])
}

fn load(values: HashMap<&'static str, String>) -> Result<Config, ConfigError> {
    Config::from_lookup(|name| values.get(name).cloned())
}

#[test]
fn configuration_builds_fixed_keycloak_coordinates() {
    let config = load(values()).unwrap();
    assert_eq!(
        config.keycloak.issuer(),
        "https://auth.example/realms/mavi-vpn"
    );
    assert_eq!(config.redirect_uri(), "https://panel.example/auth/callback");
    assert_eq!(config.bind, "0.0.0.0:8081".parse().unwrap());
    assert!(!config.insecure_http);
}

#[test]
fn enrollment_links_encode_the_public_panel_url() {
    let config = load(values()).unwrap();
    let ticket = "a".repeat(43);
    assert_eq!(
        config.enrollment_url(&ticket),
        format!("https://panel.example/enroll/{ticket}")
    );
    let deep_link = Url::parse(&config.deep_link(&ticket)).unwrap();
    let parameters: HashMap<_, _> = deep_link.query_pairs().into_owned().collect();
    assert_eq!(parameters.get("ticket"), Some(&ticket));
    assert_eq!(
        parameters.get("panel"),
        Some(&"https://panel.example".to_string())
    );
}

#[test]
fn insecure_http_is_required_for_http_urls() {
    let mut http_values = values();
    http_values.insert("MAVI_PANEL_PUBLIC_URL", "http://localhost:8081".into());
    assert!(load(http_values.clone()).is_err());
    http_values.insert("MAVI_PANEL_INSECURE_HTTP", "true".into());
    let config = load(http_values).unwrap();
    assert!(config.insecure_http);
}

#[test]
fn public_urls_reject_paths_and_request_dependent_authorities() {
    let mut path_values = values();
    path_values.insert(
        "MAVI_PANEL_PUBLIC_URL",
        "https://panel.example/panel".into(),
    );
    assert!(load(path_values).is_err());

    let mut credentials = values();
    credentials.insert("MAVI_PANEL_PUBLIC_URL", "https://user@panel.example".into());
    assert!(load(credentials).is_err());
}

#[test]
fn transport_values_map_to_the_enrollment_schema() {
    let cases = [
        (
            "http3_censorship_resistant",
            EnrollmentTransport::Http3CensorshipResistant,
        ),
        ("http3", EnrollmentTransport::Http3),
        ("quic", EnrollmentTransport::Quic),
        ("http2", EnrollmentTransport::Http2),
    ];
    for (configured, expected) in cases {
        let mut transport_values = values();
        transport_values.insert("MAVI_PANEL_TRANSPORT", configured.into());
        let config = load(transport_values).unwrap();
        assert_eq!(config.transport.enrollment(), expected);
        assert_eq!(config.transport.as_str(), configured);
    }
}

#[test]
fn generated_profile_uses_the_shared_schema_and_mtu() {
    let config = load(values()).unwrap();
    let profile = config
        .profile_from_files(
            "0b858e3f-a9c5-4ae5-a859-b556e6ba70c0".into(),
            "Work laptop".into(),
            "cd".repeat(32),
            None,
        )
        .unwrap();
    profile.validate().unwrap();
    assert_eq!(profile.schema, ENROLLMENT_SCHEMA_V1);
    assert_eq!(profile.vpn_mtu, 1280);
    assert_eq!(config.keycloak.client_id, "mavi-panel");
    assert_eq!(profile.keycloak.client_id, "mavi-client");
    assert!(profile.ech_config.is_none());
}

#[test]
fn enrollment_uses_the_configured_vpn_client_id() {
    let mut configured = values();
    configured.insert("MAVI_PANEL_VPN_CLIENT_ID", "custom-vpn-client".into());
    let config = load(configured).unwrap();
    let profile = config
        .profile_from_files(
            "device-1".into(),
            "Work laptop".into(),
            "ab".repeat(32),
            None,
        )
        .unwrap();
    assert_eq!(profile.keycloak.client_id, "custom-vpn-client");
    assert_eq!(config.keycloak.client_id, "mavi-panel");
}

#[tokio::test]
async fn http2_enrollment_ignores_the_ech_file() {
    let mut env = values();
    env.insert("MAVI_PANEL_TRANSPORT", "http2".into());
    let mut config = load(env).unwrap();
    let files = tempfile::tempdir().unwrap();
    config.cert_pin_file = files.path().join("cert_pin.txt");
    config.ech_config_file = files.path().join("ech_config_hex.txt");
    std::fs::write(&config.cert_pin_file, "cd".repeat(32)).unwrap();
    std::fs::write(&config.ech_config_file, "00ff").unwrap();
    let device_id = "0b858e3f-a9c5-4ae5-a859-b556e6ba70c0";

    let profile = config
        .enrollment_profile(device_id.into(), "Work laptop".into())
        .await
        .unwrap();
    assert_eq!(profile.transport, EnrollmentTransport::Http2);
    assert_eq!(profile.ech_config, None);

    config.transport = PanelTransport::Http3;
    let profile = config
        .enrollment_profile(device_id.into(), "Work laptop".into())
        .await
        .unwrap();
    assert_eq!(profile.ech_config.as_deref(), Some("00ff"));
}

#[test]
fn invalid_server_values_fail_during_configuration() {
    let mut endpoint_values = values();
    endpoint_values.insert("MAVI_PANEL_VPN_ENDPOINT", "https://vpn.example".into());
    assert!(load(endpoint_values).is_err());

    let mut transport_values = values();
    transport_values.insert("MAVI_PANEL_TRANSPORT", "websocket".into());
    assert!(load(transport_values).is_err());

    let mut client_values = values();
    client_values.insert("MAVI_PANEL_VPN_CLIENT_ID", "mavi-panel".into());
    assert!(load(client_values).is_err());
}
