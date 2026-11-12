use crate::config::Config;
use crate::oidc::OidcClient;
use crate::rate_limit::EnrollmentLimiter;
use sqlx::PgPool;
use std::sync::Arc;

#[derive(Clone)]
pub struct AppState {
    pub config: Arc<Config>,
    pub pool: PgPool,
    pub oidc: Arc<OidcClient>,
    pub enrollment_limiter: Arc<EnrollmentLimiter>,
}

impl AppState {
    pub fn new(config: Config, pool: PgPool, oidc: OidcClient) -> Self {
        Self {
            config: Arc::new(config),
            pool,
            oidc: Arc::new(oidc),
            enrollment_limiter: Arc::new(EnrollmentLimiter::default()),
        }
    }
}
