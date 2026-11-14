#![cfg(feature = "oauth-client")]

use shared::enrollment::client::EnrollmentRequest;

/// A separate integration-test process starts without an installed TLS
/// provider, just like pressing Review before the first Keycloak login.
#[tokio::test]
async fn first_enrollment_request_reports_network_failure_without_panicking() {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let peer = tokio::spawn(async move {
        let (connection, _) = listener.accept().await.unwrap();
        drop(connection); // Simulate a server that cannot complete TLS.
    });
    let link = format!("https://{address}/enroll/{}", "A".repeat(43));
    let error = EnrollmentRequest::parse(&link)
        .unwrap()
        .redeem()
        .await
        .unwrap_err();
    assert_eq!(error, "Could not reach the enrollment server");
    peer.await.unwrap();
}
