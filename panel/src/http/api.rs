use super::{authenticate, check_mutation, no_content, ApiError};
use crate::db::devices::{self, Device};
use crate::db::sessions::{self, SessionResponse};
use crate::oidc::Identity;
use crate::security::{is_admin, normalized_label, random_token, DevicePlatform};
use crate::state::AppState;
use axum::extract::rejection::JsonRejection;
use axum::extract::{Path, State};
use axum::http::{HeaderMap, StatusCode};
use axum::Json;
use qrcode::render::svg;
use qrcode::QrCode;
use serde::{Deserialize, Serialize};
use shared::enrollment::EnrollmentKeycloak;
use uuid::Uuid;

#[derive(Serialize)]
pub struct ServerResponse {
    name: &'static str,
    endpoint: String,
    transport: &'static str,
    cert_pin: String,
    ech_config: Option<String>,
    vpn_mtu: u16,
    keycloak: EnrollmentKeycloak,
}

#[derive(Serialize)]
pub struct DeviceListResponse {
    devices: Vec<Device>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CreateDeviceRequest {
    label: String,
    platform: DevicePlatform,
}

#[derive(Serialize)]
pub struct CreateDeviceResponse {
    id: Uuid,
    label: String,
    platform: DevicePlatform,
    status: &'static str,
    enrollment_url: String,
    deep_link: String,
    qr_svg: String,
}

pub async fn session(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> Result<Json<SessionResponse>, ApiError> {
    let authenticated = authenticate(&state, &headers).await?;
    let csrf_token = random_token().map_err(|_| ApiError::internal("CSRF randomness"))?;
    let session = sessions::rotate_csrf(&state.pool, &authenticated.token, &csrf_token)
        .await
        .map_err(|_| ApiError::internal("CSRF rotation"))?
        .ok_or_else(|| ApiError::new(StatusCode::UNAUTHORIZED, "Authentication is required"))?;
    let is_admin = is_admin(&session.roles);
    Ok(Json(SessionResponse {
        username: session.username,
        display_name: session.display_name,
        roles: session.roles,
        csrf_token,
        is_admin,
    }))
}

pub async fn server(State(state): State<AppState>) -> Result<Json<ServerResponse>, ApiError> {
    let profile = state
        .config
        .enrollment_profile(
            "00000000-0000-4000-8000-000000000000".into(),
            "Server configuration".into(),
        )
        .await
        .map_err(|_| ApiError::internal("server configuration"))?;
    Ok(Json(ServerResponse {
        name: "Mavi VPN",
        endpoint: profile.endpoint,
        transport: state.config.transport.as_str(),
        cert_pin: profile.cert_pin,
        ech_config: profile.ech_config,
        vpn_mtu: profile.vpn_mtu,
        keycloak: profile.keycloak,
    }))
}

pub async fn list_devices(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> Result<Json<DeviceListResponse>, ApiError> {
    let authenticated = authenticate(&state, &headers).await?;
    let devices = devices::list_devices(
        &state.pool,
        &authenticated.session.subject,
        is_admin(&authenticated.session.roles),
    )
    .await
    .map_err(|_| ApiError::internal("device list"))?;
    Ok(Json(DeviceListResponse { devices }))
}

pub async fn create_device(
    State(state): State<AppState>,
    headers: HeaderMap,
    payload: Result<Json<CreateDeviceRequest>, JsonRejection>,
) -> Result<Json<CreateDeviceResponse>, ApiError> {
    let authenticated = authenticate(&state, &headers).await?;
    check_mutation(&state, &headers, &authenticated)?;
    let Json(request) = payload.map_err(|_| {
        ApiError::new(
            StatusCode::BAD_REQUEST,
            "Request body must contain a valid label and platform",
        )
    })?;
    let label = normalized_label(&request.label)
        .map_err(|message| ApiError::new(StatusCode::BAD_REQUEST, message))?;
    let ticket = random_token().map_err(|_| ApiError::internal("enrollment randomness"))?;
    let enrollment_url = state.config.enrollment_url(&ticket);
    let deep_link = state.config.deep_link(&ticket);
    let qr_svg = enrollment_qr(&enrollment_url)?;
    let identity = Identity {
        subject: authenticated.session.subject,
        username: authenticated.session.username,
        display_name: authenticated.session.display_name,
        roles: authenticated.session.roles,
    };
    let device = devices::create_device(&state.pool, &identity, &label, request.platform, &ticket)
        .await
        .map_err(|_| ApiError::internal("device creation"))?;
    Ok(Json(CreateDeviceResponse {
        id: device.id,
        label: device.label,
        platform: request.platform,
        status: "pending",
        enrollment_url,
        deep_link,
        qr_svg,
    }))
}

pub async fn delete_device(
    State(state): State<AppState>,
    Path(id): Path<Uuid>,
    headers: HeaderMap,
) -> Result<axum::response::Response, ApiError> {
    let authenticated = authenticate(&state, &headers).await?;
    check_mutation(&state, &headers, &authenticated)?;
    let deleted = devices::delete_device(
        &state.pool,
        id,
        &authenticated.session.subject,
        is_admin(&authenticated.session.roles),
    )
    .await
    .map_err(|_| ApiError::internal("device deletion"))?;
    if !deleted {
        return Err(ApiError::new(StatusCode::NOT_FOUND, "Device not found"));
    }
    Ok(no_content())
}

pub async fn live() -> Json<serde_json::Value> {
    Json(serde_json::json!({"status": "ok"}))
}

pub async fn ready(State(state): State<AppState>) -> Result<Json<serde_json::Value>, ApiError> {
    sqlx::query("SELECT 1")
        .execute(&state.pool)
        .await
        .map_err(|_| ApiError::new(StatusCode::SERVICE_UNAVAILABLE, "Service is not ready"))?;
    state
        .config
        .enrollment_profile(
            "00000000-0000-4000-8000-000000000000".into(),
            "Readiness check".into(),
        )
        .await
        .map_err(|_| ApiError::new(StatusCode::SERVICE_UNAVAILABLE, "Service is not ready"))?;
    Ok(Json(serde_json::json!({"status": "ready"})))
}

pub async fn not_found() -> ApiError {
    ApiError::new(StatusCode::NOT_FOUND, "Not found")
}

fn enrollment_qr(enrollment_url: &str) -> Result<String, ApiError> {
    let code = QrCode::new(enrollment_url.as_bytes())
        .map_err(|_| ApiError::internal("enrollment QR encoding"))?;
    Ok(code
        .render::<svg::Color>()
        .min_dimensions(256, 256)
        .quiet_zone(true)
        .build())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn enrollment_qr_is_svg_without_embedded_credentials() {
        let svg = enrollment_qr("https://panel.example/enroll/test").unwrap();
        assert!(svg.starts_with("<?xml"));
        assert!(svg.contains("<svg xmlns"));
        assert!(!svg.contains("https://panel.example"));
        assert!(!svg.contains("<script"));
    }
}
