use crate::config::KeycloakSettings;
use crate::security::{has_panel_access, is_admin, PANEL_ADMIN_ROLE, PANEL_USER_ROLE};
use jsonwebtoken::{decode, decode_header, jwk::JwkSet, Algorithm, DecodingKey, Validation};
use serde::Deserialize;
use serde_json::Value;
use std::time::{Duration, Instant};
use tokio::sync::{Mutex, RwLock};

const JWKS_MAX_AGE: Duration = Duration::from_secs(60 * 60);
const JWKS_REFRESH_COOLDOWN: Duration = Duration::from_secs(10);
const MAX_JWKS_BYTES: usize = 256 * 1024;
const MAX_TOKEN_RESPONSE_BYTES: usize = 64 * 1024;

#[derive(Debug, thiserror::Error)]
pub enum OidcError {
    #[error("Identity provider request failed")]
    Provider,
    #[error("Identity token validation failed")]
    InvalidToken,
    #[error("The account does not have panel access")]
    AccessDenied,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Identity {
    pub subject: String,
    pub username: String,
    pub display_name: String,
    pub roles: Vec<String>,
}

struct JwksCache {
    keys: JwkSet,
    fetched_at: Instant,
}

pub struct OidcClient {
    settings: KeycloakSettings,
    http: reqwest::Client,
    cache: RwLock<Option<JwksCache>>,
    refresh: Mutex<Option<Instant>>,
}

impl OidcClient {
    pub fn new(settings: KeycloakSettings) -> Result<Self, OidcError> {
        let http = reqwest::Client::builder()
            .timeout(Duration::from_secs(15))
            .build()
            .map_err(|_| OidcError::Provider)?;
        Ok(Self {
            settings,
            http,
            cache: RwLock::new(None),
            refresh: Mutex::new(None),
        })
    }

    pub async fn complete_login(
        &self,
        code: &str,
        verifier: &str,
        redirect_uri: &str,
        nonce: &str,
    ) -> Result<Identity, OidcError> {
        let tokens = self.exchange_code(code, verifier, redirect_uri).await?;
        let id_token = self.verify_id_token(&tokens.id_token, nonce).await?;
        let identity = self
            .verify_access_token(&tokens.access_token, &id_token)
            .await?;
        Ok(identity)
    }

    async fn exchange_code(
        &self,
        code: &str,
        verifier: &str,
        redirect_uri: &str,
    ) -> Result<TokenResponse, OidcError> {
        let response = self
            .http
            .post(self.settings.token_endpoint())
            .form(&[
                ("grant_type", "authorization_code"),
                ("client_id", self.settings.client_id.as_str()),
                ("client_secret", self.settings.client_secret.as_str()),
                ("code", code),
                ("code_verifier", verifier),
                ("redirect_uri", redirect_uri),
            ])
            .send()
            .await
            .map_err(|_| OidcError::Provider)?
            .error_for_status()
            .map_err(|_| OidcError::Provider)?;
        let bytes = read_capped(response, MAX_TOKEN_RESPONSE_BYTES).await?;
        serde_json::from_slice(&bytes).map_err(|_| OidcError::Provider)
    }

    async fn verify_id_token(&self, token: &str, nonce: &str) -> Result<IdIdentity, OidcError> {
        let keys = self.keys_for_token(token).await?;
        verify_id_token_with_keys(token, &keys, &self.settings, nonce)
    }

    async fn verify_access_token(
        &self,
        token: &str,
        id_identity: &IdIdentity,
    ) -> Result<Identity, OidcError> {
        let keys = self.keys_for_token(token).await?;
        verify_access_token_with_keys(token, &keys, &self.settings, id_identity)
    }

    async fn keys_for_token(&self, token: &str) -> Result<JwkSet, OidcError> {
        let kid = token_kid(token)?;
        if let Some(keys) = self.cached_keys(&kid).await? {
            return Ok(keys);
        }
        let mut last_attempt = self.refresh.lock().await;
        if let Some(keys) = self.cached_keys(&kid).await? {
            return Ok(keys);
        }
        if last_attempt.is_some_and(|attempt| attempt.elapsed() < JWKS_REFRESH_COOLDOWN) {
            return Err(OidcError::InvalidToken);
        }
        *last_attempt = Some(Instant::now());
        let keys = self.fetch_jwks().await?;
        if keys.find(&kid).is_none() {
            return Err(OidcError::InvalidToken);
        }
        *self.cache.write().await = Some(JwksCache {
            keys: keys.clone(),
            fetched_at: Instant::now(),
        });
        Ok(keys)
    }

    async fn cached_keys(&self, kid: &str) -> Result<Option<JwkSet>, OidcError> {
        let cache = self.cache.read().await;
        let Some(cache) = cache.as_ref() else {
            return Ok(None);
        };
        if cache.fetched_at.elapsed() >= JWKS_MAX_AGE {
            return Ok(None);
        }
        if cache.keys.find(kid).is_none() {
            return Ok(None);
        }
        Ok(Some(cache.keys.clone()))
    }

    async fn fetch_jwks(&self) -> Result<JwkSet, OidcError> {
        let response = self
            .http
            .get(self.settings.jwks_uri())
            .send()
            .await
            .map_err(|_| OidcError::Provider)?
            .error_for_status()
            .map_err(|_| OidcError::Provider)?;
        let bytes = read_capped(response, MAX_JWKS_BYTES).await?;
        serde_json::from_slice(&bytes).map_err(|_| OidcError::Provider)
    }
}

#[derive(Deserialize)]
struct TokenResponse {
    access_token: String,
    id_token: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct IdIdentity {
    pub subject: String,
    pub username: String,
    pub display_name: String,
}

fn token_kid(token: &str) -> Result<String, OidcError> {
    let header = decode_header(token).map_err(|_| OidcError::InvalidToken)?;
    if header.alg != Algorithm::RS256 {
        return Err(OidcError::InvalidToken);
    }
    header.kid.ok_or(OidcError::InvalidToken)
}

fn decode_claims(
    token: &str,
    keys: &JwkSet,
    settings: &KeycloakSettings,
    audience: Option<&str>,
) -> Result<Value, OidcError> {
    let kid = token_kid(token)?;
    let key = keys.find(&kid).ok_or(OidcError::InvalidToken)?;
    let key = DecodingKey::from_jwk(key).map_err(|_| OidcError::InvalidToken)?;
    let mut validation = Validation::new(Algorithm::RS256);
    validation.set_issuer(&[settings.issuer()]);
    validation.set_required_spec_claims(&["exp", "iss"]);
    validation.leeway = 30;
    validation.validate_nbf = true;
    if let Some(audience) = audience {
        validation.set_audience(&[audience]);
    } else {
        validation.validate_aud = false;
    }
    decode::<Value>(token, &key, &validation)
        .map(|data| data.claims)
        .map_err(|_| OidcError::InvalidToken)
}

fn verify_id_token_with_keys(
    token: &str,
    keys: &JwkSet,
    settings: &KeycloakSettings,
    nonce: &str,
) -> Result<IdIdentity, OidcError> {
    let claims = decode_claims(token, keys, settings, Some(&settings.client_id))?;
    let token_nonce = string_claim(&claims, "nonce")?;
    if !constant_time_eq::constant_time_eq(token_nonce.as_bytes(), nonce.as_bytes()) {
        return Err(OidcError::InvalidToken);
    }
    let subject = identity_text(string_claim(&claims, "sub")?)?;
    let username = claims
        .get("preferred_username")
        .and_then(Value::as_str)
        .map(identity_text)
        .transpose()?
        .unwrap_or_else(|| subject.clone());
    let display_name = claims
        .get("name")
        .and_then(Value::as_str)
        .map(identity_text)
        .transpose()?
        .unwrap_or_else(|| username.clone());
    Ok(IdIdentity {
        subject,
        username,
        display_name,
    })
}

fn verify_access_token_with_keys(
    token: &str,
    keys: &JwkSet,
    settings: &KeycloakSettings,
    id_identity: &IdIdentity,
) -> Result<Identity, OidcError> {
    let claims = decode_claims(token, keys, settings, None)?;
    if string_claim(&claims, "sub")? != id_identity.subject
        || string_claim(&claims, "azp")? != settings.client_id
        || string_claim(&claims, "typ")? != "Bearer"
    {
        return Err(OidcError::InvalidToken);
    }
    let roles = claims
        .get("realm_access")
        .and_then(|value| value.get("roles"))
        .and_then(Value::as_array)
        .ok_or(OidcError::AccessDenied)?
        .iter()
        .filter_map(Value::as_str)
        .filter(|role| matches!(*role, PANEL_ADMIN_ROLE | PANEL_USER_ROLE))
        .map(str::to_string)
        .collect::<Vec<_>>();
    if !has_panel_access(&roles) {
        return Err(OidcError::AccessDenied);
    }
    let mut roles = roles;
    roles.sort_by_key(|role| {
        if is_admin(std::slice::from_ref(role)) {
            0
        } else {
            1
        }
    });
    roles.dedup();
    Ok(Identity {
        subject: id_identity.subject.clone(),
        username: id_identity.username.clone(),
        display_name: id_identity.display_name.clone(),
        roles,
    })
}

fn string_claim<'a>(claims: &'a Value, name: &str) -> Result<&'a str, OidcError> {
    claims
        .get(name)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .ok_or(OidcError::InvalidToken)
}

fn identity_text(value: &str) -> Result<String, OidcError> {
    if value.is_empty() || value.chars().count() > 255 || value.chars().any(char::is_control) {
        return Err(OidcError::InvalidToken);
    }
    Ok(value.to_string())
}

async fn read_capped(
    mut response: reqwest::Response,
    maximum: usize,
) -> Result<Vec<u8>, OidcError> {
    if response
        .content_length()
        .is_some_and(|length| length > maximum as u64)
    {
        return Err(OidcError::Provider);
    }
    let mut bytes = Vec::new();
    while let Some(chunk) = response.chunk().await.map_err(|_| OidcError::Provider)? {
        if bytes.len().saturating_add(chunk.len()) > maximum {
            return Err(OidcError::Provider);
        }
        bytes.extend_from_slice(&chunk);
    }
    Ok(bytes)
}

#[cfg(test)]
mod tests;
