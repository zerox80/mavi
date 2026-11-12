use super::*;
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use jsonwebtoken::jwk::{
    AlgorithmParameters, CommonParameters, Jwk, KeyAlgorithm, RSAKeyParameters, RSAKeyType,
};
use jsonwebtoken::Header;
use rcgen::{PublicKeyData, SigningKey};
use serde_json::{json, Value};
use std::time::{SystemTime, UNIX_EPOCH};

struct TestKey {
    pair: rcgen::KeyPair,
    jwks: JwkSet,
}

impl TestKey {
    fn new() -> Self {
        let pair = rcgen::KeyPair::generate_for(&rcgen::PKCS_RSA_SHA256).unwrap();
        let (modulus, exponent) = rsa_components_from_der(pair.der_bytes()).unwrap();
        let jwk = Jwk {
            common: CommonParameters {
                key_id: Some("test-key".into()),
                key_algorithm: Some(KeyAlgorithm::RS256),
                ..Default::default()
            },
            algorithm: AlgorithmParameters::RSA(RSAKeyParameters {
                key_type: RSAKeyType::RSA,
                n: URL_SAFE_NO_PAD.encode(strip_leading_zeroes(modulus)),
                e: URL_SAFE_NO_PAD.encode(strip_leading_zeroes(exponent)),
            }),
        };
        Self {
            pair,
            jwks: JwkSet { keys: vec![jwk] },
        }
    }

    fn sign(&self, claims: Value) -> String {
        let mut header = Header::new(Algorithm::RS256);
        header.kid = Some("test-key".into());
        let message = [
            URL_SAFE_NO_PAD.encode(serde_json::to_vec(&header).unwrap()),
            URL_SAFE_NO_PAD.encode(serde_json::to_vec(&claims).unwrap()),
        ]
        .join(".");
        let signature = self.pair.sign(message.as_bytes()).unwrap();
        format!("{message}.{}", URL_SAFE_NO_PAD.encode(signature))
    }
}

fn settings() -> KeycloakSettings {
    KeycloakSettings {
        url: "https://auth.example".into(),
        realm: "mavi-vpn".into(),
        client_id: "mavi-panel".into(),
        client_secret: "not-used-in-validation".into(),
    }
}

fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs()
}

fn id_claims() -> Value {
    json!({
        "iss": "https://auth.example/realms/mavi-vpn",
        "aud": "mavi-panel",
        "sub": "user-123",
        "preferred_username": "alice",
        "name": "Alice Example",
        "nonce": "expected-nonce",
        "iat": now(),
        "exp": now() + 300
    })
}

fn access_claims(roles: Value) -> Value {
    json!({
        "iss": "https://auth.example/realms/mavi-vpn",
        "aud": "account",
        "azp": "mavi-panel",
        "sub": "user-123",
        "typ": "Bearer",
        "realm_access": {"roles": roles},
        "iat": now(),
        "exp": now() + 300
    })
}

#[test]
fn rs256_id_and_access_tokens_produce_an_authorized_identity() {
    let key = TestKey::new();
    let id_token = key.sign(id_claims());
    let id =
        verify_id_token_with_keys(&id_token, &key.jwks, &settings(), "expected-nonce").unwrap();
    let access_token = key.sign(access_claims(json!([
        "vpn-user",
        "panel-user",
        "panel-admin"
    ])));
    let identity =
        verify_access_token_with_keys(&access_token, &key.jwks, &settings(), &id).unwrap();
    assert_eq!(identity.subject, "user-123");
    assert_eq!(identity.username, "alice");
    assert_eq!(identity.display_name, "Alice Example");
    assert_eq!(identity.roles, vec!["panel-admin", "panel-user"]);
}

#[test]
fn id_token_requires_the_client_audience_and_login_nonce() {
    let key = TestKey::new();
    let id_token = key.sign(id_claims());
    assert!(verify_id_token_with_keys(&id_token, &key.jwks, &settings(), "wrong-nonce").is_err());

    let mut wrong_audience = id_claims();
    wrong_audience["aud"] = json!("another-client");
    let token = key.sign(wrong_audience);
    assert!(verify_id_token_with_keys(&token, &key.jwks, &settings(), "expected-nonce").is_err());
}

#[test]
fn access_token_controls_panel_roles_and_subject_binding() {
    let key = TestKey::new();
    let id = IdIdentity {
        subject: "user-123".into(),
        username: "alice".into(),
        display_name: "Alice".into(),
    };
    let no_access = key.sign(access_claims(json!(["vpn-user"])));
    assert!(matches!(
        verify_access_token_with_keys(&no_access, &key.jwks, &settings(), &id),
        Err(OidcError::AccessDenied)
    ));

    let mut wrong_subject = access_claims(json!(["panel-admin"]));
    wrong_subject["sub"] = json!("another-user");
    let token = key.sign(wrong_subject);
    assert!(verify_access_token_with_keys(&token, &key.jwks, &settings(), &id).is_err());
}

#[test]
fn expired_or_modified_tokens_are_rejected() {
    let key = TestKey::new();
    let mut expired = id_claims();
    expired["exp"] = json!(now() - 120);
    let token = key.sign(expired);
    assert!(verify_id_token_with_keys(&token, &key.jwks, &settings(), "expected-nonce").is_err());

    let token = key.sign(id_claims());
    let mut modified = token.into_bytes();
    let index = modified.iter().position(|byte| *byte == b'.').unwrap() + 4;
    modified[index] = if modified[index] == b'a' { b'b' } else { b'a' };
    let modified = String::from_utf8(modified).unwrap();
    assert!(
        verify_id_token_with_keys(&modified, &key.jwks, &settings(), "expected-nonce").is_err()
    );
}

fn strip_leading_zeroes(bytes: &[u8]) -> &[u8] {
    let first = bytes.iter().position(|byte| *byte != 0).unwrap_or(0);
    &bytes[first..]
}

fn rsa_components_from_der(der: &[u8]) -> Option<(&[u8], &[u8])> {
    let (tag, body, rest) = der_read_tlv(der)?;
    if tag != 0x30 || !rest.is_empty() {
        return None;
    }
    if let Some(components) = rsa_components_from_sequence(body) {
        return Some(components);
    }
    let (_, _, after_algorithm) = der_read_tlv(body)?;
    let (tag, bit_string, _) = der_read_tlv(after_algorithm)?;
    if tag != 0x03 || bit_string.first().copied()? != 0 {
        return None;
    }
    let (tag, body, rest) = der_read_tlv(&bit_string[1..])?;
    if tag != 0x30 || !rest.is_empty() {
        return None;
    }
    rsa_components_from_sequence(body)
}

fn rsa_components_from_sequence(sequence: &[u8]) -> Option<(&[u8], &[u8])> {
    let (n_tag, modulus, after_n) = der_read_tlv(sequence)?;
    let (e_tag, exponent, after_e) = der_read_tlv(after_n)?;
    if n_tag == 0x02 && e_tag == 0x02 && after_e.is_empty() {
        Some((modulus, exponent))
    } else {
        None
    }
}

fn der_read_tlv(input: &[u8]) -> Option<(u8, &[u8], &[u8])> {
    let (&tag, input) = input.split_first()?;
    let (&length, input) = input.split_first()?;
    let (length, input) = if length & 0x80 == 0 {
        (usize::from(length), input)
    } else {
        let octets = usize::from(length & 0x7f);
        if octets == 0 || octets > std::mem::size_of::<usize>() {
            return None;
        }
        let (bytes, input) = input.split_at_checked(octets)?;
        let length = bytes
            .iter()
            .fold(0_usize, |value, byte| (value << 8) | usize::from(*byte));
        (length, input)
    };
    let (body, rest) = input.split_at_checked(length)?;
    Some((tag, body, rest))
}
