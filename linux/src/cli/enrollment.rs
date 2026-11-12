use anyhow::{Context, Result};
use shared::enrollment::client::EnrollmentRequest;
use shared::enrollment::EnrollmentProfile;
use std::io::{self, BufRead, Write};
#[cfg(test)]
use std::path::Path;
use std::path::PathBuf;

use super::{config, daemon, daemon_credentials, secrets};

const USAGE: &str = "Usage: mavi-vpn enroll <enrollment-link> [-c|--config <file>] [--force]";

struct EnrollmentOptions {
    link: String,
    force: bool,
}

impl EnrollmentOptions {
    fn parse(args: &[String]) -> Result<Self> {
        let mut link = None;
        let mut force = false;
        let mut index = 0;
        while index < args.len() {
            match args[index].as_str() {
                "--force" if !force => {
                    force = true;
                    index += 1;
                }
                "-c" | "--config" => {
                    anyhow::ensure!(
                        args.get(index + 1)
                            .is_some_and(|path| { !path.is_empty() && !path.starts_with('-') }),
                        "-c / --config requires a file path"
                    );
                    index += 2;
                }
                _ if args[index].starts_with('-') || link.is_some() => {
                    anyhow::bail!("{USAGE}")
                }
                _ => {
                    link = Some(args[index].clone());
                    index += 1;
                }
            }
        }
        let link = link.context(USAGE)?;
        anyhow::ensure!(!link.trim().is_empty(), "{USAGE}");
        Ok(Self { link, force })
    }
}

pub(super) fn run(args: &[String]) -> Result<()> {
    let options = EnrollmentOptions::parse(args)?;
    let request = EnrollmentRequest::parse(&options.link).map_err(anyhow::Error::msg)?;
    let path = super::config_path(args)?.unwrap_or_else(config::default_config_path);
    let runtime = tokio::runtime::Runtime::new()?;
    runtime.block_on(redeem_and_save(request, options.force, path))
}

async fn redeem_and_save(request: EnrollmentRequest, force: bool, path: PathBuf) -> Result<()> {
    let _credentials = daemon_credentials::lock(&path).await?;
    // The daemon and direct CLI use path-scoped keyring entries. Drain stopped
    // daemon rotations and reject an active session before replacing them.
    if let Ok(status) = daemon::send_request(shared::ipc::IpcRequest::Status).await {
        daemon_credentials::ensure_can_start(status)?;
        daemon_credentials::sync_refresh_tokens(
            &path,
            &secrets::KeyringSecretStore,
            daemon::send_request,
        )
        .await?;
    }

    if path.exists() && !force {
        let stdin = io::stdin();
        let mut input = stdin.lock();
        let mut output = io::stdout();
        if !confirm_replacement(&mut input, &mut output)? {
            println!("Enrollment cancelled; the saved configuration is unchanged.");
            return Ok(());
        }
    }

    let profile = request.redeem().await.map_err(anyhow::Error::msg)?;
    let label = profile.label.trim().to_string();
    let config = enrollment_config(profile)?;
    let endpoint = config.endpoint.clone();
    config::save_config(&config, &path, &secrets::KeyringSecretStore)?;
    println!("Label: {label}");
    println!("Endpoint: {endpoint}");
    println!("Enrollment saved successfully. Run 'mavi-vpn start' to sign in with Keycloak.");
    Ok(())
}

fn enrollment_config(profile: EnrollmentProfile) -> Result<shared::ipc::Config> {
    profile.into_config().map_err(anyhow::Error::msg)
}

fn confirm_replacement(input: &mut impl BufRead, output: &mut impl Write) -> Result<bool> {
    write!(
        output,
        "A saved configuration exists. Replace it with the enrolled profile? [y/N]: "
    )?;
    output.flush()?;
    let mut answer = String::new();
    input.read_line(&mut answer)?;
    Ok(matches!(
        answer.trim().to_ascii_lowercase().as_str(),
        "y" | "yes"
    ))
}

#[cfg(test)]
fn save_enrollment_for_test(
    path: &Path,
    profile: EnrollmentProfile,
    store: &dyn secrets::SecretStore,
) -> Result<()> {
    config::save_config(&enrollment_config(profile)?, path, store)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::cli::secrets::tests::{sample_config, MemorySecretStore};
    use shared::enrollment::{EnrollmentKeycloak, EnrollmentTransport, ENROLLMENT_SCHEMA_V1};

    fn profile() -> EnrollmentProfile {
        EnrollmentProfile {
            schema: ENROLLMENT_SCHEMA_V1.into(),
            device_id: "panel-device-id".into(),
            label: "Enrolled laptop".into(),
            endpoint: "vpn.example.com:10443".into(),
            cert_pin: "ab".repeat(32),
            transport: EnrollmentTransport::Http2,
            ech_config: None,
            vpn_mtu: shared::DEFAULT_TUN_MTU,
            keycloak: EnrollmentKeycloak {
                url: "https://auth.example.com/".into(),
                realm: "mavi-vpn".into(),
                client_id: "mavi-client".into(),
            },
        }
    }

    #[test]
    fn arguments_accept_custom_config_and_force() {
        let args = vec![
            "enrolled-link".into(),
            "--config".into(),
            "custom.json".into(),
            "--force".into(),
        ];
        let options = EnrollmentOptions::parse(&args).unwrap();
        assert_eq!(options.link, "enrolled-link");
        assert!(options.force);
        assert_eq!(
            super::super::config_path(&args).unwrap(),
            Some(PathBuf::from("custom.json"))
        );
        assert!(EnrollmentOptions::parse(&["--config".into()]).is_err());
        assert!(EnrollmentOptions::parse(&[]).is_err());
    }

    #[test]
    fn replacement_requires_an_explicit_affirmative_answer() {
        for (answer, expected) in [
            ("y\n", true),
            ("YES\n", true),
            ("\n", false),
            ("n\n", false),
        ] {
            let mut input = io::Cursor::new(answer);
            let mut output = Vec::new();
            assert_eq!(
                confirm_replacement(&mut input, &mut output).unwrap(),
                expected
            );
        }
    }

    #[test]
    fn enrolled_profile_replaces_old_secrets_and_uses_atomic_config_save() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("config.json");
        let store = MemorySecretStore::default();
        config::save_config(&sample_config(true), &path, &store).unwrap();

        save_enrollment_for_test(&path, profile(), &store).unwrap();

        let loaded = config::load_config(&path, &store).unwrap();
        assert!(loaded.http2_framing);
        assert!(!loaded.http3_framing);
        assert_eq!(loaded.vpn_mtu, Some(1280));
        assert!(loaded.token.is_empty());
        assert!(loaded.refresh_token.is_none());
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(
                std::fs::metadata(path).unwrap().permissions().mode() & 0o777,
                0o600
            );
        }
    }
}
