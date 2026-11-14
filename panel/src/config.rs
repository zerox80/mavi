use shared::enrollment::{
    EnrollmentKeycloak, EnrollmentProfile, EnrollmentTransport, ENROLLMENT_SCHEMA_V1,
};
use std::net::SocketAddr;
use std::path::PathBuf;
use std::str::FromStr;
use url::Url;

const MAX_SERVER_FILE_BYTES: usize = 16 * 1024;

mod endpoint;

#[derive(Debug, thiserror::Error)]
#[error("{0}")]
pub struct ConfigError(String);

impl ConfigError {
    fn new(message: impl Into<String>) -> Self {
        Self(message.into())
    }
}

#[derive(Clone)]
pub struct KeycloakSettings {
    pub url: String,
    pub realm: String,
    pub client_id: String,
    pub client_secret: String,
}

impl KeycloakSettings {
    pub fn issuer(&self) -> String {
        format!("{}/realms/{}", self.url, self.realm)
    }

    pub fn authorization_endpoint(&self) -> String {
        format!("{}/protocol/openid-connect/auth", self.issuer())
    }

    pub fn token_endpoint(&self) -> String {
        format!("{}/protocol/openid-connect/token", self.issuer())
    }

    pub fn jwks_uri(&self) -> String {
        format!("{}/protocol/openid-connect/certs", self.issuer())
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PanelTransport {
    Http3CensorshipResistant,
    Http3,
    Quic,
    Http2,
}

impl PanelTransport {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Http3CensorshipResistant => "http3_censorship_resistant",
            Self::Http3 => "http3",
            Self::Quic => "quic",
            Self::Http2 => "http2",
        }
    }

    pub fn enrollment(self) -> EnrollmentTransport {
        match self {
            Self::Http3CensorshipResistant => EnrollmentTransport::Http3CensorshipResistant,
            Self::Http3 => EnrollmentTransport::Http3,
            Self::Quic => EnrollmentTransport::Quic,
            Self::Http2 => EnrollmentTransport::Http2,
        }
    }
}

impl FromStr for PanelTransport {
    type Err = ConfigError;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        match value {
            "http3_censorship_resistant" => Ok(Self::Http3CensorshipResistant),
            "http3" => Ok(Self::Http3),
            "quic" => Ok(Self::Quic),
            "http2" => Ok(Self::Http2),
            _ => Err(ConfigError::new(
                "MAVI_PANEL_TRANSPORT has an invalid value",
            )),
        }
    }
}

#[derive(Clone)]
pub struct Config {
    pub database_url: String,
    pub keycloak: KeycloakSettings,
    pub vpn_client_id: String,
    pub proxy_token: Option<String>,
    pub public_url: Url,
    pub public_base: String,
    pub public_origin: String,
    pub vpn_endpoint: String,
    pub cert_pin_file: PathBuf,
    pub ech_config_file: PathBuf,
    pub transport: PanelTransport,
    pub bind: SocketAddr,
    pub insecure_http: bool,
}

impl Config {
    pub fn from_env() -> Result<Self, ConfigError> {
        Self::from_lookup(|name| std::env::var(name).ok())
    }

    fn from_lookup(mut lookup: impl FnMut(&str) -> Option<String>) -> Result<Self, ConfigError> {
        let required = |name: &str, lookup: &mut dyn FnMut(&str) -> Option<String>| {
            lookup(name)
                .filter(|value| !value.trim().is_empty())
                .ok_or_else(|| ConfigError::new(format!("{name} is required")))
        };
        let database_url = required("MAVI_PANEL_DATABASE_URL", &mut lookup)?;
        let keycloak_url = required("MAVI_PANEL_KEYCLOAK_URL", &mut lookup)?;
        let realm = required("MAVI_PANEL_KEYCLOAK_REALM", &mut lookup)?;
        let client_id = required("MAVI_PANEL_CLIENT_ID", &mut lookup)?;
        let client_secret = required("MAVI_PANEL_CLIENT_SECRET", &mut lookup)?;
        let vpn_client_id =
            lookup("MAVI_PANEL_VPN_CLIENT_ID").unwrap_or_else(|| "mavi-client".into());
        if vpn_client_id == client_id {
            return Err(ConfigError::new(
                "MAVI_PANEL_VPN_CLIENT_ID must identify a different client from MAVI_PANEL_CLIENT_ID",
            ));
        }
        let proxy_token = lookup("MAVI_PANEL_PROXY_TOKEN").filter(|value| !value.is_empty());
        if proxy_token.as_deref().is_some_and(|value| {
            !(32..=128).contains(&value.len())
                || !value
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
        }) {
            return Err(ConfigError::new(
                "MAVI_PANEL_PROXY_TOKEN has an invalid value",
            ));
        }
        let public_url = required("MAVI_PANEL_PUBLIC_URL", &mut lookup)?;
        let insecure_http = parse_bool(
            lookup("MAVI_PANEL_INSECURE_HTTP")
                .as_deref()
                .unwrap_or("false"),
        )?;
        let public_url = parse_web_url("MAVI_PANEL_PUBLIC_URL", &public_url, insecure_http)?;
        let keycloak_url = parse_web_url("MAVI_PANEL_KEYCLOAK_URL", &keycloak_url, insecure_http)?;
        if public_url.path() != "/" {
            return Err(ConfigError::new(
                "MAVI_PANEL_PUBLIC_URL must not contain a path",
            ));
        }
        if keycloak_url.path() != "/" {
            return Err(ConfigError::new(
                "MAVI_PANEL_KEYCLOAK_URL must not contain a path",
            ));
        }
        let transport = lookup("MAVI_PANEL_TRANSPORT")
            .as_deref()
            .unwrap_or("http3_censorship_resistant")
            .parse()?;
        let vpn_endpoint = endpoint::resolve(transport, &mut lookup)?;
        let bind = lookup("MAVI_PANEL_BIND")
            .as_deref()
            .unwrap_or("0.0.0.0:8081")
            .parse()
            .map_err(|_| ConfigError::new("MAVI_PANEL_BIND has an invalid value"))?;
        let public_base = public_url.as_str().trim_end_matches('/').to_string();
        let public_origin = public_url.origin().ascii_serialization();
        let config = Self {
            database_url,
            keycloak: KeycloakSettings {
                url: keycloak_url.as_str().trim_end_matches('/').to_string(),
                realm,
                client_id,
                client_secret,
            },
            vpn_client_id,
            proxy_token,
            public_url,
            public_base,
            public_origin,
            vpn_endpoint,
            cert_pin_file: PathBuf::from(
                lookup("MAVI_PANEL_CERT_PIN_FILE")
                    .unwrap_or_else(|| "/server-data/cert_pin.txt".into()),
            ),
            ech_config_file: PathBuf::from(
                lookup("MAVI_PANEL_ECH_CONFIG_FILE")
                    .unwrap_or_else(|| "/server-data/ech_config_hex.txt".into()),
            ),
            transport,
            bind,
            insecure_http,
        };
        let profile = config.profile_from_files(
            "00000000-0000-4000-8000-000000000000".into(),
            "Configuration check".into(),
            "ab".repeat(32),
            None,
        )?;
        let _ = config.keycloak.client_secret.is_empty();
        profile.validate().map_err(ConfigError::new)?;
        Ok(config)
    }

    pub fn redirect_uri(&self) -> String {
        format!("{}/auth/callback", self.public_base)
    }

    pub fn enrollment_url(&self, ticket: &str) -> String {
        format!("{}/enroll/{ticket}", self.public_base)
    }

    pub fn deep_link(&self, ticket: &str) -> String {
        let mut url = Url::parse("mavivpn://enroll").expect("static deep link URL is valid");
        url.query_pairs_mut()
            .append_pair("ticket", ticket)
            .append_pair("panel", &self.public_base);
        url.into()
    }

    pub async fn enrollment_profile(
        &self,
        device_id: String,
        label: String,
    ) -> Result<EnrollmentProfile, ConfigError> {
        let cert_pin = read_required_file(&self.cert_pin_file).await?;
        let ech_config = if self.transport == PanelTransport::Http2 {
            None
        } else {
            read_optional_file(&self.ech_config_file).await?
        };
        let profile = self.profile_from_files(device_id, label, cert_pin, ech_config)?;
        profile.validate().map_err(ConfigError::new)?;
        Ok(profile)
    }

    fn profile_from_files(
        &self,
        device_id: String,
        label: String,
        cert_pin: String,
        ech_config: Option<String>,
    ) -> Result<EnrollmentProfile, ConfigError> {
        Ok(EnrollmentProfile {
            schema: ENROLLMENT_SCHEMA_V1.into(),
            device_id,
            label,
            endpoint: self.vpn_endpoint.clone(),
            cert_pin,
            transport: self.transport.enrollment(),
            ech_config,
            vpn_mtu: shared::DEFAULT_TUN_MTU,
            keycloak: EnrollmentKeycloak {
                url: self.keycloak.url.clone(),
                realm: self.keycloak.realm.clone(),
                client_id: self.vpn_client_id.clone(),
            },
        })
    }
}

fn parse_bool(value: &str) -> Result<bool, ConfigError> {
    match value {
        "true" => Ok(true),
        "false" => Ok(false),
        _ => Err(ConfigError::new(
            "MAVI_PANEL_INSECURE_HTTP must be true or false",
        )),
    }
}

fn parse_web_url(name: &str, value: &str, insecure_http: bool) -> Result<Url, ConfigError> {
    let url = Url::parse(value).map_err(|_| ConfigError::new(format!("{name} is invalid")))?;
    let valid_scheme = url.scheme() == "https" || (insecure_http && url.scheme() == "http");
    if !valid_scheme
        || url.host_str().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return Err(ConfigError::new(format!("{name} is invalid")));
    }
    Ok(url)
}

async fn read_required_file(path: &PathBuf) -> Result<String, ConfigError> {
    let bytes = tokio::fs::read(path)
        .await
        .map_err(|_| ConfigError::new("Could not read the certificate PIN file"))?;
    parse_server_file(&bytes, "Certificate PIN file")
}

async fn read_optional_file(path: &PathBuf) -> Result<Option<String>, ConfigError> {
    match tokio::fs::read(path).await {
        Ok(bytes) => parse_server_file(&bytes, "ECH configuration file").map(Some),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(_) => Err(ConfigError::new(
            "Could not read the ECH configuration file",
        )),
    }
}

fn parse_server_file(bytes: &[u8], name: &str) -> Result<String, ConfigError> {
    if bytes.is_empty() || bytes.len() > MAX_SERVER_FILE_BYTES {
        return Err(ConfigError::new(format!("{name} has an invalid size")));
    }
    let value = std::str::from_utf8(bytes)
        .map_err(|_| ConfigError::new(format!("{name} is not UTF-8")))?
        .trim()
        .to_string();
    if value.is_empty() {
        return Err(ConfigError::new(format!("{name} is empty")));
    }
    Ok(value)
}

#[cfg(test)]
mod tests;
