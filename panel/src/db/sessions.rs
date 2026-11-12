use crate::oidc::Identity;
use crate::security::hash_token;
use chrono::{DateTime, Duration, Utc};
use serde::Serialize;
use sqlx::types::Json;
use sqlx::{PgPool, Row};

#[derive(Clone, Debug)]
pub struct Session {
    pub subject: String,
    pub username: String,
    pub display_name: String,
    pub roles: Vec<String>,
    pub csrf_hash: String,
}

#[derive(Clone, Debug)]
pub struct LoginTransaction {
    pub pkce_verifier: String,
    pub nonce: String,
}

#[derive(Serialize)]
pub struct SessionResponse {
    pub username: String,
    pub display_name: String,
    pub roles: Vec<String>,
    pub csrf_token: String,
    pub is_admin: bool,
}

pub async fn create_login(
    pool: &PgPool,
    state: &str,
    verifier: &str,
    nonce: &str,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        "INSERT INTO panel_login_transactions (state_hash, pkce_verifier, nonce, expires_at)
         VALUES ($1, $2, $3, $4)",
    )
    .bind(hash_token(state))
    .bind(verifier)
    .bind(nonce)
    .bind(Utc::now() + Duration::minutes(15))
    .execute(pool)
    .await?;
    Ok(())
}

pub async fn consume_login(
    pool: &PgPool,
    state: &str,
) -> Result<Option<LoginTransaction>, sqlx::Error> {
    let row = sqlx::query(
        "DELETE FROM panel_login_transactions
         WHERE state_hash = $1 AND expires_at > now()
         RETURNING pkce_verifier, nonce",
    )
    .bind(hash_token(state))
    .fetch_optional(pool)
    .await?;
    row.map(|row| {
        Ok(LoginTransaction {
            pkce_verifier: row.try_get("pkce_verifier")?,
            nonce: row.try_get("nonce")?,
        })
    })
    .transpose()
}

pub async fn create_session(
    pool: &PgPool,
    token: &str,
    csrf_token: &str,
    identity: &Identity,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        "INSERT INTO panel_sessions
         (token_hash, subject, username, display_name, roles, csrf_hash, expires_at)
         VALUES ($1, $2, $3, $4, $5, $6, $7)",
    )
    .bind(hash_token(token))
    .bind(&identity.subject)
    .bind(&identity.username)
    .bind(&identity.display_name)
    .bind(Json(&identity.roles))
    .bind(hash_token(csrf_token))
    .bind(Utc::now() + Duration::hours(12))
    .execute(pool)
    .await?;
    Ok(())
}

pub async fn find_session(pool: &PgPool, token: &str) -> Result<Option<Session>, sqlx::Error> {
    let row = sqlx::query(
        "SELECT subject, username, display_name, roles, csrf_hash
         FROM panel_sessions WHERE token_hash = $1 AND expires_at > now()",
    )
    .bind(hash_token(token))
    .fetch_optional(pool)
    .await?;
    row.map(session_from_row).transpose()
}

pub async fn rotate_csrf(
    pool: &PgPool,
    token: &str,
    csrf_token: &str,
) -> Result<Option<Session>, sqlx::Error> {
    let row = sqlx::query(
        "UPDATE panel_sessions SET csrf_hash = $2
         WHERE token_hash = $1 AND expires_at > now()
         RETURNING subject, username, display_name, roles, csrf_hash",
    )
    .bind(hash_token(token))
    .bind(hash_token(csrf_token))
    .fetch_optional(pool)
    .await?;
    row.map(session_from_row).transpose()
}

pub async fn delete_session(pool: &PgPool, token: &str) -> Result<(), sqlx::Error> {
    sqlx::query("DELETE FROM panel_sessions WHERE token_hash = $1")
        .bind(hash_token(token))
        .execute(pool)
        .await?;
    Ok(())
}

fn session_from_row(row: sqlx::postgres::PgRow) -> Result<Session, sqlx::Error> {
    let Json(roles) = row.try_get("roles")?;
    Ok(Session {
        subject: row.try_get("subject")?,
        username: row.try_get("username")?,
        display_name: row.try_get("display_name")?,
        roles,
        csrf_hash: row.try_get("csrf_hash")?,
    })
}

#[allow(dead_code)]
fn _timestamp_type_check(_: DateTime<Utc>) {}
