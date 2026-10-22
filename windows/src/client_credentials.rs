use anyhow::{Context, Result};
use std::fs::{File, OpenOptions, TryLockError};
use std::future::Future;
use std::path::Path;
use std::time::Duration;

use crate::ipc::{IpcRequest, IpcResponse, WINDOWS_CLI_CONNECTION_ID};
use crate::secrets::{config_refresh_token_account, SecretStore};

/// Check before performing OAuth: refreshing here would consume credentials
/// still owned by the active service worker even though Start will be rejected.
pub(crate) async fn prepare_start<F, Fut>(
    response: IpcResponse,
    prepare: F,
) -> Result<crate::ipc::Config>
where
    F: FnOnce() -> Fut,
    Fut: Future<Output = Result<crate::ipc::Config>>,
{
    ensure_can_start(response)?;
    prepare().await
}

fn ensure_can_start(response: IpcResponse) -> Result<()> {
    match response {
        IpcResponse::Status {
            state: crate::ipc::VpnState::Stopped | crate::ipc::VpnState::Failed,
            ..
        } => Ok(()),
        IpcResponse::Status { .. } => anyhow::bail!("VPN is already running or stopping"),
        IpcResponse::Error(error) => anyhow::bail!("Service returned an error: {error}"),
        _ => anyhow::bail!("Unexpected status response from service"),
    }
}

/// Serialize fetch/save/acknowledge and login/start across CLI processes using
/// the same keyring account. Closing the file also releases a cancelled lock.
pub(crate) async fn lock_credentials(config_path: &Path) -> Result<File> {
    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(config_path.with_file_name("keycloak-credentials.lock"))
        .context("Could not open CLI credential lock")?;
    loop {
        match file.try_lock() {
            Ok(()) => return Ok(file),
            Err(TryLockError::WouldBlock) => tokio::time::sleep(Duration::from_millis(50)).await,
            Err(TryLockError::Error(error)) => return Err(error.into()),
        }
    }
}

/// The service retains rotations across Stop. Save to the same account used by
/// load_config before acknowledging, so a failed write or lost reply is retryable.
pub(crate) async fn sync_refresh_tokens<R, F>(store: &dyn SecretStore, mut request: R) -> Result<()>
where
    R: FnMut(IpcRequest) -> F,
    F: Future<Output = Result<IpcResponse>>,
{
    loop {
        let response = request(IpcRequest::TakeRefreshTokenUpdateFor {
            connection_id: WINDOWS_CLI_CONNECTION_ID.into(),
        })
        .await?;
        match response {
            IpcResponse::RefreshTokenUpdate {
                connection_id: None,
                refresh_token: None,
            } => return Ok(()),
            IpcResponse::RefreshTokenUpdate {
                connection_id: Some(connection_id),
                refresh_token: Some(refresh_token),
            } if connection_id == WINDOWS_CLI_CONNECTION_ID && !refresh_token.trim().is_empty() => {
                store
                    .set_secret(config_refresh_token_account(), &refresh_token)
                    .context("Failed to store rotated CLI refresh token in the keyring")?;
                match request(IpcRequest::AcknowledgeRefreshTokenUpdate {
                    connection_id,
                    refresh_token,
                })
                .await?
                {
                    IpcResponse::Ok => {}
                    IpcResponse::Error(error) => {
                        anyhow::bail!("Service returned an error: {error}")
                    }
                    _ => anyhow::bail!("Unexpected refresh-token acknowledgement from service"),
                }
            }
            IpcResponse::Error(error) => anyhow::bail!("Service returned an error: {error}"),
            _ => anyhow::bail!("Unexpected CLI refresh-token update from service"),
        }
    }
}

#[cfg(test)]
#[path = "client_credentials/tests.rs"]
mod tests;
