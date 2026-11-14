use super::{ConfigError, PanelTransport};
use std::net::{Ipv6Addr, SocketAddr};

/// Explicit endpoints support an external port mapping; otherwise follow the
/// same listener settings as the VPN service every time Compose recreates us.
pub(super) fn resolve(
    transport: PanelTransport,
    lookup: &mut impl FnMut(&str) -> Option<String>,
) -> Result<String, ConfigError> {
    if let Some(endpoint) = nonempty(lookup, "MAVI_PANEL_VPN_ENDPOINT") {
        return Ok(endpoint);
    }
    let host = nonempty(lookup, "VPN_PUBLIC_HOST")
        .or_else(|| nonempty(lookup, "DOMAIN_NAME"))
        .ok_or_else(|| {
            ConfigError::new("VPN_PUBLIC_HOST is required for automatic VPN endpoints")
        })?;
    let key = if transport == PanelTransport::Http2 {
        "VPN_HTTP2_BIND_ADDR"
    } else {
        "VPN_BIND_ADDR"
    };
    let port = if let Some(bind) = nonempty(lookup, key) {
        bind.parse::<SocketAddr>()
            .map_err(|_| ConfigError::new(format!("{key} has an invalid value")))?
            .port()
    } else if transport == PanelTransport::Http2 {
        return Err(ConfigError::new(
            "VPN_HTTP2_BIND_ADDR is required for automatic HTTP/2 endpoints",
        ));
    } else {
        nonempty(lookup, "VPN_PORT")
            .as_deref()
            .unwrap_or("10443")
            .parse::<u16>()
            .map_err(|_| ConfigError::new("VPN_PORT must be between 1 and 65535"))?
    };
    if port == 0 {
        return Err(ConfigError::new("The public VPN port cannot be zero"));
    }
    let host = if host.parse::<Ipv6Addr>().is_ok() {
        format!("[{host}]")
    } else {
        host
    };
    Ok(format!("{host}:{port}"))
}

fn nonempty(lookup: &mut impl FnMut(&str) -> Option<String>, key: &str) -> Option<String> {
    lookup(key).filter(|value| !value.trim().is_empty())
}
