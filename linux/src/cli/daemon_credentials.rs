use super::secrets::{SecretAccounts, SecretStore};
use anyhow::{Context, Result};
use shared::ipc::{Config, IpcRequest, IpcResponse, KeycloakRuntimeAuth, VpnState};
use std::fs::{File, OpenOptions, TryLockError};
use std::future::Future;
use std::path::Path;
use std::time::Duration;

pub(super) async fn lock(path: &Path) -> Result<File> {
    let path = std::path::absolute(path)?;
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(path.with_extension("credentials.lock"))?;
    loop {
        match file.try_lock() {
            Ok(()) => return Ok(file),
            Err(TryLockError::WouldBlock) => tokio::time::sleep(Duration::from_millis(50)).await,
            Err(TryLockError::Error(error)) => return Err(error.into()),
        }
    }
}

pub(super) fn ensure_can_start(response: IpcResponse) -> Result<()> {
    match response {
        IpcResponse::Status {
            state: VpnState::Stopped | VpnState::Failed,
            ..
        } => Ok(()),
        IpcResponse::Status { .. } => anyhow::bail!("VPN is already running or stopping"),
        IpcResponse::Error(error) => anyhow::bail!("Daemon returned an error: {error}"),
        _ => anyhow::bail!("Unexpected status response from daemon"),
    }
}

pub(super) fn start_request(config: Config, path: &Path) -> IpcRequest {
    if config.kc_auth.unwrap_or(false) {
        if let Some(refresh) = config.refresh_token.as_ref().filter(|r| !r.is_empty()) {
            let keycloak = KeycloakRuntimeAuth {
                connection_id: SecretAccounts::for_config(path).refresh_token,
                kc_url: config.kc_url.clone().unwrap_or_default(),
                realm: config.kc_realm.clone().unwrap_or_else(|| "mavi-vpn".into()),
                client_id: config
                    .kc_client_id
                    .clone()
                    .unwrap_or_else(|| "mavi-client".into()),
                refresh_token: refresh.clone(),
            };
            return IpcRequest::StartWithKeycloak { config, keycloak };
        }
    }
    IpcRequest::Start(config)
}

pub(super) async fn sync_refresh_tokens<R, F>(
    path: &Path,
    store: &dyn SecretStore,
    mut request: R,
) -> Result<()>
where
    R: FnMut(IpcRequest) -> F,
    F: Future<Output = Result<IpcResponse>>,
{
    let account = SecretAccounts::for_config(path).refresh_token;
    loop {
        match request(IpcRequest::TakeRefreshTokenUpdateFor {
            connection_id: account.clone(),
        })
        .await?
        {
            IpcResponse::RefreshTokenUpdate {
                connection_id: None,
                refresh_token: None,
            } => return Ok(()),
            IpcResponse::RefreshTokenUpdate {
                connection_id: Some(id),
                refresh_token: Some(refresh),
            } if id == account && !refresh.is_empty() => {
                store
                    .set_secret(&account, &refresh)
                    .context("Could not save the daemon's rotated CLI refresh token")?;
                match request(IpcRequest::AcknowledgeRefreshTokenUpdate {
                    connection_id: id,
                    refresh_token: refresh,
                })
                .await?
                {
                    IpcResponse::Ok => {}
                    IpcResponse::Error(error) => anyhow::bail!("Daemon returned an error: {error}"),
                    _ => anyhow::bail!("Unexpected refresh-token acknowledgement"),
                }
            }
            IpcResponse::Error(error) => anyhow::bail!("Daemon returned an error: {error}"),
            _ => anyhow::bail!("Unexpected CLI refresh-token update"),
        }
    }
}

#[cfg(test)]
mod tests;
