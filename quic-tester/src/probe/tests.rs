use super::*;
use bytes::Bytes;
use std::sync::Arc;
use std::time::Duration;

async fn probe_server(body: Bytes) -> Result<http::Response<Vec<u8>>> {
    let generated = rcgen::generate_simple_self_signed(vec!["localhost".to_owned()])?;
    let cert = generated.cert.der().clone();
    let key = rustls::pki_types::PrivateKeyDer::Pkcs8(generated.signing_key.serialize_der().into());
    let provider = Arc::new(rustls::crypto::aws_lc_rs::default_provider());
    let mut tls = rustls::ServerConfig::builder_with_provider(provider.clone())
        .with_protocol_versions(&[&rustls::version::TLS13])?
        .with_no_client_auth()
        .with_single_cert(vec![cert.clone()], key)?;
    tls.alpn_protocols = vec![b"h3".to_vec()];
    let server = quinn::Endpoint::server(
        quinn::ServerConfig::with_crypto(Arc::new(
            quinn::crypto::rustls::QuicServerConfig::try_from(tls)?,
        )),
        "127.0.0.1:0".parse()?,
    )?;
    let mut roots = rustls::RootCertStore::empty();
    roots.add(cert)?;
    let mut tls = rustls::ClientConfig::builder_with_provider(provider)
        .with_protocol_versions(&[&rustls::version::TLS13])?
        .with_root_certificates(roots)
        .with_no_client_auth();
    tls.alpn_protocols = vec![b"h3".to_vec()];
    let client = quinn::Endpoint::client("127.0.0.1:0".parse()?)?;
    client.set_default_client_config(quinn::ClientConfig::new(Arc::new(
        quinn::crypto::rustls::QuicClientConfig::try_from(tls)?,
    )));

    let serve = async {
        let connection = server.accept().await.unwrap().await?;
        let mut h3 = h3::server::Connection::new(h3_quinn::Connection::new(connection)).await?;
        let resolver = h3.accept().await?.unwrap();
        let (request, mut stream) = resolver.resolve_request().await?;
        assert_eq!(request.method(), http::Method::GET);
        assert_eq!(request.uri().path(), "/");
        assert!(!request.headers().contains_key("authorization"));
        stream
            .send_response(
                http::Response::builder()
                    .header("server", "nginx")
                    .body(())?,
            )
            .await?;
        stream.send_data(body).await?;
        stream.finish().await?;
        // Keep the server alive until the client has consumed the complete body.
        std::future::pending::<Result<()>>().await
    };
    let probe = async {
        let connection = client.connect(server.local_addr()?, "localhost")?.await?;
        request(connection).await
    };
    let result = tokio::time::timeout(Duration::from_secs(5), async {
        tokio::select! {
            result = probe => result,
            result = serve => {
                result?;
                anyhow::bail!("server exited before probe completed")
            }
        }
    })
    .await?;
    client.close(0_u32.into(), b"test complete");
    server.close(0_u32.into(), b"test complete");
    result
}

#[tokio::test]
async fn real_http3_probe_receives_nginx_headers_and_body() {
    let response = probe_server(Bytes::from_static(b"<h1>Welcome to nginx!</h1>"))
        .await
        .unwrap();
    assert_eq!(response.status(), http::StatusCode::OK);
    assert_eq!(response.headers()["server"], "nginx");
    assert_eq!(response.body(), b"<h1>Welcome to nginx!</h1>");
}

#[tokio::test]
async fn oversized_response_is_rejected() {
    let error = probe_server(Bytes::from(vec![b'x'; MAX_RESPONSE_BYTES + 1]))
        .await
        .unwrap_err();
    assert!(error.to_string().contains("exceeds"), "{error:#}");
}
