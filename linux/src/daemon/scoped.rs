use super::{DaemonState, IpcRequest, IpcResponse, Ordering};

pub(super) fn resolve(
    request: IpcRequest,
    state: &DaemonState,
) -> Result<(IpcRequest, Option<String>), IpcResponse> {
    let (session_id, request) = match request {
        IpcRequest::StartScoped { session_id, config } => {
            if session_id.is_empty() || session_id.len() > 128 {
                return Err(IpcResponse::Error("Invalid session ID".into()));
            }
            return Ok((IpcRequest::Start(config), Some(session_id)));
        }
        IpcRequest::SessionStatus { session_id } => (session_id, IpcRequest::Status),
        IpcRequest::UpdateSessionToken { session_id, token } => {
            (session_id, IpcRequest::UpdateToken { token })
        }
        IpcRequest::StopSession { session_id } => (session_id, IpcRequest::Stop),
        request => return Ok((request, None)),
    };
    if state.session_id.as_deref() != Some(session_id.as_str())
        || !state.vpn_running.load(Ordering::SeqCst)
    {
        return Err(IpcResponse::Error("VPN session is no longer active".into()));
    }
    Ok((request, None))
}

#[cfg(test)]
mod tests;
