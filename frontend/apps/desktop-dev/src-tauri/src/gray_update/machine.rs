//! Machine-code approval uses a local key and an independent managed bearer.

use super::{
    private_client, read_token, record_exists, response_bytes, rollout_path, save_token, url_safe,
    CheckOutcome, EnrollReply, Managed, RolloutCheck, API, MAX_JSON, POLICY_ERROR, STORE_ERROR,
};
use reqwest::{header::HeaderMap, StatusCode};
use ring::signature::{Ed25519KeyPair, KeyPair};
use serde::Serialize;
use serde_json::Value;
use std::{
    fs::{self, File, OpenOptions},
    path::{Path, PathBuf},
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use tauri::AppHandle;

pub(super) const SEED_FILE: &str = "machine-seed";
pub(super) const TOKEN_FILE: &str = "machine-token";
const CLAIM_CONTEXT: &str = "VortoOne desktop rollout machine claim v1";
const CLAIM_STATUS_HEADER: &str = "x-vortoone-machine-claim";

#[cfg(target_os = "macos")]
const MACHINE_PLATFORM: &str = "mac";
#[cfg(target_os = "windows")]
const MACHINE_PLATFORM: &str = "win";
#[cfg(target_os = "linux")]
const MACHINE_PLATFORM: &str = "linux";

struct Paths {
    seed: PathBuf,
    token: PathBuf,
}

impl Paths {
    fn for_app(app: &AppHandle) -> Result<Self, &'static str> {
        Ok(Self {
            seed: rollout_path(app, SEED_FILE)?,
            token: rollout_path(app, TOKEN_FILE)?,
        })
    }

    fn exists(&self) -> Result<bool, &'static str> {
        Ok(record_exists(&self.seed)? || record_exists(&self.token)?)
    }

    fn lock(&self) -> Result<File, &'static str> {
        let directory = self.seed.parent().ok_or(STORE_ERROR)?;
        #[cfg(unix)]
        crate::connector_secrets::private_dir(directory).map_err(|_| STORE_ERROR)?;
        #[cfg(target_os = "windows")]
        fs::create_dir_all(directory).map_err(|_| STORE_ERROR)?;
        let mut options = OpenOptions::new();
        options.read(true).write(true).create(true).truncate(false);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600).custom_flags(libc::O_NOFOLLOW);
        }
        let lock = options
            .open(directory.join("machine.lock"))
            .map_err(|_| STORE_ERROR)?;
        lock.try_lock().map_err(|_| STORE_ERROR)?;
        Ok(lock)
    }
}

#[cfg(target_os = "macos")]
pub(super) fn has_state(app: &AppHandle) -> Result<bool, &'static str> {
    Paths::for_app(app)?.exists()
}

fn hex(bytes: &[u8]) -> String {
    const DIGITS: &[u8; 16] = b"0123456789abcdef";
    let mut text = String::with_capacity(bytes.len() * 2);
    for &byte in bytes {
        text.push(char::from(DIGITS[usize::from(byte >> 4)]));
        text.push(char::from(DIGITS[usize::from(byte & 15)]));
    }
    text
}

fn decode_seed(text: &str) -> Result<[u8; 32], &'static str> {
    if text.len() != 64
        || !text
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err(STORE_ERROR);
    }
    let mut seed = [0u8; 32];
    for (index, byte) in seed.iter_mut().enumerate() {
        *byte = u8::from_str_radix(&text[index * 2..index * 2 + 2], 16).map_err(|_| STORE_ERROR)?;
    }
    Ok(seed)
}

fn read_key(path: &Path) -> Result<Option<Ed25519KeyPair>, &'static str> {
    let Some(text) = read_token(path)? else {
        return Ok(None);
    };
    let seed = decode_seed(&text)?;
    Ed25519KeyPair::from_seed_unchecked(&seed)
        .map(Some)
        .map_err(|_| STORE_ERROR)
}

fn machine_code(key: &Ed25519KeyPair) -> String {
    format!("{MACHINE_PLATFORM}-{}", hex(key.public_key().as_ref()))
}

pub(super) fn code(app: &AppHandle) -> Result<String, &'static str> {
    let paths = Paths::for_app(app)?;
    let _lock = paths.lock()?;
    if let Some(key) = read_key(&paths.seed)? {
        return Ok(machine_code(&key));
    }
    if record_exists(&paths.token)? {
        // A missing original key should not silently create a different identity.
        return Err(STORE_ERROR);
    }
    let mut seed = [0u8; 32];
    getrandom::fill(&mut seed).map_err(|_| STORE_ERROR)?;
    let key = Ed25519KeyPair::from_seed_unchecked(&seed).map_err(|_| STORE_ERROR)?;
    save_token(&paths.seed, &hex(&seed))?;
    Ok(machine_code(&key))
}

#[derive(Serialize)]
struct Proof<'a> {
    machine_code: String,
    version: &'a str,
    issued_at: u64,
    nonce: String,
    signature: String,
}

fn claim_message(code: &str, version: &str, issued_at: u64, nonce: &str) -> String {
    format!("{CLAIM_CONTEXT}\n{code}\n{version}\n{issued_at}\n{nonce}")
}

impl<'a> Proof<'a> {
    fn signed(key: &Ed25519KeyPair, version: &'a str, issued_at: u64, nonce: &[u8; 16]) -> Self {
        let machine_code = machine_code(key);
        let nonce = hex(nonce);
        let message = claim_message(&machine_code, version, issued_at, &nonce);
        let signature = hex(key.sign(message.as_bytes()).as_ref());
        Self {
            machine_code,
            version,
            issued_at,
            nonce,
            signature,
        }
    }
}

enum Claim<T> {
    Granted(T),
    VerifiedDenial,
    Indeterminate,
}

impl<T> Claim<T> {
    // An existing managed credential may be cleared only after a verified denial.
    fn confirmed_recovery(self) -> Result<Option<T>, &'static str> {
        match self {
            Self::Granted(value) => Ok(Some(value)),
            Self::VerifiedDenial => Ok(None),
            Self::Indeterminate => Err(POLICY_ERROR),
        }
    }
}

fn classify_claim(status: StatusCode, headers: &HeaderMap) -> Result<Claim<()>, &'static str> {
    match status {
        StatusCode::OK => Ok(Claim::Granted(())),
        StatusCode::NO_CONTENT => {
            let mut values = headers.get_all(CLAIM_STATUS_HEADER).iter();
            let verified = matches!(
                (values.next(), values.next()),
                (Some(value), None) if value.as_bytes() == b"verified-denial"
            );
            Ok(if verified {
                Claim::VerifiedDenial
            } else {
                Claim::Indeterminate
            })
        }
        _ => Err(POLICY_ERROR),
    }
}

async fn claim(app: &AppHandle, key: &Ed25519KeyPair) -> Result<Claim<Managed>, &'static str> {
    let mut nonce = [0; 16];
    getrandom::fill(&mut nonce).map_err(|_| STORE_ERROR)?;
    let issued_at = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| POLICY_ERROR)?
        .as_secs();
    let version = app.package_info().version.to_string();
    let proof = Proof::signed(key, &version, issued_at, &nonce);
    let client = private_client()?;
    let response = client
        .post(format!("{API}/machines/claim"))
        .timeout(Duration::from_secs(10))
        .json(&proof)
        .send()
        .await
        .map_err(|_| POLICY_ERROR)?;
    match classify_claim(response.status(), response.headers())? {
        Claim::Granted(()) => {}
        Claim::VerifiedDenial => return Ok(Claim::VerifiedDenial),
        Claim::Indeterminate => return Ok(Claim::Indeterminate),
    }
    let reply: EnrollReply =
        serde_json::from_slice(&response_bytes(response, MAX_JSON, POLICY_ERROR).await?)
            .map_err(|_| POLICY_ERROR)?;
    if !url_safe(&reply.token, 32..=256) {
        return Err(POLICY_ERROR);
    }
    Ok(Claim::Granted(Managed {
        client,
        token: reply.token,
    }))
}

fn clear_token(path: &Path) -> Result<(), &'static str> {
    // Reading Keychain requires the marker. Remove the marker first so a
    // failed removal never leaves one that points to a missing secret.
    fs::remove_file(path).map_err(|_| STORE_ERROR)?;
    #[cfg(target_os = "macos")]
    let _ = super::keychain::delete(path);
    Ok(())
}

pub(super) async fn check(
    app: &AppHandle,
) -> Result<Option<(Managed, RolloutCheck<Value>)>, &'static str> {
    let paths = Paths::for_app(app)?;
    if !paths.exists()? {
        return Ok(None);
    }
    let _lock = paths.lock()?;

    if let Some(token) = read_token(&paths.token)? {
        let current = Managed {
            client: private_client()?,
            token,
        };
        let check = current.check(app).await?;
        if !matches!(
            check,
            RolloutCheck::NoOffer(CheckOutcome::EnrollmentInvalid)
        ) {
            return Ok(Some((current, check)));
        }

        // A valid claim denial is the only way to leave a previously managed feed.
        let key = read_key(&paths.seed)?.ok_or(STORE_ERROR)?;
        if let Some(recovered) = claim(app, &key).await?.confirmed_recovery()? {
            save_token(&paths.token, &recovered.token)?;
            let check = recovered.check(app).await?;
            return Ok(Some((recovered, check)));
        }
        clear_token(&paths.token)?;
        return Ok(None);
    }

    let Some(key) = read_key(&paths.seed)? else {
        return Ok(None);
    };
    // Having copied a code doesn't mean this installation was ever approved.
    let Ok(Claim::Granted(granted)) = claim(app, &key).await else {
        return Ok(None);
    };
    save_token(&paths.token, &granted.token)?;
    let check = granted.check(app).await?;
    Ok(Some((granted, check)))
}

#[cfg(test)]
mod tests {
    use super::*;
    use ring::signature::{UnparsedPublicKey, ED25519};

    #[test]
    fn machine_claim_uses_fixed_encoding_and_ed25519_signature() {
        let seed = decode_seed("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
            .unwrap();
        let key = Ed25519KeyPair::from_seed_unchecked(&seed).unwrap();
        let proof = Proof::signed(&key, "0.3.7", 1_700_000_000, &[0xab; 16]);
        let code = format!(
            "{MACHINE_PLATFORM}-d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
        );
        assert_eq!(proof.machine_code, code);
        assert_eq!(proof.nonce, "abababababababababababababababab");
        let message = format!(
            "VortoOne desktop rollout machine claim v1\n{code}\n0.3.7\n1700000000\nabababababababababababababababab"
        );
        let signature: Vec<u8> = proof
            .signature
            .as_bytes()
            .chunks_exact(2)
            .map(|pair| u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap())
            .collect();
        assert_eq!(proof.signature.len(), 128);
        assert_eq!(proof.signature, hex(&signature));
        UnparsedPublicKey::new(&ED25519, key.public_key())
            .verify(message.as_bytes(), &signature)
            .unwrap();
        let json = serde_json::to_value(proof).unwrap();
        assert_eq!(json["issued_at"], 1_700_000_000);
        assert_eq!(json["version"], "0.3.7");
        assert_eq!(json.as_object().unwrap().len(), 5);
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn machine_claim_matches_backend_cryptography_vector() {
        let seed: [u8; 32] = std::array::from_fn(|index| index as u8);
        let key = Ed25519KeyPair::from_seed_unchecked(&seed).unwrap();
        let proof = Proof::signed(&key, "0.3.6", 1_800_000_000, &[0xab; 16]);
        assert_eq!(
            proof.machine_code,
            "mac-03a107bff3ce10be1d70dd18e74bc09967e4d6309ba50d5f1ddc8664125531b8"
        );
        assert_eq!(
            proof.signature,
            concat!(
                "f7c3255dcfacf6b9efe012c2c3b18bdb01be16fa93aac013a790ab0b7d97beb714",
                "7a28f7c058f05c6141603cf1c0c88a81dacc8dffe4c4ee2e3a125a2d82fe02",
            ),
        );
    }

    #[test]
    fn machine_recovery_requires_a_single_exact_verified_denial_header() {
        use reqwest::header::HeaderValue;

        let mut headers = HeaderMap::new();
        let recovery = |headers: &HeaderMap| {
            classify_claim(StatusCode::NO_CONTENT, headers).and_then(Claim::confirmed_recovery)
        };
        assert_eq!(recovery(&headers), Err(POLICY_ERROR));
        for value in [
            "",
            "denied",
            "VERIFIED-DENIAL",
            "verified-denial, verified-denial",
        ] {
            headers.insert(CLAIM_STATUS_HEADER, HeaderValue::from_static(value));
            assert_eq!(recovery(&headers), Err(POLICY_ERROR), "{value}");
        }
        headers.insert(
            CLAIM_STATUS_HEADER,
            HeaderValue::from_bytes(&[0x80]).unwrap(),
        );
        assert_eq!(recovery(&headers), Err(POLICY_ERROR));

        headers.insert(
            CLAIM_STATUS_HEADER,
            HeaderValue::from_static("verified-denial"),
        );
        assert_eq!(recovery(&headers), Ok(None));
        headers.append(
            CLAIM_STATUS_HEADER,
            HeaderValue::from_static("verified-denial"),
        );
        assert_eq!(recovery(&headers), Err(POLICY_ERROR));
        headers.insert(
            CLAIM_STATUS_HEADER,
            HeaderValue::from_static("verified-denial"),
        );
        headers.append(CLAIM_STATUS_HEADER, HeaderValue::from_static("other"));
        assert_eq!(recovery(&headers), Err(POLICY_ERROR));
    }

    #[test]
    fn machine_recovery_accepts_success_and_rejects_other_http_statuses() {
        use reqwest::header::HeaderValue;

        let mut headers = HeaderMap::new();
        headers.insert(
            CLAIM_STATUS_HEADER,
            HeaderValue::from_static("verified-denial"),
        );
        let recovery =
            |status| classify_claim(status, &headers).and_then(Claim::confirmed_recovery);
        assert_eq!(recovery(StatusCode::OK), Ok(Some(())));
        for invalid in [
            StatusCode::UNAUTHORIZED,
            StatusCode::FORBIDDEN,
            StatusCode::NOT_FOUND,
            StatusCode::TOO_MANY_REQUESTS,
            StatusCode::SERVICE_UNAVAILABLE,
        ] {
            assert_eq!(recovery(invalid), Err(POLICY_ERROR));
        }
    }

    #[test]
    fn machine_reads_do_not_create_identity_or_storage() {
        let unused = std::env::temp_dir().join(crate::connector_secrets::random_id().unwrap());
        let paths = Paths {
            seed: unused.join(SEED_FILE),
            token: unused.join(TOKEN_FILE),
        };
        assert_eq!(paths.exists(), Ok(false));
        assert!(read_key(&paths.seed).unwrap().is_none());
        assert!(!unused.exists());
        assert!(decode_seed(&"AB".repeat(32)).is_err());
        assert!(decode_seed(&"ff".repeat(31)).is_err());
        assert_ne!(paths.seed, paths.token);
    }

    #[test]
    fn machine_storage_lock_excludes_other_operations_and_releases_on_drop() {
        let directory = std::env::temp_dir().join(crate::connector_secrets::random_id().unwrap());
        let paths = Paths {
            seed: directory.join(SEED_FILE),
            token: directory.join(TOKEN_FILE),
        };
        let lock = paths.lock().unwrap();
        assert!(paths.lock().is_err());
        drop(lock);
        assert!(paths.lock().is_ok());
        fs::remove_dir_all(directory).unwrap();
    }
}
