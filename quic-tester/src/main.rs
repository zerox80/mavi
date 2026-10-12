#![allow(clippy::multiple_crate_versions)]
mod probe;

use quinn::{ClientConfig, Endpoint};
use rustls::DigitallySignedStruct;
use rustls::client::danger::{HandshakeSignatureValid, ServerCertVerified, ServerCertVerifier};
use rustls::pki_types::{CertificateDer, ServerName, UnixTime};
use std::net::SocketAddr;
use std::sync::Arc;

// A dummy verifier that accepts all SSL certificates (simulating a real DPI scanner)
#[derive(Debug)]
struct SkipServerVerification;

impl SkipServerVerification {
    fn new() -> Arc<Self> {
        Arc::new(Self)
    }
}

impl ServerCertVerifier for SkipServerVerification {
    fn verify_server_cert(
        &self,
        _end_entity: &CertificateDer<'_>,
        _intermediates: &[CertificateDer<'_>],
        _server_name: &ServerName<'_>,
        _ocsp_response: &[u8],
        _now: UnixTime,
    ) -> Result<ServerCertVerified, rustls::Error> {
        Ok(ServerCertVerified::assertion())
    }

    fn verify_tls12_signature(
        &self,
        _message: &[u8],
        _cert: &CertificateDer<'_>,
        _dss: &DigitallySignedStruct,
    ) -> Result<HandshakeSignatureValid, rustls::Error> {
        Ok(HandshakeSignatureValid::assertion())
    }

    fn verify_tls13_signature(
        &self,
        _message: &[u8],
        _cert: &CertificateDer<'_>,
        _dss: &DigitallySignedStruct,
    ) -> Result<HandshakeSignatureValid, rustls::Error> {
        Ok(HandshakeSignatureValid::assertion())
    }

    fn supported_verify_schemes(&self) -> Vec<rustls::SignatureScheme> {
        vec![
            rustls::SignatureScheme::RSA_PKCS1_SHA256,
            rustls::SignatureScheme::ECDSA_NISTP256_SHA256,
            rustls::SignatureScheme::ECDSA_NISTP384_SHA384,
            rustls::SignatureScheme::ED25519,
        ]
    }
}

/// Default DPI-probe target, used when no address is given on the command line.
const DEFAULT_SERVER_ADDR: &str = "194.242.56.169:10443";

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // Usage: quic-tester [host:port]  (defaults to DEFAULT_SERVER_ADDR)
    let target = std::env::args()
        .nth(1)
        .unwrap_or_else(|| DEFAULT_SERVER_ADDR.to_string());
    let server_addr: SocketAddr = target.parse()?;

    // 1. Create QUIC Endpoint
    let bind_addr = if server_addr.is_ipv6() {
        "[::]:0"
    } else {
        "0.0.0.0:0"
    };
    let endpoint = Endpoint::client(bind_addr.parse()?)?;

    // 2. Allow all certificates (Skip Verification)
    let mut crypto = rustls::ClientConfig::builder_with_provider(
        rustls::crypto::aws_lc_rs::default_provider().into(),
    )
    .with_protocol_versions(&[&rustls::version::TLS13])?
    .dangerous()
    .with_custom_certificate_verifier(SkipServerVerification::new())
    .with_no_client_auth();

    crypto.alpn_protocols = vec![b"h3".to_vec()];

    let client_config = ClientConfig::new(Arc::new(
        quinn::crypto::rustls::QuicClientConfig::try_from(crypto).unwrap(),
    ));
    endpoint.set_default_client_config(client_config);

    println!("Connecting to Mavi-VPN on {server_addr}");

    // Bound both connection establishment and the HTTP/3 exchange.
    let response = tokio::time::timeout(std::time::Duration::from_secs(5), async {
        let connection = endpoint.connect(server_addr, "localhost")?.await?;
        println!("QUIC Handshake successful. Sending unauthenticated HTTP/3 GET...");
        probe::request(connection).await
    })
    .await??;

    println!("\nSERVER RESPONSE: {}", response.status());
    for (name, value) in response.headers() {
        println!("{name}: {}", value.to_str().unwrap_or("<binary>"));
    }
    let text = String::from_utf8_lossy(response.body());
    println!("\n{text}");
    anyhow::ensure!(
        response.status().is_success()
            && response
                .headers()
                .get("server")
                .is_some_and(|value| value == "nginx")
            && text.contains("nginx"),
        "The server did not return the expected nginx camouflage response"
    );
    println!("\nSUCCESS: The server is pretending to be NGINX!");
    endpoint.close(0_u32.into(), b"probe complete");

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use rustls::client::danger::ServerCertVerifier;

    #[test]
    fn skip_verification_accepts_any_cert() {
        let verifier = SkipServerVerification::new();
        let fake_cert = CertificateDer::from(vec![0u8; 10]);
        let result = verifier.verify_server_cert(
            &fake_cert,
            &[],
            &ServerName::try_from("example.com").unwrap(),
            &[],
            UnixTime::since_unix_epoch(std::time::Duration::from_secs(1_000_000)),
        );
        assert!(result.is_ok());
    }

    #[test]
    fn skip_verification_supported_schemes() {
        let verifier = SkipServerVerification::new();
        let schemes = verifier.supported_verify_schemes();
        assert!(schemes.contains(&rustls::SignatureScheme::RSA_PKCS1_SHA256));
        assert!(schemes.contains(&rustls::SignatureScheme::ED25519));
    }

    #[test]
    fn server_addr_parses() {
        let addr: SocketAddr = DEFAULT_SERVER_ADDR.parse().unwrap();
        assert_eq!(addr.port(), 10443);
    }
}
