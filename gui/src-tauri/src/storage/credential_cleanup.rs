use super::{connection_refresh_token_account, connection_token_account, Prefs, SecretStore};
use std::collections::BTreeSet;
use std::path::Path;

/// Disk-only metadata: the frontend cannot discard or inject cleanup work.
#[derive(Default, serde::Deserialize, serde::Serialize)]
pub(super) struct StoredPrefs {
    #[serde(flatten)]
    pub(super) prefs: Prefs,
    #[serde(default, skip_serializing_if = "BTreeSet::is_empty")]
    pending_credential_deletions: BTreeSet<String>,
}

impl StoredPrefs {
    pub(super) fn for_save(prefs: Prefs, previous: Self) -> Self {
        let mut pending = previous.pending_credential_deletions;
        for old in previous.prefs.connections {
            pending.insert(connection_token_account(&old.id));
            pending.insert(connection_refresh_token_account(&old.id));
            pending.extend(old.refresh_account());
        }
        let mut stored = Self {
            prefs,
            pending_credential_deletions: pending,
        };
        stored.protect_current_accounts();
        stored
    }

    fn protect_current_accounts(&mut self) {
        for conn in &self.prefs.connections {
            self.pending_credential_deletions
                .remove(&connection_token_account(&conn.id));
            self.pending_credential_deletions
                .remove(&connection_refresh_token_account(&conn.id));
            if let Some(account) = conn.refresh_account() {
                self.pending_credential_deletions.remove(&account);
            }
        }
    }

    pub(super) fn cleanup(
        &mut self,
        path: &Path,
        store: &dyn SecretStore,
        write: &mut impl FnMut(&Path, &str) -> Result<(), String>,
    ) {
        let pending_before = self.pending_credential_deletions.len();
        self.protect_current_accounts();
        self.pending_credential_deletions.retain(|account| {
            if let Err(error) = store.delete_secret(account) {
                tracing::warn!(%error, "Could not remove an unused saved credential; will retry");
                true
            } else {
                false
            }
        });
        if self.pending_credential_deletions.len() != pending_before {
            // A failed checkpoint leaves the committed queue intact. Retrying an
            // already deleted account is safe, and must not undo the profile save.
            let result = serde_json::to_string_pretty(self)
                .map_err(|e| e.to_string())
                .and_then(|content| write(path, &content));
            if let Err(error) = result {
                tracing::warn!(%error, "Could not checkpoint saved credential cleanup; will retry");
            }
        }
    }
}
