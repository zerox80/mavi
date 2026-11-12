use crate::storage::{import_enrollment_to_dir, Prefs};
use shared::enrollment::client::EnrollmentRequest;
use shared::enrollment::EnrollmentProfile;
use tauri::{AppHandle, Manager};

/// Redeems the one-time ticket without changing saved profiles or the active
/// VPN session. The frontend must show the returned profile for confirmation;
/// cancelling simply discards this secret-free response.
#[tauri::command]
pub(crate) async fn redeem_enrollment(link: String) -> Result<EnrollmentProfile, String> {
    let request = EnrollmentRequest::parse(&link)?;
    request.redeem().await
}

/// Imports an already redeemed profile. Revalidation is required because the
/// confirmation round-trip crosses the untrusted frontend boundary.
#[tauri::command]
pub(crate) async fn import_enrollment(
    app: AppHandle,
    profile: EnrollmentProfile,
) -> Result<Prefs, String> {
    let config_dir = app.path().app_config_dir().map_err(|e| e.to_string())?;
    import_enrollment_to_dir(&config_dir, profile)
}
