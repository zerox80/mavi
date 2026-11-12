use axum::http::{header, HeaderMap};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use cookie::{Cookie, SameSite};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

pub const SESSION_COOKIE: &str = "__Host-mavi_session";
pub const LOGIN_COOKIE: &str = "__Host-mavi_login";
pub const CSRF_HEADER: &str = "x-mavi-csrf";
pub const PANEL_ADMIN_ROLE: &str = "panel-admin";
pub const PANEL_USER_ROLE: &str = "panel-user";
const TOKEN_BYTES: usize = 32;

#[derive(Debug, thiserror::Error)]
#[error("Could not generate random data")]
pub struct RandomError;

pub fn random_token() -> Result<String, RandomError> {
    let mut bytes = [0_u8; TOKEN_BYTES];
    getrandom::fill(&mut bytes).map_err(|_| RandomError)?;
    Ok(URL_SAFE_NO_PAD.encode(bytes))
}

pub fn hash_token(token: &str) -> String {
    URL_SAFE_NO_PAD.encode(Sha256::digest(token.as_bytes()))
}

pub fn pkce_challenge(verifier: &str) -> String {
    URL_SAFE_NO_PAD.encode(Sha256::digest(verifier.as_bytes()))
}

pub fn valid_ticket(ticket: &str) -> bool {
    if ticket.len() != 43 {
        return false;
    }
    let Ok(bytes) = URL_SAFE_NO_PAD.decode(ticket) else {
        return false;
    };
    bytes.len() == TOKEN_BYTES && URL_SAFE_NO_PAD.encode(bytes) == ticket
}

pub fn session_cookie(token: &str, insecure_http: bool) -> String {
    build_cookie(SESSION_COOKIE, token, insecure_http, 12 * 60 * 60)
}

pub fn login_cookie(state: &str, insecure_http: bool) -> String {
    build_cookie(LOGIN_COOKIE, state, insecure_http, 15 * 60)
}

pub fn clear_session_cookie(insecure_http: bool) -> String {
    build_cookie(SESSION_COOKIE, "", insecure_http, 0)
}

pub fn clear_login_cookie(insecure_http: bool) -> String {
    build_cookie(LOGIN_COOKIE, "", insecure_http, 0)
}

fn build_cookie(name: &'static str, value: &str, insecure_http: bool, max_age: i64) -> String {
    Cookie::build((name, value.to_string()))
        .path("/")
        .secure(!insecure_http)
        .http_only(true)
        .same_site(SameSite::Lax)
        .max_age(cookie::time::Duration::seconds(max_age))
        .build()
        .to_string()
}

pub fn cookie_value(headers: &HeaderMap, name: &str) -> Option<String> {
    let mut found = None;
    for header in headers.get_all(header::COOKIE) {
        let header = header.to_str().ok()?;
        for pair in header.split(';') {
            let (candidate, value) = pair.trim().split_once('=')?;
            if candidate == name {
                if found.is_some() || value.is_empty() {
                    return None;
                }
                found = Some(value.to_string());
            }
        }
    }
    found
}

pub fn origin_is_allowed(headers: &HeaderMap, expected_origin: &str) -> bool {
    headers
        .get(header::ORIGIN)
        .and_then(|value| value.to_str().ok())
        .is_some_and(|origin| {
            constant_time_eq::constant_time_eq(origin.as_bytes(), expected_origin.as_bytes())
        })
}

pub fn csrf_matches(headers: &HeaderMap, expected_hash: &str) -> bool {
    headers
        .get(CSRF_HEADER)
        .and_then(|value| value.to_str().ok())
        .is_some_and(|token| {
            valid_ticket(token)
                && constant_time_eq::constant_time_eq(
                    hash_token(token).as_bytes(),
                    expected_hash.as_bytes(),
                )
        })
}

pub fn normalized_label(label: &str) -> Result<String, &'static str> {
    let label = label.trim();
    if label.is_empty() || label.chars().count() > 80 {
        return Err("Label must contain 1 to 80 characters");
    }
    if label.chars().any(char::is_control) {
        return Err("Label contains control characters");
    }
    Ok(label.to_string())
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum DevicePlatform {
    Windows,
    Linux,
    Android,
}

impl DevicePlatform {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Windows => "windows",
            Self::Linux => "linux",
            Self::Android => "android",
        }
    }
}

pub fn is_admin(roles: &[String]) -> bool {
    roles.iter().any(|role| role == PANEL_ADMIN_ROLE)
}

pub fn has_panel_access(roles: &[String]) -> bool {
    roles
        .iter()
        .any(|role| role == PANEL_ADMIN_ROLE || role == PANEL_USER_ROLE)
}

pub fn can_delete_device(roles: &[String], subject: &str, owner_subject: &str) -> bool {
    is_admin(roles) || subject == owner_subject
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::http::HeaderValue;

    #[test]
    fn tokens_are_random_base64url_and_only_hashes_are_reused() {
        let first = random_token().unwrap();
        let second = random_token().unwrap();
        assert!(valid_ticket(&first));
        assert!(valid_ticket(&second));
        assert_ne!(first, second);
        assert_eq!(hash_token(&first), hash_token(&first));
        assert_ne!(hash_token(&first), first);
        assert_ne!(hash_token(&first), hash_token(&second));
    }

    #[test]
    fn ticket_validation_rejects_noncanonical_encodings() {
        assert!(!valid_ticket(""));
        assert!(!valid_ticket(&"a".repeat(43)));
        assert!(!valid_ticket(&format!("{}=", "a".repeat(42))));
        assert!(!valid_ticket(&format!("{}!", "a".repeat(42))));
    }

    #[test]
    fn pkce_uses_the_s256_challenge() {
        let verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk";
        assert_eq!(
            pkce_challenge(verifier),
            "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
        );
    }

    #[test]
    fn cookies_are_host_scoped_and_secure_by_default() {
        let cookie = session_cookie("opaque", false);
        assert!(cookie.starts_with("__Host-mavi_session=opaque"));
        assert!(cookie.contains("Path=/"));
        assert!(cookie.contains("Secure"));
        assert!(cookie.contains("HttpOnly"));
        assert!(cookie.contains("SameSite=Lax"));
        assert!(!cookie.contains("Domain="));
        let development = session_cookie("opaque", true);
        assert!(!development.contains("Secure"));
        assert!(clear_session_cookie(false).contains("Max-Age=0"));
    }

    #[test]
    fn cookie_parsing_rejects_duplicates_and_selects_the_session() {
        let mut headers = HeaderMap::new();
        headers.insert(
            header::COOKIE,
            HeaderValue::from_static("other=1; __Host-mavi_session=abc"),
        );
        assert_eq!(
            cookie_value(&headers, SESSION_COOKIE).as_deref(),
            Some("abc")
        );
        headers.insert(
            header::COOKIE,
            HeaderValue::from_static("__Host-mavi_session=abc; __Host-mavi_session=def"),
        );
        assert!(cookie_value(&headers, SESSION_COOKIE).is_none());
    }

    #[test]
    fn mutations_require_the_configured_origin_and_csrf_hash() {
        let token = random_token().unwrap();
        let hash = hash_token(&token);
        let mut headers = HeaderMap::new();
        headers.insert(
            header::ORIGIN,
            HeaderValue::from_static("https://panel.example"),
        );
        headers.insert(CSRF_HEADER, HeaderValue::from_str(&token).unwrap());
        assert!(origin_is_allowed(&headers, "https://panel.example"));
        assert!(csrf_matches(&headers, &hash));
        headers.insert(
            header::ORIGIN,
            HeaderValue::from_static("https://attacker.example"),
        );
        assert!(!origin_is_allowed(&headers, "https://panel.example"));
        headers.insert(CSRF_HEADER, HeaderValue::from_static("invalid"));
        assert!(!csrf_matches(&headers, &hash));
    }

    #[test]
    fn authorization_separates_admins_owners_and_other_users() {
        let admin = vec![PANEL_ADMIN_ROLE.to_string()];
        let user = vec![PANEL_USER_ROLE.to_string()];
        assert!(has_panel_access(&admin));
        assert!(has_panel_access(&user));
        assert!(!has_panel_access(&["vpn-user".into()]));
        assert!(can_delete_device(&admin, "admin", "owner"));
        assert!(can_delete_device(&user, "owner", "owner"));
        assert!(!can_delete_device(&user, "other", "owner"));
    }

    #[test]
    fn labels_are_trimmed_and_control_characters_are_rejected() {
        assert_eq!(normalized_label("  Work laptop ").unwrap(), "Work laptop");
        assert!(normalized_label("   ").is_err());
        assert!(normalized_label("bad\nlabel").is_err());
        assert!(normalized_label(&"x".repeat(81)).is_err());
    }
}
