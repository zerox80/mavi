use anyhow::{Context, Result};
use bytes::Buf;

const MAX_RESPONSE_BYTES: usize = 64 * 1024;

/// Send a real HTTP/3 request, including SETTINGS and QPACK/control streams.
pub async fn request(connection: quinn::Connection) -> Result<http::Response<Vec<u8>>> {
    let (mut driver, mut sender) = h3::client::new(h3_quinn::Connection::new(connection)).await?;
    let exchange = async {
        let request = http::Request::builder()
            .uri("https://localhost/")
            .body(())?;
        let mut stream = sender.send_request(request).await?;
        stream.finish().await?;
        let response = stream.recv_response().await?;
        let mut body = Vec::new();
        while let Some(mut data) = stream.recv_data().await? {
            anyhow::ensure!(
                data.remaining() <= MAX_RESPONSE_BYTES - body.len(),
                "HTTP/3 response exceeds {MAX_RESPONSE_BYTES} bytes"
            );
            let length = data.remaining();
            body.extend_from_slice(&data.copy_to_bytes(length));
        }
        Ok(response.map(|()| body))
    };
    // Drive incoming control streams while waiting for response headers/data.
    // Both futures are dropped together on timeout or completion.
    tokio::select! {
        biased;
        result = exchange => result,
        error = std::future::poll_fn(|cx| driver.poll_close(cx)) => {
            Err(anyhow::anyhow!(error)).context("HTTP/3 connection closed before response completed")
        }
    }
}

#[cfg(test)]
mod tests;
