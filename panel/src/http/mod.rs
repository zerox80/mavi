mod api;
mod assets;
mod auth;
mod enrollment;
mod middleware;

use crate::db::sessions::{self, Session};
use crate::security::{cookie_value, csrf_matches, origin_is_allowed, SESSION_COOKIE};
use crate::state::AppState;
use axum::extract::DefaultBodyLimit;
use axum::http::{HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{delete, get, post};
use axum::{Json, Router};
use serde_json::json;

pub fn router(state: AppState) -> Router {
    let insecure_http = state.config.insecure_http;
    Router::new()
        .route("/", get(assets::index))
        .route("/login", get(assets::index))
        .route("/app.js", get(assets::app_js))
        .route("/enroll.js", get(assets::enroll_js))
        .route("/styles.css", get(assets::styles))
        .route("/enroll/{ticket}", get(assets::enroll))
        .route("/auth/login", get(auth::login))
        .route("/auth/callback", get(auth::callback))
        .route("/auth/logout", post(auth::logout))
        .route("/api/v1/session", get(api::session))
        .route("/api/v1/server", get(api::server))
        .route(
            "/api/v1/devices",
            get(api::list_devices).post(api::create_device),
        )
        .route("/api/v1/devices/{id}", delete(api::delete_device))
        .route("/api/v1/enrollment/redeem", post(enrollment::redeem))
        .route("/health/live", get(api::live))
        .route("/health/ready", get(api::ready))
        .fallback(api::not_found)
        .layer(DefaultBodyLimit::max(16 * 1024))
        .layer(axum::middleware::from_fn_with_state(
            insecure_http,
            middleware::security_headers,
        ))
        .with_state(state)
}

pub struct Authenticated {
    pub token: String,
    pub session: Session,
}

pub async fn authenticate(
    state: &AppState,
    headers: &HeaderMap,
) -> Result<Authenticated, ApiError> {
    let token = cookie_value(headers, SESSION_COOKIE)
        .filter(|token| crate::security::valid_ticket(token))
        .ok_or_else(|| ApiError::new(StatusCode::UNAUTHORIZED, "Authentication is required"))?;
    let session = sessions::find_session(&state.pool, &token)
        .await
        .map_err(|_| ApiError::internal("session lookup"))?
        .ok_or_else(|| ApiError::new(StatusCode::UNAUTHORIZED, "Authentication is required"))?;
    Ok(Authenticated { token, session })
}

pub fn check_mutation(
    state: &AppState,
    headers: &HeaderMap,
    authenticated: &Authenticated,
) -> Result<(), ApiError> {
    if !origin_is_allowed(headers, &state.config.public_origin) {
        return Err(ApiError::new(
            StatusCode::FORBIDDEN,
            "Origin is not allowed",
        ));
    }
    if !csrf_matches(headers, &authenticated.session.csrf_hash) {
        return Err(ApiError::new(
            StatusCode::FORBIDDEN,
            "CSRF validation failed",
        ));
    }
    Ok(())
}

#[derive(Debug)]
pub struct ApiError {
    status: StatusCode,
    message: &'static str,
}

impl ApiError {
    pub fn new(status: StatusCode, message: &'static str) -> Self {
        Self { status, message }
    }

    pub fn internal(operation: &'static str) -> Self {
        tracing::error!(operation, "Panel request failed");
        Self::new(StatusCode::INTERNAL_SERVER_ERROR, "Internal server error")
    }
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        (self.status, Json(json!({"error": self.message}))).into_response()
    }
}

pub fn no_content() -> Response {
    StatusCode::NO_CONTENT.into_response()
}
