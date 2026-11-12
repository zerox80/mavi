use super::{authenticate, check_mutation, ApiError};
use crate::db::sessions;
use crate::oidc::OidcError;
use crate::security::{
    clear_login_cookie, clear_session_cookie, cookie_value, hash_token, login_cookie,
    pkce_challenge, random_token, session_cookie, valid_ticket, LOGIN_COOKIE,
};
use crate::state::AppState;
use axum::extract::{Query, State};
use axum::http::{header, HeaderMap, HeaderValue, StatusCode};
use axum::response::{IntoResponse, Response};
use serde::Deserialize;
use url::Url;

#[derive(Deserialize)]
pub struct CallbackParameters {
    code: Option<String>,
    state: Option<String>,
    error: Option<String>,
}

pub async fn login(State(state): State<AppState>) -> Result<Response, ApiError> {
    let state_value = random_token().map_err(|_| ApiError::internal("login randomness"))?;
    let verifier = random_token().map_err(|_| ApiError::internal("login randomness"))?;
    let nonce = random_token().map_err(|_| ApiError::internal("login randomness"))?;
    sessions::create_login(&state.pool, &state_value, &verifier, &nonce)
        .await
        .map_err(|_| ApiError::internal("login transaction creation"))?;
    let mut authorization = Url::parse(&state.config.keycloak.authorization_endpoint())
        .map_err(|_| ApiError::internal("authorization URL"))?;
    authorization
        .query_pairs_mut()
        .append_pair("response_type", "code")
        .append_pair("client_id", &state.config.keycloak.client_id)
        .append_pair("redirect_uri", &state.config.redirect_uri())
        .append_pair("scope", "openid profile")
        .append_pair("state", &state_value)
        .append_pair("nonce", &nonce)
        .append_pair("code_challenge", &pkce_challenge(&verifier))
        .append_pair("code_challenge_method", "S256");
    let mut response = redirect(authorization.as_str())?;
    append_cookie(
        &mut response,
        &login_cookie(&state_value, state.config.insecure_http),
    )?;
    Ok(response)
}

pub async fn callback(
    State(state): State<AppState>,
    headers: HeaderMap,
    Query(parameters): Query<CallbackParameters>,
) -> Response {
    match complete_callback(&state, &headers, parameters).await {
        Ok(cookie) => {
            let mut response = match redirect("/") {
                Ok(response) => response,
                Err(error) => return error.into_response(),
            };
            if append_cookie(&mut response, &cookie).is_err() {
                return ApiError::internal("session cookie").into_response();
            }
            let clear = clear_login_cookie(state.config.insecure_http);
            if append_cookie(&mut response, &clear).is_err() {
                return ApiError::internal("login cookie").into_response();
            }
            response
        }
        Err(error) => {
            let mut response = error.into_response();
            let clear = clear_login_cookie(state.config.insecure_http);
            if let Ok(value) = HeaderValue::from_str(&clear) {
                response.headers_mut().append(header::SET_COOKIE, value);
            }
            response
        }
    }
}

async fn complete_callback(
    state: &AppState,
    headers: &HeaderMap,
    parameters: CallbackParameters,
) -> Result<String, ApiError> {
    if parameters.error.is_some() {
        return Err(ApiError::new(
            StatusCode::BAD_REQUEST,
            "Sign-in was not completed",
        ));
    }
    let code = parameters
        .code
        .filter(|code| !code.is_empty() && code.len() <= 4096)
        .ok_or_else(|| ApiError::new(StatusCode::BAD_REQUEST, "Invalid sign-in response"))?;
    let state_value = parameters
        .state
        .filter(|value| valid_ticket(value))
        .ok_or_else(|| ApiError::new(StatusCode::BAD_REQUEST, "Invalid sign-in state"))?;
    let cookie_state = cookie_value(headers, LOGIN_COOKIE)
        .filter(|value| valid_ticket(value))
        .ok_or_else(|| ApiError::new(StatusCode::BAD_REQUEST, "Login cookie is missing"))?;
    if !constant_time_eq::constant_time_eq(
        hash_token(&state_value).as_bytes(),
        hash_token(&cookie_state).as_bytes(),
    ) {
        return Err(ApiError::new(
            StatusCode::BAD_REQUEST,
            "Sign-in state does not match",
        ));
    }
    let transaction = sessions::consume_login(&state.pool, &state_value)
        .await
        .map_err(|_| ApiError::internal("login transaction lookup"))?
        .ok_or_else(|| {
            ApiError::new(
                StatusCode::BAD_REQUEST,
                "Login transaction is invalid or expired",
            )
        })?;
    let identity = state
        .oidc
        .complete_login(
            &code,
            &transaction.pkce_verifier,
            &state.config.redirect_uri(),
            &transaction.nonce,
        )
        .await
        .map_err(|error| match error {
            OidcError::AccessDenied => ApiError::new(
                StatusCode::FORBIDDEN,
                "The account does not have panel access",
            ),
            OidcError::Provider => ApiError::new(
                StatusCode::BAD_GATEWAY,
                "Identity provider could not complete sign-in",
            ),
            OidcError::InvalidToken => {
                ApiError::new(StatusCode::UNAUTHORIZED, "Identity token validation failed")
            }
        })?;
    let session = random_token().map_err(|_| ApiError::internal("session randomness"))?;
    let csrf = random_token().map_err(|_| ApiError::internal("CSRF randomness"))?;
    sessions::create_session(&state.pool, &session, &csrf, &identity)
        .await
        .map_err(|_| ApiError::internal("session creation"))?;
    Ok(session_cookie(&session, state.config.insecure_http))
}

pub async fn logout(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> Result<Response, ApiError> {
    let authenticated = authenticate(&state, &headers).await?;
    check_mutation(&state, &headers, &authenticated)?;
    sessions::delete_session(&state.pool, &authenticated.token)
        .await
        .map_err(|_| ApiError::internal("session deletion"))?;
    let mut response = StatusCode::NO_CONTENT.into_response();
    append_cookie(
        &mut response,
        &clear_session_cookie(state.config.insecure_http),
    )?;
    Ok(response)
}

fn redirect(location: &str) -> Result<Response, ApiError> {
    let location =
        HeaderValue::from_str(location).map_err(|_| ApiError::internal("redirect location"))?;
    let mut response = StatusCode::SEE_OTHER.into_response();
    response.headers_mut().insert(header::LOCATION, location);
    Ok(response)
}

fn append_cookie(response: &mut Response, cookie: &str) -> Result<(), ApiError> {
    let value = HeaderValue::from_str(cookie).map_err(|_| ApiError::internal("cookie encoding"))?;
    response.headers_mut().append(header::SET_COOKIE, value);
    Ok(())
}
