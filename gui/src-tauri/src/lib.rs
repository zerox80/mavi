#![allow(clippy::multiple_crate_versions)]
//! # Mavi VPN GUI - Tauri Backend
//!
//! Communicates with the VPN daemon/service via OS-native local IPC.
//! Exposes Tauri commands for the web frontend and manages the system tray.

mod commands;
mod ipc;
mod logging;
mod oauth;
mod secret_store;
mod service_setup;
mod storage;
mod tray;

use commands::{
    import_enrollment, load_config, load_prefs, redeem_enrollment, save_config, save_prefs,
    vpn_connect, vpn_disconnect, vpn_repair_network, vpn_status,
};
use tray::{setup_tray, start_status_poller};

#[cfg_attr(mobile, tauri::mobile_entry_point)]
/// Entry point for the Mavi VPN GUI application.
///
/// # Panics
/// Panics if the Tauri application fails to start.
pub fn run() {
    if let Some(path) = logging::init_gui_logging() {
        tracing::info!("GUI log file: {}", path.display());
    }

    let builder = tauri::Builder::default();
    #[cfg(desktop)]
    let builder = builder.plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
        use tauri::Manager;
        if let Some(window) = app.get_webview_window("main") {
            let _ = window.show();
            let _ = window.unminimize();
            let _ = window.set_focus();
        }
    }));
    builder
        .plugin(tauri_plugin_deep_link::init())
        .manage(commands::TokenRefreshHandle::default())
        .manage(commands::ConnectionLifecycle::default())
        .invoke_handler(tauri::generate_handler![
            vpn_connect,
            vpn_disconnect,
            vpn_repair_network,
            vpn_status,
            redeem_enrollment,
            import_enrollment,
            save_config,
            load_config,
            load_prefs,
            save_prefs,
            service_setup::setup_vpn_service,
        ])
        .setup(|app| {
            #[cfg(any(target_os = "linux", all(debug_assertions, target_os = "windows")))]
            {
                use tauri_plugin_deep_link::DeepLinkExt;
                if let Err(error) = app.deep_link().register_all() {
                    tracing::warn!("Could not register enrollment links: {error}");
                }
            }
            setup_tray(app)?;
            start_status_poller(app.handle().clone());
            #[cfg(target_os = "windows")]
            commands::start_service_refresh_token_sync(app.handle());
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running Mavi VPN GUI");
}
