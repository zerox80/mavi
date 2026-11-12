use anyhow::{Context, Result};
use shared::enrollment::client::EnrollmentRequest;
use shared::enrollment::EnrollmentProfile;
use std::io::{self, BufRead, Write};
#[cfg(test)]
use std::path::Path;

#[cfg(test)]
use crate::client_config::save_config_to_path;
use crate::client_config::{config_path, save_config};
use crate::client_credentials::{ensure_can_start, lock_credentials};
use crate::client_ipc::{send_request_internal, sync_service_refresh_tokens};
use crate::ipc::IpcRequest;
#[cfg(test)]
use crate::secrets::SecretStore;

const USAGE: &str = "Usage: mavi-vpn-client enroll <enrollment-link> [--force]";

struct EnrollmentOptions {
    link: String,
    force: bool,
}

impl EnrollmentOptions {
    fn parse(args: &[String]) -> Result<Self> {
        let mut link = None;
        let mut force = false;
        for arg in args {
            match arg.as_str() {
                "--force" if !force => force = true,
                "--force" => anyhow::bail!("{USAGE}"),
                _ if arg.starts_with('-') || link.is_some() => anyhow::bail!("{USAGE}"),
                _ => link = Some(arg.clone()),
            }
        }
        let link = link.context(USAGE)?;
        anyhow::ensure!(!link.trim().is_empty(), "{USAGE}");
        Ok(Self { link, force })
    }
}

pub(crate) async fn run(args: &[String]) -> Result<()> {
    let options = EnrollmentOptions::parse(args)?;
    let request = EnrollmentRequest::parse(&options.link).map_err(anyhow::Error::msg)?;
    let path = config_path();
    let _credentials = lock_credentials(&path).await?;

    // Replacing the CLI profile also replaces its path-independent keyring
    // entries. Never do that while the old session can still rotate them.
    if let Ok(status) = send_request_internal(IpcRequest::Status).await {
        ensure_can_start(status)?;
        sync_service_refresh_tokens().await?;
    }

    if path.exists() && !options.force {
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
    save_config(&config)?;
    println!("Label: {label}");
    println!("Endpoint: {endpoint}");
    println!(
        "Enrollment saved successfully. Run 'mavi-vpn-client start' to sign in with Keycloak."
    );
    Ok(())
}

fn enrollment_config(profile: EnrollmentProfile) -> Result<crate::ipc::Config> {
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
    store: &dyn SecretStore,
) -> Result<()> {
    save_config_to_path(path, &enrollment_config(profile)?, store)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::secrets::tests::MemorySecretStore;
    use crate::secrets::{config_refresh_token_account, config_token_account};
    use shared::enrollment::{EnrollmentKeycloak, EnrollmentTransport, ENROLLMENT_SCHEMA_V1};

    fn profile() -> EnrollmentProfile {
        EnrollmentProfile {
            schema: ENROLLMENT_SCHEMA_V1.into(),
            device_id: "panel-device-id".into(),
            label: "  Enrolled laptop  ".into(),
            endpoint: "vpn.example.com:10443".into(),
            cert_pin: "ab".repeat(32),
            transport: EnrollmentTransport::Http3CensorshipResistant,
            ech_config: Some("00ff".into()),
            vpn_mtu: shared::DEFAULT_TUN_MTU,
            keycloak: EnrollmentKeycloak {
                url: "https://auth.example.com/".into(),
                realm: "mavi-vpn".into(),
                client_id: "mavi-client".into(),
            },
        }
    }

    #[test]
    fn arguments_accept_force_without_echoing_the_ticket() {
        let options = EnrollmentOptions::parse(&["--force".into(), "secret-link".into()]).unwrap();
        assert!(options.force);
        assert_eq!(options.link, "secret-link");
        for args in [
            vec![],
            vec!["one".into(), "two".into()],
            vec!["--force".into()],
        ] {
            let error = EnrollmentOptions::parse(&args)
                .map(|_| ())
                .unwrap_err()
                .to_string();
            assert!(error.contains("Usage:"));
            assert!(!error.contains("one") && !error.contains("two"));
        }
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
    fn enrolled_profile_is_saved_without_reusing_old_keyring_credentials() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("config.json");
        let store = MemorySecretStore::default();
        store
            .set_secret(config_token_account(), "old-access")
            .unwrap();
        store
            .set_secret(config_refresh_token_account(), "old-refresh")
            .unwrap();

        save_enrollment_for_test(&path, profile(), &store).unwrap();

        let loaded = crate::client_config::load_config_from_path(&path, &store)
            .unwrap()
            .unwrap();
        assert_eq!(loaded.endpoint, "vpn.example.com:10443");
        assert_eq!(loaded.kc_url.as_deref(), Some("https://auth.example.com"));
        assert_eq!(loaded.vpn_mtu, Some(1280));
        assert!(loaded.token.is_empty());
        assert!(loaded.refresh_token.is_none());
        assert!(store.secret(config_token_account()).is_none());
        assert!(store.secret(config_refresh_token_account()).is_none());
    }
}
