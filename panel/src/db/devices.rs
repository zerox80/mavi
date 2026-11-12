use super::insert_audit;
use crate::oidc::Identity;
use crate::security::{hash_token, DevicePlatform};
use chrono::{DateTime, Duration, Utc};
use serde::Serialize;
use sqlx::{PgPool, Row};
use uuid::Uuid;

#[derive(Clone, Debug, Serialize)]
pub struct Device {
    pub id: Uuid,
    pub owner_username: String,
    pub label: String,
    pub platform: String,
    pub status: String,
    pub created_at: DateTime<Utc>,
    pub imported_at: Option<DateTime<Utc>>,
}

pub async fn list_devices(
    pool: &PgPool,
    subject: &str,
    administrator: bool,
) -> Result<Vec<Device>, sqlx::Error> {
    let rows = if administrator {
        sqlx::query(device_select("ORDER BY created_at DESC LIMIT 500").as_str())
            .fetch_all(pool)
            .await?
    } else {
        sqlx::query(
            device_select("WHERE owner_subject = $1 ORDER BY created_at DESC LIMIT 500").as_str(),
        )
        .bind(subject)
        .fetch_all(pool)
        .await?
    };
    rows.into_iter().map(device_from_row).collect()
}

pub async fn create_device(
    pool: &PgPool,
    identity: &Identity,
    label: &str,
    platform: DevicePlatform,
    ticket: &str,
) -> Result<Device, sqlx::Error> {
    let id = Uuid::new_v4();
    let mut transaction = pool.begin().await?;
    let row = sqlx::query(
        "INSERT INTO panel_devices (id, owner_subject, owner_username, label, platform, status)
         VALUES ($1, $2, $3, $4, $5, 'pending')
         RETURNING id, owner_username, label, platform, status, created_at, imported_at",
    )
    .bind(id)
    .bind(&identity.subject)
    .bind(&identity.username)
    .bind(label)
    .bind(platform.as_str())
    .fetch_one(&mut *transaction)
    .await?;
    sqlx::query(
        "INSERT INTO panel_enrollment_tickets (token_hash, device_id, expires_at)
         VALUES ($1, $2, $3)",
    )
    .bind(hash_token(ticket))
    .bind(id)
    .bind(Utc::now() + Duration::minutes(15))
    .execute(&mut *transaction)
    .await?;
    insert_audit(
        &mut transaction,
        &identity.subject,
        "device_enrollment_created",
        id,
        "Created device inventory and a single-use enrollment ticket",
    )
    .await?;
    transaction.commit().await?;
    device_from_row(row)
}

pub async fn delete_device(
    pool: &PgPool,
    id: Uuid,
    subject: &str,
    administrator: bool,
) -> Result<bool, sqlx::Error> {
    let mut transaction = pool.begin().await?;
    let row = sqlx::query(
        "DELETE FROM panel_devices
         WHERE id = $1 AND (owner_subject = $2 OR $3)
         RETURNING id",
    )
    .bind(id)
    .bind(subject)
    .bind(administrator)
    .fetch_optional(&mut *transaction)
    .await?;
    let Some(row) = row else {
        transaction.rollback().await?;
        return Ok(false);
    };
    insert_audit(
        &mut transaction,
        subject,
        "device_inventory_deleted",
        row.try_get("id")?,
        "Deleted inventory and its enrollment ticket; no VPN credential or session was revoked",
    )
    .await?;
    transaction.commit().await?;
    Ok(true)
}

fn device_select(suffix: &str) -> String {
    format!(
        "SELECT id, owner_username, label, platform, status, created_at, imported_at
         FROM panel_devices {suffix}"
    )
}

fn device_from_row(row: sqlx::postgres::PgRow) -> Result<Device, sqlx::Error> {
    Ok(Device {
        id: row.try_get("id")?,
        owner_username: row.try_get("owner_username")?,
        label: row.try_get("label")?,
        platform: row.try_get("platform")?,
        status: row.try_get("status")?,
        created_at: row.try_get("created_at")?,
        imported_at: row.try_get("imported_at")?,
    })
}
