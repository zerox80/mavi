use axum::http::{header, HeaderValue, StatusCode};
use axum::response::{Html, IntoResponse, Response};

const INDEX: &str = include_str!("../../web/index.html");
const ENROLL: &str = include_str!("../../web/enroll.html");
const APP_JS: &str = include_str!("../../web/app.js");
const ENROLL_JS: &str = include_str!("../../web/enroll.js");
const STYLES: &str = include_str!("../../web/styles.css");

pub async fn index() -> Html<&'static str> {
    Html(INDEX)
}

pub async fn enroll() -> Html<&'static str> {
    Html(ENROLL)
}

pub async fn app_js() -> Response {
    asset(APP_JS, "text/javascript; charset=utf-8")
}

pub async fn enroll_js() -> Response {
    asset(ENROLL_JS, "text/javascript; charset=utf-8")
}

pub async fn styles() -> Response {
    asset(STYLES, "text/css; charset=utf-8")
}

fn asset(content: &'static str, content_type: &'static str) -> Response {
    let mut response = (StatusCode::OK, content).into_response();
    response
        .headers_mut()
        .insert(header::CONTENT_TYPE, HeaderValue::from_static(content_type));
    response
}
