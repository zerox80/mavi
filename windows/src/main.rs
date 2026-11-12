#![allow(clippy::multiple_crate_versions)]
use anyhow::Result;
use std::process::ExitCode;

mod client_config;
mod client_credentials;
mod client_enrollment;
mod client_ipc;
mod client_prompt;
mod ipc;
mod oauth;
mod secrets;

use client_ipc::send_request;
use client_prompt::{interactive_mode, read_line, start_vpn};
use ipc::IpcRequest;

#[tokio::main]
async fn main() -> ExitCode {
    println!();
    println!("========================================");
    println!("         Mavi VPN - Windows");
    println!("========================================");
    println!();

    let args: Vec<String> = std::env::args().skip(1).collect();

    let result = if args.is_empty() {
        interactive_mode().await
    } else {
        dispatch_cli(&args).await
    };

    let exit_code = match result {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("\n[ERROR] Error: {e:#}");
            ExitCode::FAILURE
        }
    };

    if args.is_empty() {
        println!("\nPress Enter to exit...");
        let _ = read_line();
    }
    exit_code
}

async fn dispatch_cli(args: &[String]) -> Result<()> {
    let cmd = args[0].to_lowercase();
    match cmd.as_str() {
        "start" => start_vpn().await,
        "stop" => send_request(IpcRequest::Stop).await,
        "status" => send_request(IpcRequest::Status).await,
        "repair" => send_request(IpcRequest::RepairNetwork).await,
        "enroll" => client_enrollment::run(&args[1..]).await,
        "help" | "--help" | "-h" => {
            print_help();
            Ok(())
        }
        _ => {
            anyhow::bail!("Unknown command: {cmd}\nUsage: mavi-vpn-client [start|stop|status|repair]\n       mavi-vpn-client enroll <enrollment-link> [--force]")
        }
    }
}

fn print_help() {
    println!("Usage: mavi-vpn-client [COMMAND]");
    println!();
    println!("Commands:");
    println!("  start                         Start the VPN with Keycloak or a preshared key");
    println!("  stop                          Stop the VPN");
    println!("  status                        Show VPN status");
    println!("  repair                        Repair stale network settings");
    println!("  enroll <enrollment-link>      Import a one-time webpanel enrollment");
    println!();
    println!("Enrollment options:");
    println!("  --force                       Replace an existing configuration without prompting");
}

#[cfg(test)]
mod main_tests;
