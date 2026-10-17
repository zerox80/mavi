//! Exercise the actual runner over loopback QUIC without creating a TUN device.

use super::*;
use crate::vpn_core::session::run_vpn;
use rustls::pki_types::{CertificateDer, PrivateKeyDer};
use sha2::{Digest, Sha256};
use shared::{control, ipc::Config, ControlMessage};
use std::sync::Mutex;

#[tokio::test]
async fn raw_quic_capacity_rejection_retries_until_stopped() {
    tokio::time::timeout(Duration::from_secs(10), async {
        let generated = rcgen::generate_simple_self_signed(vec!["localhost".into()]).unwrap();
        let cert = CertificateDer::from(generated.cert.der().to_vec());
        let pin = Sha256::digest(cert.as_ref())
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect();
        let key = PrivateKeyDer::Pkcs8(generated.signing_key.serialize_der().into());
        let mut tls = rustls::ServerConfig::builder_with_provider(
            rustls::crypto::aws_lc_rs::default_provider().into(),
        )
        .with_protocol_versions(&[&rustls::version::TLS13])
        .unwrap()
        .with_no_client_auth()
        .with_single_cert(vec![cert], key)
        .unwrap();
        tls.alpn_protocols = vec![b"mavivpn".to_vec()];
        let server_config = quinn::ServerConfig::with_crypto(Arc::new(
            quinn::crypto::rustls::QuicServerConfig::try_from(tls).unwrap(),
        ));
        let server = quinn::Endpoint::server(server_config, "[::1]:0".parse().unwrap()).unwrap();
        let config = Config {
            endpoint: server.local_addr().unwrap().to_string(),
            token: "valid-token".into(),
            cert_pin: pin,
            censorship_resistant: false,
            http3_framing: false,
            http2_framing: false,
            kc_auth: None,
            kc_url: None,
            kc_realm: None,
            kc_client_id: None,
            refresh_token: None,
            ech_config: None,
            vpn_mtu: Some(shared::DEFAULT_TUN_MTU),
        };
        let running = Arc::new(AtomicBool::new(true));
        let connected = Arc::new(AtomicBool::new(false));
        let last_error = Arc::new(Mutex::new(None));
        let assigned_ip = Arc::new(Mutex::new(None));
        let runner = run_vpn(
            config,
            running.clone(),
            connected.clone(),
            last_error.clone(),
            assigned_ip.clone(),
            Arc::new(Mutex::new("valid-token".into())),
            Arc::new(Mutex::new(None)),
            None,
        );
        let peer = async {
            // Capacity rejections are deliberately indistinguishable from bad
            // credentials. Verify a second Auth arrives without another Start.
            for _ in 0..2 {
                let connection = server.accept().await.unwrap().await.unwrap();
                let (mut send, mut recv) = connection.accept_bi().await.unwrap();
                let body = control::read_control_frame(&mut recv, control::MAX_CONTROL_FRAME_BYTES)
                    .await
                    .unwrap();
                assert!(matches!(
                    control::decode_control_message(&body).unwrap(),
                    ControlMessage::Auth { token } if token == "valid-token"
                ));
                control::write_control_frame(
                    &mut send,
                    &ControlMessage::Error {
                        message: "Unauthorized".into(),
                    },
                )
                .await
                .unwrap();
                send.finish().unwrap();
                // Keep the server connection alive until the client consumes
                // the reply and closes it, so loss cannot masquerade as retry.
                connection.closed().await;
            }
            assert!(running.load(Ordering::Relaxed));
            assert!(!connected.load(Ordering::Relaxed));
            assert!(assigned_ip.lock().unwrap().is_none());
            assert_eq!(
                last_error.lock().unwrap().as_deref(),
                Some("Server rejected connection: Unauthorized")
            );
            running.store(false, Ordering::Relaxed);
        };
        let (result, ()) = tokio::join!(runner, peer);
        result.unwrap();
    })
    .await
    .expect("generic rejection must retry automatically and Stop must end backoff");
}
