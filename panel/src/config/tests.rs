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
    assert_eq!(profile.keycloak.client_id, "mavi-panel");
    assert!(profile.ech_config.is_none());
}

#[test]
fn invalid_server_values_fail_during_configuration() {
    let mut endpoint_values = values();
    endpoint_values.insert("MAVI_PANEL_VPN_ENDPOINT", "https://vpn.example".into());
    assert!(load(endpoint_values).is_err());

    let mut transport_values = values();
    transport_values.insert("MAVI_PANEL_TRANSPORT", "websocket".into());
    assert!(load(transport_values).is_err());
}
