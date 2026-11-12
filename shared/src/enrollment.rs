use crate::ipc::Config;
use serde::{Deserialize, Serialize};

pub const ENROLLMENT_SCHEMA_V1: &str = "mavi.connection.v1";

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EnrollmentTransport {
    Quic,
    Http3,
    Http3CensorshipResistant,
    Http2,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EnrollmentKeycloak {
    pub url: String,
    pub realm: String,
    pub client_id: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EnrollmentProfile {
    pub schema: String,
    pub device_id: String,
    pub label: String,
    pub endpoint: String,
    pub cert_pin: String,
    pub transport: EnrollmentTransport,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub ech_config: Option<String>,
    pub vpn_mtu: u16,
    pub keycloak: EnrollmentKeycloak,
}

impl EnrollmentProfile {
    pub fn validate(&self) -> Result<(), String> {
        if self.schema != ENROLLMENT_SCHEMA_V1 {
            return Err("Unsupported enrollment schema".into());
        }
        validate_identifier("device_id", &self.device_id, 64)?;
        if self.label.trim().is_empty() || self.label.chars().count() > 80 {
            return Err("Device label must contain 1 to 80 characters".into());
        }
        if self.label.chars().any(char::is_control) {
            return Err("Device label contains control characters".into());
        }
        validate_endpoint(&self.endpoint)?;
        let pins = crate::hex::decode_hex_pins(&self.cert_pin)
            .ok_or_else(|| "Invalid certificate PIN".to_string())?;
        if pins.len() > 8 {
            return Err("At most eight certificate PINs are allowed".into());
        }
        if self.vpn_mtu != crate::DEFAULT_TUN_MTU {
            return Err(format!(
                "Enrollment requires VPN_MTU={}",
                crate::DEFAULT_TUN_MTU
            ));
        }
        crate::validate_keycloak_url(self.keycloak.url.trim_end_matches('/'))?;
        validate_keycloak_base_url(&self.keycloak.url)?;
        validate_identifier("realm", &self.keycloak.realm, 64)?;
        validate_identifier("client_id", &self.keycloak.client_id, 64)?;
        if let Some(ech) = &self.ech_config {
            if self.transport == EnrollmentTransport::Http2 {
                return Err("ECH is not supported with HTTP/2 enrollment".into());
            }
            let bytes = crate::hex::decode_hex(ech)
                .ok_or_else(|| "Invalid ECH configuration".to_string())?;
            if bytes.is_empty() || bytes.len() > 8192 {
                return Err("ECH configuration must contain 1 to 8192 bytes".into());
            }
        }
        Ok(())
    }

    pub fn into_config(self) -> Result<Config, String> {
        self.validate()?;
        let (http3_framing, http2_framing, censorship_resistant) = match self.transport {
            EnrollmentTransport::Quic => (false, false, false),
            EnrollmentTransport::Http3 => (true, false, false),
            EnrollmentTransport::Http3CensorshipResistant => (true, false, true),
            EnrollmentTransport::Http2 => (false, true, false),
        };
        Ok(Config {
            endpoint: self.endpoint,
            token: String::new(),
            cert_pin: self.cert_pin,
            censorship_resistant,
            http3_framing,
            kc_auth: Some(true),
            kc_url: Some(self.keycloak.url.trim_end_matches('/').to_string()),
            kc_realm: Some(self.keycloak.realm),
            kc_client_id: Some(self.keycloak.client_id),
            refresh_token: None,
            ech_config: self.ech_config,
            vpn_mtu: Some(self.vpn_mtu),
            http2_framing,
        })
    }
}

fn validate_identifier(name: &str, value: &str, max_len: usize) -> Result<(), String> {
    if value.is_empty() || value.len() > max_len {
        return Err(format!("Invalid {name} length"));
    }
    if !value
        .bytes()
        .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.'))
    {
        return Err(format!("Invalid {name}"));
    }
    Ok(())
}

fn validate_endpoint(endpoint: &str) -> Result<(), String> {
    if endpoint.is_empty()
        || endpoint.len() > 255
        || endpoint
            .bytes()
            .any(|byte| byte.is_ascii_whitespace() || byte <= 0x20)
        || endpoint.contains(['/', '@', '?', '#'])
    {
        return Err("Invalid VPN endpoint".into());
    }
    let (host, port) = crate::endpoint::split_endpoint(endpoint);
    if host.is_empty() || host.len() > 253 {
        return Err("VPN endpoint host is missing".into());
    }
    if endpoint.starts_with('[') {
        if host.parse::<std::net::Ipv6Addr>().is_err() {
            return Err("Bracketed VPN endpoint must contain an IPv6 address".into());
        }
    } else if endpoint_host_is_valid_ip(host) {
        // IP literals need no DNS-specific validation.
    } else if host.contains(':')
        || !host
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'.'))
    {
        return Err("Invalid VPN endpoint host".into());
    }
    let port = port.ok_or_else(|| "VPN endpoint port is missing".to_string())?;
    match port.parse::<u16>() {
        Ok(1..=65535) => Ok(()),
        _ => Err("Invalid VPN endpoint port".into()),
    }
}

fn endpoint_host_is_valid_ip(host: &str) -> bool {
    host.parse::<std::net::IpAddr>().is_ok()
}

fn validate_keycloak_base_url(value: &str) -> Result<(), String> {
    let url = url::Url::parse(value.trim_end_matches('/'))
        .map_err(|_| "Invalid Keycloak URL".to_string())?;
    if url.host_str().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return Err("Keycloak URL has an invalid authority, query, or fragment".into());
    }
    Ok(())
}

#[cfg(feature = "oauth-client")]
pub mod client {
    use super::EnrollmentProfile;
    use serde::{Deserialize, Serialize};
    use std::time::Duration;

    const MAX_RESPONSE_BYTES: usize = 64 * 1024;

    #[derive(Clone)]
    pub struct EnrollmentRequest {
        redeem_url: url::Url,
        ticket: String,
    }

    impl std::fmt::Debug for EnrollmentRequest {
        fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
            formatter
                .debug_struct("EnrollmentRequest")
                .field("redeem_url", &self.redeem_url)
                .field("ticket", &"<redacted>")
                .finish()
        }
    }

    #[derive(Serialize)]
    struct RedeemRequest<'a> {
        ticket: &'a str,
    }

    #[derive(Deserialize)]
    #[serde(deny_unknown_fields)]
    struct RedeemResponse {
        profile: EnrollmentProfile,
    }

    impl EnrollmentRequest {
        pub fn redemption_url(&self) -> &url::Url {
            &self.redeem_url
        }

        pub fn parse(link: &str) -> Result<Self, String> {
            let url = url::Url::parse(link.trim()).map_err(|_| "Invalid enrollment link")?;
            match url.scheme() {
                "https" | "http" => parse_https_link(url),
                "mavivpn" => parse_deep_link(url),
                _ => Err("Enrollment link must use HTTPS".into()),
            }
        }

        pub async fn redeem(self) -> Result<EnrollmentProfile, String> {
            let response = reqwest::Client::new()
                .post(self.redeem_url.clone())
                .json(&RedeemRequest {
                    ticket: &self.ticket,
                })
                .timeout(Duration::from_secs(20))
                .send()
                .await
                .map_err(|_| "Could not reach the enrollment server".to_string())?;
            if !response.status().is_success() {
                return Err(match response.status().as_u16() {
                    404 | 410 => "Enrollment link is invalid, expired, or already used".into(),
                    429 => "Too many enrollment attempts; try again later".into(),
                    _ => "Enrollment server rejected the request".into(),
                });
            }
            if response
                .content_length()
                .is_some_and(|length| length > MAX_RESPONSE_BYTES as u64)
            {
                return Err("Enrollment response is too large".into());
            }
            let mut body = Vec::new();
            let mut response = response;
            while let Some(chunk) = response
                .chunk()
                .await
                .map_err(|_| "Could not read the enrollment response".to_string())?
            {
                if body.len() + chunk.len() > MAX_RESPONSE_BYTES {
                    return Err("Enrollment response is too large".into());
                }
                body.extend_from_slice(&chunk);
            }
            let response: RedeemResponse = serde_json::from_slice(&body)
                .map_err(|_| "Enrollment server returned an invalid profile".to_string())?;
            response.profile.validate()?;
            Ok(response.profile)
        }
    }

    fn parse_https_link(url: url::Url) -> Result<EnrollmentRequest, String> {
        validate_web_url(&url)?;
        if url.query().is_some() || url.fragment().is_some() {
            return Err("Enrollment link must not contain a query or fragment".into());
        }
        let segments: Vec<_> = url
            .path_segments()
            .ok_or("Invalid enrollment link")?
            .collect();
        if segments.len() < 2 || segments[segments.len() - 2] != "enroll" {
            return Err("Enrollment link has an invalid path".into());
        }
        let ticket = segments[segments.len() - 1].to_string();
        validate_ticket(&ticket)?;
        let prefix = &segments[..segments.len() - 2];
        let base_path = if prefix.is_empty() {
            String::new()
        } else {
            format!("/{}", prefix.join("/"))
        };
        let mut redeem_url = url;
        redeem_url.set_path(&format!("{base_path}/api/v1/enrollment/redeem"));
        redeem_url.set_query(None);
        redeem_url.set_fragment(None);
        Ok(EnrollmentRequest { redeem_url, ticket })
    }

    fn parse_deep_link(url: url::Url) -> Result<EnrollmentRequest, String> {
        if url.host_str() != Some("enroll") {
            return Err("Invalid Mavi enrollment deep link".into());
        }
        let mut ticket = None;
        let mut panel = None;
        for (name, value) in url.query_pairs() {
            match name.as_ref() {
                "ticket" if ticket.is_none() => ticket = Some(value.into_owned()),
                "panel" if panel.is_none() => panel = Some(value.into_owned()),
                _ => return Err("Enrollment deep link contains invalid parameters".into()),
            }
        }
        let ticket = ticket.ok_or("Enrollment ticket is missing")?;
        validate_ticket(&ticket)?;
        let panel: url::Url = panel
            .ok_or("Enrollment panel URL is missing")?
            .parse()
            .map_err(|_| "Invalid enrollment panel URL")?;
        validate_web_url(&panel)?;
        if panel.query().is_some() || panel.fragment().is_some() {
            return Err("Enrollment panel URL must not contain a query or fragment".into());
        }
        let mut redeem_url = panel;
        redeem_url.set_path(&format!(
            "{}/api/v1/enrollment/redeem",
            redeem_url.path().trim_end_matches('/')
        ));
        Ok(EnrollmentRequest { redeem_url, ticket })
    }

    fn validate_web_url(url: &url::Url) -> Result<(), String> {
        if url.host_str().is_none() || !url.username().is_empty() || url.password().is_some() {
            return Err("Enrollment URL has an invalid authority".into());
        }
        if url.scheme() == "https" {
            return Ok(());
        }
        let loopback = url
            .host_str()
            .is_some_and(|host| matches!(host, "localhost" | "127.0.0.1" | "::1"));
        if url.scheme() == "http" && loopback {
            return Ok(());
        }
        Err("Enrollment link must use HTTPS".into())
    }

    fn validate_ticket(ticket: &str) -> Result<(), String> {
        if !(32..=128).contains(&ticket.len())
            || !ticket
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
        {
            return Err("Invalid enrollment ticket".into());
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn profile() -> EnrollmentProfile {
        EnrollmentProfile {
            schema: ENROLLMENT_SCHEMA_V1.into(),
            device_id: "0b858e3f-a9c5-4ae5-a859-b556e6ba70c0".into(),
            label: "Alice laptop".into(),
            endpoint: "vpn.example.com:10443".into(),
            cert_pin: "ab".repeat(32),
            transport: EnrollmentTransport::Http3CensorshipResistant,
            ech_config: Some("00ff".into()),
            vpn_mtu: crate::DEFAULT_TUN_MTU,
            keycloak: EnrollmentKeycloak {
                url: "https://auth.example.com/".into(),
                realm: "mavi-vpn".into(),
                client_id: "mavi-client".into(),
            },
        }
    }

    #[test]
    fn profile_converts_to_secret_free_keycloak_config() {
        let config = profile().into_config().unwrap();
        assert_eq!(config.endpoint, "vpn.example.com:10443");
        assert_eq!(config.kc_url.as_deref(), Some("https://auth.example.com"));
        assert_eq!(config.kc_auth, Some(true));
        assert!(config.http3_framing);
        assert!(config.censorship_resistant);
        assert!(!config.http2_framing);
        assert_eq!(config.vpn_mtu, Some(1280));
        assert!(config.token.is_empty());
        assert!(config.refresh_token.is_none());
    }

    #[test]
    fn profile_rejects_unknown_fields_and_credentials() {
        let mut value = serde_json::to_value(profile()).unwrap();
        value["refresh_token"] = serde_json::Value::String("do-not-import".into());
        assert!(serde_json::from_value::<EnrollmentProfile>(value).is_err());
    }

    #[test]
    fn profile_rejects_schema_and_mtu_changes() {
        let mut wrong_schema = profile();
        wrong_schema.schema = "mavi.connection.v2".into();
        assert!(wrong_schema.validate().is_err());

        let mut wrong_mtu = profile();
        wrong_mtu.vpn_mtu = 1360;
        assert!(wrong_mtu.validate().is_err());
    }

    #[test]
    fn profile_rejects_ambiguous_endpoints_and_keycloak_paths() {
        let mut endpoint = profile();
        endpoint.endpoint = "https://user@vpn.example.com:10443".into();
        assert!(endpoint.validate().is_err());

        let mut realm = profile();
        realm.keycloak.realm = "other/realm".into();
        assert!(realm.validate().is_err());
    }

    #[test]
    fn transport_mapping_covers_http2() {
        let mut value = profile();
        value.transport = EnrollmentTransport::Http2;
        assert!(value.clone().into_config().is_err());
        value.ech_config = None;
        let config = value.into_config().unwrap();
        assert!(config.http2_framing);
        assert!(!config.http3_framing);
        assert!(!config.censorship_resistant);
    }

    #[cfg(feature = "oauth-client")]
    #[test]
    fn enrollment_links_resolve_to_redemption_endpoint() {
        use client::EnrollmentRequest;

        let ticket = "A".repeat(43);
        let request =
            EnrollmentRequest::parse(&format!("https://panel.example.com/enroll/{ticket}"))
                .unwrap();
        assert_eq!(
            request.redemption_url().as_str(),
            "https://panel.example.com/api/v1/enrollment/redeem"
        );
        assert!(!format!("{request:?}").contains(&ticket));

        let request = EnrollmentRequest::parse(&format!(
            "mavivpn://enroll?ticket={ticket}&panel=https%3A%2F%2Fpanel.example.com"
        ))
        .unwrap();
        assert_eq!(
            request.redemption_url().as_str(),
            "https://panel.example.com/api/v1/enrollment/redeem"
        );
    }

    #[cfg(feature = "oauth-client")]
    #[test]
    fn enrollment_links_reject_unsafe_urls_and_extra_parameters() {
        use client::EnrollmentRequest;

        let ticket = "A".repeat(43);
        for link in [
            format!("http://panel.example.com/enroll/{ticket}"),
            format!("https://user@panel.example.com/enroll/{ticket}"),
            format!("https://panel.example.com/enroll/{ticket}?next=other"),
            format!(
                "mavivpn://enroll?ticket={ticket}&panel=https%3A%2F%2Fpanel.example.com&next=other"
            ),
            "https://panel.example.com/enroll/short".into(),
        ] {
            assert!(EnrollmentRequest::parse(&link).is_err(), "accepted {link}");
        }
    }
}
