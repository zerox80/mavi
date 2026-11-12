use super::ApiError;
use crate::db::enrollment::{self, RedeemResult};
use crate::security::valid_ticket;
use crate::state::AppState;
use axum::extract::rejection::JsonRejection;
use axum::extract::{ConnectInfo, State};
use axum::http::{header, HeaderValue, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde::{Deserialize, Serialize};
use shared::enrollment::EnrollmentProfile;
use std::net::SocketAddr;

const TICKET_ERROR: &str = "Enrollment ticket is invalid, expired, or already used";

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RedeemRequest {
    ticket: String,
}

#[derive(Serialize)]
pub struct RedeemResponse {
    profile: EnrollmentProfile,
}

pub async fn redeem(
    State(state): State<AppState>,
    ConnectInfo(address): ConnectInfo<SocketAddr>,
    payload: Result<Json<RedeemRequest>, JsonRejection>,
) -> Result<Response, ApiError> {
    let client = address.ip();
    if let Err(retry_after) = state.enrollment_limiter.check(client).await {
        let mut response = ApiError::new(
            StatusCode::TOO_MANY_REQUESTS,
            "Too many enrollment attempts; try again later",
        )
        .into_response();
        if let Ok(value) = HeaderValue::from_str(&retry_after.to_string()) {
            response.headers_mut().insert(header::RETRY_AFTER, value);
        }
        return Ok(response);
    }
    let Json(request) = payload.map_err(|_| {
        ApiError::new(
            StatusCode::BAD_REQUEST,
            "Request body must contain one enrollment ticket",
        )
    })?;
    if !valid_ticket(&request.ticket) {
        return Err(ApiError::new(StatusCode::NOT_FOUND, TICKET_ERROR));
    }
    let mut profile = state
        .config
        .enrollment_profile(
            "00000000-0000-4000-8000-000000000000".into(),
            "Enrollment".into(),
        )
        .await
        .map_err(|_| ApiError::internal("enrollment configuration"))?;
    match enrollment::redeem(&state.pool, &request.ticket)
        .await
        .map_err(|_| ApiError::internal("enrollment redemption"))?
    {
        RedeemResult::Redeemed(device) => {
            profile.device_id = device.id.to_string();
            profile.label = device.label;
            profile
                .validate()
                .map_err(|_| ApiError::internal("enrollment profile validation"))?;
            Ok(Json(RedeemResponse { profile }).into_response())
        }
        RedeemResult::NotFound => Err(ApiError::new(StatusCode::NOT_FOUND, TICKET_ERROR)),
        RedeemResult::ExpiredOrRedeemed => Err(ApiError::new(StatusCode::GONE, TICKET_ERROR)),
    }
}
