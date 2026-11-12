use super::ApiError;
use crate::db::enrollment::{self, RedeemResult};
use crate::security::valid_ticket;
use crate::state::AppState;
use axum::extract::rejection::JsonRejection;
use axum::extract::{ConnectInfo, State};
use axum::http::{header, HeaderMap, HeaderValue, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde::{Deserialize, Serialize};
use shared::enrollment::EnrollmentProfile;
use std::net::{IpAddr, SocketAddr};

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
    headers: HeaderMap,
    payload: Result<Json<RedeemRequest>, JsonRejection>,
) -> Result<Response, ApiError> {
    let client = client_address(address.ip(), &headers, state.config.proxy_token.as_deref());
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

fn client_address(peer: IpAddr, headers: &HeaderMap, proxy_token: Option<&str>) -> IpAddr {
    let forwarded = (|| {
        let token = single_header(headers, "x-mavi-proxy-token")?;
        if !constant_time_eq::constant_time_eq(token.as_bytes(), proxy_token?.as_bytes()) {
            return None;
        }
        // The authenticated ingress appends its immediate peer after any
        // forwarded addresses supplied by the client.
        let chain = single_header(headers, "x-forwarded-for")?.as_bytes();
        let client = chain.rsplit(|byte| *byte == b',').next()?;
        std::str::from_utf8(client)
            .ok()?
            .trim()
            .parse::<IpAddr>()
            .ok()
    })();
    forwarded.unwrap_or(peer).to_canonical()
}

fn single_header<'a>(headers: &'a HeaderMap, name: &'static str) -> Option<&'a HeaderValue> {
    let values = headers.get_all(name);
    let mut values = values.iter();
    let value = values.next()?;
    values.next().is_none().then_some(value)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn authenticated_proxy_preserves_independent_client_budgets() {
        let token = "test-proxy-token";
        let peer = "172.20.0.1".parse().unwrap();
        let mut headers = HeaderMap::new();
        headers.insert("x-mavi-proxy-token", HeaderValue::from_static(token));
        headers.insert(
            "x-forwarded-for",
            HeaderValue::from_static("192.0.2.99, 192.0.2.10"),
        );
        let first = client_address(peer, &headers, Some(token));
        assert_eq!(first, "192.0.2.10".parse::<IpAddr>().unwrap());
        headers.insert(
            "x-forwarded-for",
            HeaderValue::from_bytes(b"\xff, 192.0.2.10").unwrap(),
        );
        assert_eq!(client_address(peer, &headers, Some(token)), first);
        let limiter = crate::rate_limit::EnrollmentLimiter::default();
        for _ in 0..10 {
            assert!(limiter.check(first).await.is_ok());
        }
        assert!(limiter.check(first).await.is_err());

        headers.insert("x-forwarded-for", HeaderValue::from_static("2001:db8::1"));
        let second = client_address(peer, &headers, Some(token));
        assert_eq!(second, "2001:db8::1".parse::<IpAddr>().unwrap());
        assert!(limiter.check(second).await.is_ok());
    }

    #[test]
    fn untrusted_or_ambiguous_forwarding_uses_the_socket_peer() {
        let peer = "172.20.0.1".parse().unwrap();
        let mut headers = HeaderMap::new();
        headers.insert("x-forwarded-for", HeaderValue::from_static("192.0.2.10"));
        assert_eq!(client_address(peer, &headers, Some("trusted")), peer);
        headers.insert("x-mavi-proxy-token", HeaderValue::from_static("wrong"));
        assert_eq!(client_address(peer, &headers, Some("trusted")), peer);
        headers.insert("x-mavi-proxy-token", HeaderValue::from_static("trusted"));
        assert_eq!(client_address(peer, &headers, None), peer);
        headers.append("x-forwarded-for", HeaderValue::from_static("192.0.2.11"));
        assert_eq!(client_address(peer, &headers, Some("trusted")), peer);
        headers.insert(
            "x-forwarded-for",
            HeaderValue::from_static("192.0.2.10, unknown"),
        );
        assert_eq!(client_address(peer, &headers, Some("trusted")), peer);
    }
}
