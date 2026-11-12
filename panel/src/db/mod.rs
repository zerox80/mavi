pub mod devices;
pub mod enrollment;
pub mod sessions;

use sqlx::postgres::PgPoolOptions;
use sqlx::{PgPool, Postgres, Transaction};
use std::time::Duration;

const MIGRATIONS: &[&str] = &[
    "CREATE TABLE IF NOT EXISTS panel_sessions (
        token_hash TEXT PRIMARY KEY,
        subject TEXT NOT NULL,
        username TEXT NOT NULL,
        display_name TEXT NOT NULL,
        roles JSONB NOT NULL,
        csrf_hash TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        expires_at TIMESTAMPTZ NOT NULL
    )",
    "CREATE INDEX IF NOT EXISTS panel_sessions_expiry ON panel_sessions (expires_at)",
    "CREATE TABLE IF NOT EXISTS panel_login_transactions (
        state_hash TEXT PRIMARY KEY,
        pkce_verifier TEXT NOT NULL,
        nonce TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        expires_at TIMESTAMPTZ NOT NULL
    )",
    "CREATE INDEX IF NOT EXISTS panel_login_expiry ON panel_login_transactions (expires_at)",
    "CREATE TABLE IF NOT EXISTS panel_devices (
        id UUID PRIMARY KEY,
        owner_subject TEXT NOT NULL,
        owner_username TEXT NOT NULL,
        label TEXT NOT NULL,
        platform TEXT NOT NULL CHECK (platform IN ('windows', 'linux', 'android')),
        status TEXT NOT NULL CHECK (status IN ('pending', 'imported')),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        imported_at TIMESTAMPTZ
    )",
    "CREATE INDEX IF NOT EXISTS panel_devices_owner ON panel_devices (owner_subject, created_at)",
    "CREATE TABLE IF NOT EXISTS panel_enrollment_tickets (
        token_hash TEXT PRIMARY KEY,
        device_id UUID NOT NULL REFERENCES panel_devices(id) ON DELETE CASCADE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        expires_at TIMESTAMPTZ NOT NULL,
        redeemed_at TIMESTAMPTZ
    )",
    "CREATE INDEX IF NOT EXISTS panel_tickets_device ON panel_enrollment_tickets (device_id)",
    "CREATE TABLE IF NOT EXISTS panel_audit_events (
        id BIGSERIAL PRIMARY KEY,
        occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        actor_subject TEXT NOT NULL,
        action TEXT NOT NULL,
        device_id UUID,
        detail TEXT NOT NULL DEFAULT ''
    )",
    "CREATE INDEX IF NOT EXISTS panel_audit_time ON panel_audit_events (occurred_at DESC)",
];

pub async fn connect(database_url: &str) -> Result<PgPool, sqlx::Error> {
    PgPoolOptions::new()
        .max_connections(10)
        .acquire_timeout(Duration::from_secs(5))
        .connect(database_url)
        .await
}

pub async fn migrate(pool: &PgPool) -> Result<(), sqlx::Error> {
    let mut transaction = pool.begin().await?;
    for statement in MIGRATIONS {
        sqlx::query(statement).execute(&mut *transaction).await?;
    }
    transaction.commit().await
}

pub async fn cleanup(pool: &PgPool) -> Result<(), sqlx::Error> {
    let mut transaction = pool.begin().await?;
    sqlx::query("DELETE FROM panel_sessions WHERE expires_at <= now()")
        .execute(&mut *transaction)
        .await?;
    sqlx::query("DELETE FROM panel_login_transactions WHERE expires_at <= now()")
        .execute(&mut *transaction)
        .await?;
    transaction.commit().await
}

pub async fn insert_audit(
    transaction: &mut Transaction<'_, Postgres>,
    actor_subject: &str,
    action: &str,
    device_id: uuid::Uuid,
    detail: &str,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        "INSERT INTO panel_audit_events (actor_subject, action, device_id, detail)
         VALUES ($1, $2, $3, $4)",
    )
    .bind(actor_subject)
    .bind(action)
    .bind(device_id)
    .bind(detail)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}
