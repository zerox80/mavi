use shared::ipc::IpcResponse;
use std::collections::HashMap;
use std::sync::{Arc, Mutex};

/// Retain rotations across Stop until the owning CLI saves and acknowledges
/// them. The root daemon cannot access the user's Secret Service keyring.
#[derive(Default)]
pub(super) struct RefreshUpdates(Arc<Mutex<HashMap<String, String>>>);

impl RefreshUpdates {
    pub(super) fn publisher(&self, id: String) -> crate::vpn_core::TokenPersistence {
        let updates = self.0.clone();
        Box::new(move |tokens| {
            if let Some(refresh) = tokens.refresh_token.as_ref().filter(|r| !r.is_empty()) {
                updates
                    .lock()
                    .unwrap_or_else(std::sync::PoisonError::into_inner)
                    .insert(id.clone(), refresh.clone());
            }
            Ok(())
        })
    }

    pub(super) fn get(&self, id: &str) -> IpcResponse {
        let updates = self
            .0
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let refresh = updates.get(id).cloned();
        IpcResponse::RefreshTokenUpdate {
            connection_id: refresh.as_ref().map(|_| id.to_string()),
            refresh_token: refresh,
        }
    }

    pub(super) fn acknowledge(&self, id: &str, refresh: &str) {
        let mut updates = self
            .0
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        if updates.get(id).is_some_and(|pending| pending == refresh) {
            updates.remove(id);
        }
    }
}
