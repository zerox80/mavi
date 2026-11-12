use mavi_panel::config::Config;
use mavi_panel::db;
use mavi_panel::oidc::OidcClient;
use mavi_panel::state::AppState;
use std::time::Duration;

#[tokio::main]
async fn main() {
    // reqwest is built with "rustls-no-provider", so the process must install
    // a crypto provider before creating HTTP clients (same as the VPN clients).
    let _ = rustls::crypto::aws_lc_rs::default_provider().install_default();
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "mavi_panel=info".into()),
        )
        .init();
    if let Err(message) = run().await {
        eprintln!("Panel startup failed: {message}");
        std::process::exit(1);
    }
}

async fn run() -> Result<(), &'static str> {
    let config = Config::from_env().map_err(|_| "Invalid panel environment configuration")?;
    let pool = db::connect(&config.database_url)
        .await
        .map_err(|_| "Database connection failed")?;
    db::migrate(&pool)
        .await
        .map_err(|_| "Database migration failed")?;
    db::cleanup(&pool)
        .await
        .map_err(|_| "Database cleanup failed")?;
    let oidc = OidcClient::new(config.keycloak.clone())
        .map_err(|_| "OIDC client initialization failed")?;
    let bind = config.bind;
    let state = AppState::new(config, pool.clone(), oidc);
    spawn_cleanup(pool);
    let listener = tokio::net::TcpListener::bind(bind)
        .await
        .map_err(|_| "Could not bind the panel listener")?;
    tracing::info!(%bind, "Mavi panel is listening");
    axum::serve(
        listener,
        mavi_panel::http::router(state)
            .into_make_service_with_connect_info::<std::net::SocketAddr>(),
    )
    .with_graceful_shutdown(shutdown_signal())
    .await
    .map_err(|_| "Panel HTTP server failed")?;
    Ok(())
}

fn spawn_cleanup(pool: sqlx::PgPool) {
    tokio::spawn(async move {
        let mut interval = tokio::time::interval(Duration::from_secs(15 * 60));
        interval.tick().await;
        loop {
            interval.tick().await;
            if db::cleanup(&pool).await.is_err() {
                tracing::error!("Session cleanup failed");
            }
        }
    });
}

async fn shutdown_signal() {
    let ctrl_c = async {
        if tokio::signal::ctrl_c().await.is_err() {
            tracing::error!("Could not install the Ctrl-C handler");
        }
    };
    #[cfg(unix)]
    {
        use tokio::signal::unix::{signal, SignalKind};
        let terminate = async {
            match signal(SignalKind::terminate()) {
                Ok(mut signal) => signal.recv().await,
                Err(_) => {
                    tracing::error!("Could not install the termination signal handler");
                    std::future::pending().await
                }
            }
        };
        tokio::select! {
            () = ctrl_c => {},
            _ = terminate => {},
        }
    }
    #[cfg(not(unix))]
    ctrl_c.await;
}
