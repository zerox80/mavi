use super::insert_audit;
use crate::security::hash_token;
use chrono::{DateTime, Utc};
use sqlx::{PgPool, Row};
use uuid::Uuid;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RedeemedDevice {
    pub id: Uuid,
    pub label: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum RedeemResult {
    Redeemed(RedeemedDevice),
    NotFound,
    ExpiredOrRedeemed,
}

pub async fn redeem(pool: &PgPool, ticket: &str) -> Result<RedeemResult, sqlx::Error> {
    let mut transaction = pool.begin().await?;
    let row = sqlx::query(
        "SELECT ticket.device_id, ticket.expires_at, ticket.redeemed_at, device.label
         FROM panel_enrollment_tickets AS ticket
         JOIN panel_devices AS device ON device.id = ticket.device_id
         WHERE ticket.token_hash = $1
         FOR UPDATE OF ticket, device",
    )
    .bind(hash_token(ticket))
    .fetch_optional(&mut *transaction)
    .await?;
    let Some(row) = row else {
        transaction.rollback().await?;
        return Ok(RedeemResult::NotFound);
    };
    let redeemed_at: Option<DateTime<Utc>> = row.try_get("redeemed_at")?;
    let expires_at: DateTime<Utc> = row.try_get("expires_at")?;
    if redeemed_at.is_some() || expires_at <= Utc::now() {
        transaction.rollback().await?;
        return Ok(RedeemResult::ExpiredOrRedeemed);
    }
    let device_id: Uuid = row.try_get("device_id")?;
    let label: String = row.try_get("label")?;
    let update = sqlx::query(
        "UPDATE panel_enrollment_tickets SET redeemed_at = now()
         WHERE token_hash = $1 AND redeemed_at IS NULL AND expires_at > now()",
    )
    .bind(hash_token(ticket))
    .execute(&mut *transaction)
    .await?;
    if update.rows_affected() != 1 {
        transaction.rollback().await?;
        return Ok(RedeemResult::ExpiredOrRedeemed);
    }
    sqlx::query("UPDATE panel_devices SET status = 'imported', imported_at = now() WHERE id = $1")
        .bind(device_id)
        .execute(&mut *transaction)
        .await?;
    insert_audit(
        &mut transaction,
        "enrollment",
        "enrollment_ticket_redeemed",
        device_id,
        "Consumed the single-use ticket and marked the device imported",
    )
    .await?;
    transaction.commit().await?;
    Ok(RedeemResult::Redeemed(RedeemedDevice {
        id: device_id,
        label,
    }))
}
