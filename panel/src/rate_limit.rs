use std::collections::HashMap;
use std::net::IpAddr;
use std::time::{Duration, Instant};
use tokio::sync::Mutex;

const WINDOW: Duration = Duration::from_secs(60);
const MAX_ATTEMPTS: u8 = 10;
const MAX_CLIENTS: usize = 4096;

struct Window {
    started: Instant,
    attempts: u8,
}

#[derive(Default)]
pub struct EnrollmentLimiter {
    clients: Mutex<HashMap<IpAddr, Window>>,
}

impl EnrollmentLimiter {
    pub async fn check(&self, client: IpAddr) -> Result<(), u64> {
        let mut clients = self.clients.lock().await;
        let now = Instant::now();
        if clients.len() >= MAX_CLIENTS {
            clients.retain(|_, window| now.duration_since(window.started) < WINDOW);
            if clients.len() >= MAX_CLIENTS && !clients.contains_key(&client) {
                return Err(WINDOW.as_secs());
            }
        }
        let window = clients.entry(client).or_insert_with(|| Window {
            started: now,
            attempts: 0,
        });
        if now.duration_since(window.started) >= WINDOW {
            window.started = now;
            window.attempts = 0;
        }
        if window.attempts >= MAX_ATTEMPTS {
            let remaining = WINDOW
                .as_secs()
                .saturating_sub(now.duration_since(window.started).as_secs());
            return Err(remaining.max(1));
        }
        window.attempts += 1;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn enrollment_attempts_are_limited_per_client() {
        let limiter = EnrollmentLimiter::default();
        let client = "192.0.2.10".parse().unwrap();
        let other = "192.0.2.11".parse().unwrap();
        for _ in 0..MAX_ATTEMPTS {
            assert!(limiter.check(client).await.is_ok());
        }
        assert_eq!(limiter.check(client).await, Err(60));
        assert!(limiter.check(other).await.is_ok());
    }
}
