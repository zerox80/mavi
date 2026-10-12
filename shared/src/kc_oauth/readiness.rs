use super::is_access_token_usable;
use std::time::Duration;

/// Wait for the session's refresh worker to publish a usable access token.
/// Network failures must leave reconnect pending, rather than sending an
/// expired token and turning a temporary outage into permanent auth failure.
/// `min_lifetime_secs` reserves enough time for the upcoming handshake.
/// Returns `None` when the user stops or refresh requires a new login.
pub async fn wait_for_usable_access_token(
    read_token: impl Fn() -> String,
    is_running: impl Fn() -> bool,
    min_lifetime_secs: u64,
) -> Option<String> {
    while is_running() {
        let token = read_token();
        if is_access_token_usable(&token, min_lifetime_secs) {
            return Some(token);
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;
    use base64::Engine;
    use std::sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    };
    use std::time::{SystemTime, UNIX_EPOCH};

    fn token(seconds: i64) -> String {
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs() as i64;
        let payload = base64::engine::general_purpose::URL_SAFE_NO_PAD
            .encode(format!("{{\"exp\":{}}}", now + seconds));
        format!("e30.{payload}.signature")
    }

    #[tokio::test(start_paused = true)]
    async fn reconnect_waits_through_refresh_failures_then_uses_the_new_token() {
        let current = Arc::new(Mutex::new(token(-60)));
        let reader = current.clone();
        let waiting = tokio::spawn(async move {
            wait_for_usable_access_token(|| reader.lock().unwrap().clone(), || true, 15).await
        });
        tokio::task::yield_now().await;
        // No refresh result during the outage, including two failed ticks.
        tokio::time::advance(Duration::from_secs(65)).await;
        assert!(!waiting.is_finished());
        let fresh = token(600);
        *current.lock().unwrap() = fresh.clone();
        assert_eq!(waiting.await.unwrap(), Some(fresh));
    }

    #[tokio::test(start_paused = true)]
    async fn stop_or_login_failure_cancels_waiting_without_a_handshake() {
        let running = Arc::new(AtomicBool::new(true));
        let flag = running.clone();
        let waiting = tokio::spawn(async move {
            wait_for_usable_access_token(|| token(-60), || flag.load(Ordering::SeqCst), 15).await
        });
        tokio::task::yield_now().await;
        assert!(!waiting.is_finished());
        running.store(false, Ordering::SeqCst);
        assert_eq!(waiting.await.unwrap(), None);
    }

    #[tokio::test(start_paused = true)]
    async fn reserves_handshake_time_and_returns_fresh_tokens_without_delay() {
        let nearly_expired = token(5);
        let current = Arc::new(Mutex::new(nearly_expired));
        let reader = current.clone();
        let waiting = tokio::spawn(async move {
            wait_for_usable_access_token(|| reader.lock().unwrap().clone(), || true, 15).await
        });
        tokio::task::yield_now().await;
        assert!(!waiting.is_finished());
        let fresh = token(600);
        *current.lock().unwrap() = fresh.clone();
        assert_eq!(waiting.await.unwrap(), Some(fresh.clone()));
        assert_eq!(
            wait_for_usable_access_token(|| fresh.clone(), || true, 15).await,
            Some(fresh)
        );
    }
}
