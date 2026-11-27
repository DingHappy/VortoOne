//! Device enrollment and authenticated policy for the stable updater.

use reqwest::{Client, RequestBuilder, Response, StatusCode};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    fs,
    io::Read,
    path::{Path, PathBuf},
    time::Duration,
};
use tauri::{AppHandle, Manager, Url, WebviewWindow};

const HOST: &str = "vortoone.com";
const API: &str = "https://vortoone.com/api/desktop-rollout";
const VERSIONS: (&str, &str) = ("versions", "versions.html");
const ENROLLMENT: (&str, &str) = ("gray-enrollment", "gray-enrollment.html");
const POLICY_ERROR: &str = "无法核对灰度更新资格，请稍后重试或打开版本管理。";
const DOWNLOAD_ERROR: &str = "无法下载或验签灰度更新，请稍后重试。";
const STORE_ERROR: &str = "无法访问本机灰度凭据，请在版本管理中检查设备登记。";
const MAX_JSON: usize = 128 * 1024;
const MAX_DOWNLOAD: usize = 512 * 1024 * 1024;

#[cfg(target_os = "macos")]
pub const TARGET: (&str, &str) = ("darwin-aarch64", "aarch64.app.tar.gz");
#[cfg(target_os = "windows")]
pub const TARGET: (&str, &str) = ("windows-x86_64-nsis", "windows-x64.exe");
#[cfg(target_os = "linux")]
pub const TARGET: (&str, &str) = ("linux-x86_64-deb", "ubuntu-amd64.deb");

fn supported(app: &AppHandle) -> bool {
    crate::is_stable_channel(app.config())
        && crate::stable_update::supported()
        && (cfg!(not(target_os = "macos")) || cfg!(target_arch = "aarch64"))
}

#[derive(Serialize)]
pub struct GrayStatus {
    supported: bool,
    enrolled: bool,
    version: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    platform: Option<&'static str>,
}

#[derive(Deserialize)]
pub struct EnrollInput {
    code: String,
    name: String,
}

#[derive(Deserialize)]
struct EnrollReply {
    token: String,
}

#[derive(Clone, Copy)]
pub enum Report {
    Installing,
    Failed,
    Declined,
}

impl Report {
    fn name(self) -> &'static str {
        match self {
            Self::Installing => "installing",
            Self::Failed => "failed",
            Self::Declined => "declined",
        }
    }
}

pub struct Managed {
    client: Client,
    token: String,
}

fn private_client() -> Result<Client, &'static str> {
    // Use the same TLS provider as the public updater, including before its first check.
    let _ = rustls::crypto::ring::default_provider().install_default();
    Client::builder()
        .https_only(true)
        .redirect(reqwest::redirect::Policy::none())
        .connect_timeout(Duration::from_secs(10))
        .read_timeout(Duration::from_secs(30))
        .build()
        .map_err(|_| POLICY_ERROR)
}

async fn response_bytes(
    mut response: Response,
    max: usize,
    error: &'static str,
) -> Result<Vec<u8>, &'static str> {
    if response
        .content_length()
        .is_some_and(|size| size > max as u64)
    {
        return Err(error);
    }
    let mut bytes = Vec::new();
    while let Some(chunk) = response.chunk().await.map_err(|_| error)? {
        if chunk.len() > max - bytes.len() {
            return Err(error);
        }
        bytes.extend_from_slice(&chunk);
    }
    Ok(bytes)
}

impl Managed {
    pub fn read(app: &AppHandle) -> Result<Option<Self>, &'static str> {
        if !supported(app) {
            return Ok(None);
        }
        read_token(&record_path(app)?)?
            .map(|token| {
                Ok(Self {
                    client: private_client()?,
                    token,
                })
            })
            .transpose()
    }

    pub async fn manifest(&self, app: &AppHandle) -> Result<Option<Value>, &'static str> {
        let response = self
            .client
            .post(format!("{API}/check"))
            .timeout(Duration::from_secs(20))
            .bearer_auth(&self.token)
            .json(&json!({"platform": TARGET.0, "version": app.package_info().version.to_string()}))
            .send()
            .await
            .map_err(|_| POLICY_ERROR)?;
        match response.status() {
            StatusCode::NO_CONTENT => Ok(None),
            StatusCode::OK => {
                let bytes = response_bytes(response, MAX_JSON, POLICY_ERROR).await?;
                let manifest = serde_json::from_slice(&bytes).map_err(|_| POLICY_ERROR)?;
                validate_manifest(&manifest, TARGET)?;
                Ok(Some(manifest))
            }
            _ => Err(POLICY_ERROR),
        }
    }

    fn download_request(&self, version: &str, url: &Url) -> Result<RequestBuilder, &'static str> {
        if !download_url_valid(url, version, TARGET.1) {
            return Err(DOWNLOAD_ERROR);
        }
        // Only a validated artifact URL can receive the device credential.
        Ok(self
            .client
            .get(url.clone())
            .bearer_auth(&self.token)
            .timeout(Duration::from_secs(15 * 60)))
    }

    pub async fn download(&self, version: &str, url: &Url) -> Result<Vec<u8>, &'static str> {
        let response = self
            .download_request(version, url)?
            .send()
            .await
            .map_err(|_| DOWNLOAD_ERROR)?;
        if response.status() != StatusCode::OK {
            return Err(DOWNLOAD_ERROR);
        }
        response_bytes(response, MAX_DOWNLOAD, DOWNLOAD_ERROR).await
    }

    pub async fn report(&self, release: &str, status: Report) -> Result<(), &'static str> {
        let response = self
            .client
            .post(format!("{API}/report"))
            .timeout(Duration::from_secs(15))
            .bearer_auth(&self.token)
            .json(&json!({"release": release, "status": status.name()}))
            .send()
            .await
            .map_err(|_| POLICY_ERROR)?;
        match response.status() {
            StatusCode::NO_CONTENT => Ok(()),
            StatusCode::CONFLICT => Err("这台设备的更新已暂停或撤回，安装已停止。"),
            _ => Err(POLICY_ERROR),
        }
    }

    async fn leave(&self) -> Result<(), &'static str> {
        const ERROR: &str = "无法退出灰度，请联网后重试。";
        let response = self
            .client
            .post(format!("{API}/leave"))
            .timeout(Duration::from_secs(15))
            .bearer_auth(&self.token)
            .send()
            .await
            .map_err(|_| ERROR)?;
        match response.status() {
            StatusCode::NO_CONTENT | StatusCode::UNAUTHORIZED => Ok(()),
            _ => Err(ERROR),
        }
    }
}

fn url_safe(value: &str, bounds: std::ops::RangeInclusive<usize>) -> bool {
    bounds.contains(&value.len())
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'))
}

fn stable_version(version: &str) -> bool {
    let mut parts = version.split('.');
    let valid_component = |part: &str| {
        !part.is_empty()
            && part.bytes().all(|byte| byte.is_ascii_digit())
            && (part == "0" || !part.starts_with('0'))
            && part.parse::<u64>().is_ok()
    };
    (0..3).all(|_| parts.next().is_some_and(valid_component)) && parts.next().is_none()
}

fn validate_manifest(manifest: &Value, target: (&str, &str)) -> Result<(), &'static str> {
    let version = manifest["version"]
        .as_str()
        .filter(|v| stable_version(v))
        .ok_or(POLICY_ERROR)?;
    let assets = manifest["platforms"]
        .as_object()
        .filter(|assets| assets.len() == 1)
        .ok_or(POLICY_ERROR)?;
    let asset = assets.get(target.0).ok_or(POLICY_ERROR)?;
    let url = asset["url"]
        .as_str()
        .and_then(|url| Url::parse(url).ok())
        .ok_or(POLICY_ERROR)?;
    if !download_url_valid(&url, version, target.1)
        || !asset["signature"]
            .as_str()
            .is_some_and(|signature| !signature.is_empty())
        || manifest.get("url").is_some()
        || manifest.get("signature").is_some()
    {
        return Err(POLICY_ERROR);
    }
    Ok(())
}

fn download_url_valid(url: &Url, version: &str, suffix: &str) -> bool {
    if url.scheme() != "https"
        || url.host_str() != Some(HOST)
        || url.port().is_some()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return false;
    }
    let Some(mut parts) = url.path_segments() else {
        return false;
    };
    parts
        .by_ref()
        .take(3)
        .eq(["api", "desktop-rollout", "download"])
        && parts
            .next()
            .is_some_and(|ticket| url_safe(ticket, 16..=256))
        && parts.next() == Some(format!("VortoOne_{version}_{suffix}").as_str())
        && parts.next().is_none()
}

fn trusted_window(label: &str, url: &Url, expected: (&str, &str)) -> bool {
    label == expected.0
        && crate::packaged_url(url)
        && url.path().strip_prefix('/') == Some(expected.1)
        && url.query().is_none()
        && url.fragment().is_none()
        && url.username().is_empty()
        && url.password().is_none()
        && url.port().is_none()
}

fn check_window(window: &WebviewWindow, expected: (&str, &str)) -> Result<(), &'static str> {
    if window
        .url()
        .ok()
        .is_some_and(|url| trusted_window(window.label(), &url, expected))
    {
        Ok(())
    } else {
        Err("此窗口无法执行该操作。")
    }
}

fn require_supported(app: &AppHandle) -> Result<(), &'static str> {
    if supported(app) {
        Ok(())
    } else {
        Err("当前安装形式不支持灰度更新。")
    }
}

#[cfg(target_os = "macos")]
pub fn accepted_enrollment_link(raw: &str) -> bool {
    raw.eq_ignore_ascii_case("vortoone-update://enroll")
}

pub fn open_enrollment(app: &AppHandle) -> Result<(), &'static str> {
    require_supported(app)?;
    if let Some(window) = app.get_webview_window(ENROLLMENT.0) {
        let _ = window.unminimize();
    }
    crate::show(
        app,
        ENROLLMENT.0,
        ENROLLMENT.1,
        "VortoOne · 设备登记",
        520.0,
        420.0,
    )
    .map_err(|_| "无法打开设备登记窗口。")
}

fn status(app: &AppHandle) -> Result<GrayStatus, &'static str> {
    let supported = supported(app);
    Ok(GrayStatus {
        supported,
        enrolled: supported && record_exists(&record_path(app)?)?,
        version: app.package_info().version.to_string(),
        platform: supported.then_some(TARGET.0),
    })
}

#[cfg(target_os = "macos")]
pub fn check_at_startup(app: &AppHandle) -> bool {
    // If enrollment storage is unavailable, let the managed check fail closed.
    supported(app) && status(app).map(|status| status.enrolled).unwrap_or(true)
}

#[tauri::command]
pub fn gray_update_status(
    window: WebviewWindow,
    app: AppHandle,
) -> Result<GrayStatus, &'static str> {
    let expected = if window.label() == ENROLLMENT.0 {
        ENROLLMENT
    } else {
        VERSIONS
    };
    check_window(&window, expected)?;
    status(&app)
}

#[tauri::command]
pub async fn gray_update_enroll(
    window: WebviewWindow,
    app: AppHandle,
    input: EnrollInput,
) -> Result<GrayStatus, &'static str> {
    check_window(&window, ENROLLMENT)?;
    require_supported(&app)?;
    let _guard = crate::stable_update::begin(&app)?;
    let path = record_path(&app)?;
    if record_exists(&path)? {
        return Err("这台设备已登记，请先退出当前灰度。");
    }
    let code = input.code.trim();
    let name = input.name.trim();
    if code.len() != 20
        || !code.bytes().all(|byte| byte.is_ascii_alphanumeric())
        || !(1..=64).contains(&name.chars().count())
        || name.chars().any(char::is_control)
    {
        return Err("请填写有效的登记码和设备名称。");
    }
    let client = private_client()?;
    let response = client
        .post(format!("{API}/enroll"))
        .timeout(Duration::from_secs(20))
        .json(&json!({
            "code": code, "name": name, "platform": TARGET.0,
            "version": app.package_info().version.to_string(),
        }))
        .send()
        .await
        .map_err(|_| POLICY_ERROR)?;
    if !matches!(response.status(), StatusCode::OK | StatusCode::CREATED) {
        return Err("无法登记设备，请核对登记码或联系发布管理员。");
    }
    let bytes = response_bytes(response, MAX_JSON, POLICY_ERROR).await?;
    let reply: EnrollReply = serde_json::from_slice(&bytes).map_err(|_| POLICY_ERROR)?;
    if !url_safe(&reply.token, 32..=256) {
        return Err(POLICY_ERROR);
    }
    if let Err(error) = save_token(&path, &reply.token) {
        let _ = Managed {
            client,
            token: reply.token,
        }
        .leave()
        .await;
        return Err(error);
    }
    status(&app)
}

#[tauri::command]
pub async fn gray_update_leave(
    window: WebviewWindow,
    app: AppHandle,
) -> Result<GrayStatus, &'static str> {
    check_window(&window, VERSIONS)?;
    require_supported(&app)?;
    let _guard = crate::stable_update::begin(&app)?;
    let path = record_path(&app)?;
    if let Some(managed) = Managed::read(&app)? {
        managed.leave().await?;
        #[cfg(target_os = "macos")]
        keychain::delete(&path)?;
        fs::remove_file(path).map_err(|_| STORE_ERROR)?;
    }
    status(&app)
}

#[tauri::command]
pub async fn gray_update_check(window: WebviewWindow, app: AppHandle) -> Result<(), &'static str> {
    check_window(&window, VERSIONS)?;
    require_supported(&app)?;
    if !status(&app)?.enrolled {
        return Err("这台设备尚未登记。");
    }
    crate::stable_update::check_from_versions(&app).await
}

#[cfg(any(target_os = "windows", target_os = "linux"))]
#[tauri::command]
pub async fn gray_update_open_enrollment(
    window: WebviewWindow,
    app: AppHandle,
) -> Result<(), &'static str> {
    check_window(&window, VERSIONS)?;
    tauri::async_runtime::spawn_blocking(move || open_enrollment(&app))
        .await
        .map_err(|_| "无法打开设备登记窗口。")?
}

fn record_path(app: &AppHandle) -> Result<PathBuf, &'static str> {
    app.path()
        .app_local_data_dir()
        .map(|dir| dir.join("desktop-rollout").join("enrollment"))
        .map_err(|_| STORE_ERROR)
}

fn record_exists(path: &Path) -> Result<bool, &'static str> {
    let directory = path.parent().ok_or(STORE_ERROR)?;
    match fs::symlink_metadata(directory) {
        Ok(meta) if meta.is_dir() && !meta.file_type().is_symlink() => {}
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(false),
        _ => return Err(STORE_ERROR),
    }
    match fs::symlink_metadata(path) {
        Ok(meta) if meta.is_file() && !meta.file_type().is_symlink() => Ok(true),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(false),
        _ => Err(STORE_ERROR),
    }
}

fn read_token(path: &Path) -> Result<Option<String>, &'static str> {
    if !record_exists(path)? {
        return Ok(None);
    }
    let mut options = fs::OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW);
    }
    let file = options.open(path).map_err(|_| STORE_ERROR)?;
    let metadata = file.metadata().map_err(|_| STORE_ERROR)?;
    if !metadata.is_file() || metadata.len() > 4096 {
        return Err(STORE_ERROR);
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if metadata.mode() & 0o777 != 0o600
            || metadata.nlink() != 1
            || metadata.uid() != unsafe { libc::geteuid() }
        {
            return Err(STORE_ERROR);
        }
    }
    let mut record = Vec::new();
    file.take(4097)
        .read_to_end(&mut record)
        .map_err(|_| STORE_ERROR)?;
    if record.len() > 4096 {
        return Err(STORE_ERROR);
    }

    #[cfg(target_os = "macos")]
    let token = if record == b"keychain\n" {
        keychain::get(path)?
    } else {
        return Err(STORE_ERROR);
    };
    #[cfg(target_os = "linux")]
    let token = String::from_utf8(record).map_err(|_| STORE_ERROR)?;
    #[cfg(target_os = "windows")]
    let token = String::from_utf8(dpapi(&record, false)?).map_err(|_| STORE_ERROR)?;
    if url_safe(&token, 32..=256) {
        Ok(Some(token))
    } else {
        Err(STORE_ERROR)
    }
}

#[cfg(unix)]
fn save_token(path: &Path, token: &str) -> Result<(), &'static str> {
    use crate::connector_secrets::atomic_private_write;
    #[cfg(target_os = "macos")]
    {
        // The marker keeps managed policy in effect if the Keychain is locked.
        keychain::put(path, token)?;
        if atomic_private_write(path, b"keychain\n").is_err() {
            let _ = keychain::delete(path);
            return Err(STORE_ERROR);
        }
        Ok(())
    }
    #[cfg(target_os = "linux")]
    atomic_private_write(path, token.as_bytes()).map_err(|_| STORE_ERROR)
}

#[cfg(target_os = "macos")]
mod keychain {
    use super::{Path, STORE_ERROR};
    use crate::connector_secrets::{SecretRef, SecretStore, Storage, SystemStore};

    fn store(path: &Path) -> Result<(SystemStore, SecretRef), &'static str> {
        let directory = path.parent().ok_or(STORE_ERROR)?.to_owned();
        // Nil UUIDs are reserved. Computer credentials always use random v4 UUIDs.
        let nil = "00000000000000000000000000000000";
        Ok((
            SystemStore { directory },
            SecretRef {
                home: nil.into(),
                connector: nil.into(),
            },
        ))
    }

    pub fn get(path: &Path) -> Result<String, &'static str> {
        let (store, key) = store(path)?;
        store.get(&key, Storage::Keychain).map_err(|_| STORE_ERROR)
    }

    pub fn put(path: &Path, token: &str) -> Result<(), &'static str> {
        let (store, key) = store(path)?;
        store
            .put(&key, Storage::Keychain, token)
            .map_err(|_| STORE_ERROR)
    }

    pub fn delete(path: &Path) -> Result<(), &'static str> {
        let (store, key) = store(path)?;
        store
            .delete(&key, Storage::Keychain)
            .map_err(|_| STORE_ERROR)
    }
}

#[cfg(target_os = "windows")]
fn save_token(path: &Path, token: &str) -> Result<(), &'static str> {
    use std::io::Write;
    let directory = path.parent().ok_or(STORE_ERROR)?;
    fs::create_dir_all(directory).map_err(|_| STORE_ERROR)?;
    if record_exists(path)? {
        return Err(STORE_ERROR);
    }
    let secret = dpapi(token.as_bytes(), true)?;
    let id = crate::connector_secrets::random_id().map_err(|_| STORE_ERROR)?;
    let temporary = directory.join(format!(".{id}.tmp"));
    let result = (|| -> std::io::Result<()> {
        let mut file = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temporary)?;
        file.write_all(&secret)?;
        file.sync_all()?;
        drop(file);
        fs::rename(&temporary, path)
    })();
    if result.is_err() {
        let _ = fs::remove_file(temporary);
    }
    result.map_err(|_| STORE_ERROR)
}

#[cfg(target_os = "windows")]
fn dpapi(bytes: &[u8], encrypt: bool) -> Result<Vec<u8>, &'static str> {
    use std::ptr::{null, null_mut};
    use windows_sys::Win32::{
        Foundation::LocalFree,
        Security::Cryptography::{
            CryptProtectData, CryptUnprotectData, CRYPTPROTECT_UI_FORBIDDEN, CRYPT_INTEGER_BLOB,
        },
    };
    let mut input = bytes.to_vec();
    let input_blob = CRYPT_INTEGER_BLOB {
        cbData: input.len() as u32,
        pbData: input.as_mut_ptr(),
    };
    let mut output = CRYPT_INTEGER_BLOB {
        cbData: 0,
        pbData: null_mut(),
    };
    // Current-user DPAPI scope, without an interactive prompt or a machine-wide fallback.
    unsafe {
        let ok = if encrypt {
            CryptProtectData(
                &input_blob,
                null(),
                null(),
                null(),
                null(),
                CRYPTPROTECT_UI_FORBIDDEN,
                &mut output,
            )
        } else {
            CryptUnprotectData(
                &input_blob,
                null_mut(),
                null(),
                null(),
                null(),
                CRYPTPROTECT_UI_FORBIDDEN,
                &mut output,
            )
        };
        if ok == 0 || output.pbData.is_null() {
            return Err(STORE_ERROR);
        }
        let result = std::slice::from_raw_parts(output.pbData, output.cbData as usize).to_vec();
        LocalFree(output.pbData.cast());
        Ok(result)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[cfg(target_os = "macos")]
    #[test]
    fn gray_invitation_accepts_only_the_bare_stable_link() {
        assert!(accepted_enrollment_link("vortoone-update://enroll"));
        for other in [
            "vortoone://enroll",
            "vortoone://connect",
            "vortoone://pair",
            "vortoone-update://enroll?code=ignored",
            "vortoone-update://enroll#code=ignored",
            "vortoone-update://enroll?",
            "vortoone-update://enroll/",
            "vortoone-update://enroll/other",
            "vortoone-update://user@enroll",
            "vortoone-update://enroll:90",
            "https://enroll",
        ] {
            assert!(!accepted_enrollment_link(other), "{other}");
        }
    }

    #[test]
    fn gray_commands_are_bound_to_their_exact_packaged_windows() {
        for (label, page) in [VERSIONS, ENROLLMENT] {
            for origin in ["tauri://localhost", "http://tauri.localhost"] {
                let url = Url::parse(&format!("{origin}/{page}")).unwrap();
                assert!(trusted_window(label, &url, (label, page)));
                assert!(!trusted_window("live", &url, (label, page)));
                for extra in ["?next", "#fragment", "/nested"] {
                    let changed = Url::parse(&format!("{url}{extra}")).unwrap();
                    assert!(!trusted_window(label, &changed, (label, page)));
                }
            }
            let remote = Url::parse(&format!("https://vortoone.com/{page}")).unwrap();
            assert!(!trusted_window(label, &remote, (label, page)));
        }
        let normal_page = Url::parse("tauri://localhost/versions.html").unwrap();
        assert!(!trusted_window(ENROLLMENT.0, &normal_page, ENROLLMENT));
    }

    fn offer(version: &str, target: (&str, &str)) -> Value {
        let url = format!(
            "{API}/download/0123456789abcdefABCDEF/VortoOne_{version}_{}",
            target.1
        );
        json!({"version": version, "platforms": {(target.0): {
            "url": url, "signature": "signed release",
        }}})
    }

    #[test]
    fn gray_requires_one_matching_platform_and_a_strict_stable_version() {
        for target in [
            ("darwin-aarch64", "aarch64.app.tar.gz"),
            ("windows-x86_64-nsis", "windows-x64.exe"),
            ("linux-x86_64-deb", "ubuntu-amd64.deb"),
        ] {
            let mut manifest = offer("0.3.5", target);
            assert!(validate_manifest(&manifest, target).is_ok());
            manifest["platforms"]["indirect-platform"] = json!({});
            assert!(validate_manifest(&manifest, target).is_err());
            for invalid in [
                "0.03.5",
                "v0.3.5",
                "0.3.5-beta.1",
                "0.3",
                "0.3.5.2",
                "0.+3.5",
            ] {
                assert!(
                    validate_manifest(&offer(invalid, target), target).is_err(),
                    "{invalid}"
                );
            }
        }
    }

    #[test]
    fn gray_download_attaches_device_bearer_only_for_validated_artifacts() {
        let managed = Managed {
            client: private_client().unwrap(),
            token: "test-device-token".into(),
        };
        let manifest = offer("0.3.5", TARGET);
        let url = manifest["platforms"][TARGET.0]["url"].as_str().unwrap();
        let official = Url::parse(url).unwrap();
        let request = managed
            .download_request("0.3.5", &official)
            .unwrap()
            .build()
            .unwrap();
        assert_eq!(request.method(), reqwest::Method::GET);
        assert_eq!(request.url(), &official);
        let authorization = &request.headers()[reqwest::header::AUTHORIZATION];
        assert_eq!(authorization, "Bearer test-device-token");
        assert!(authorization.is_sensitive());

        for candidate in [
            url.replace("https://", "http://"),
            url.replace(HOST, "example.invalid"),
            url.replace(HOST, "vortoone.com.example.invalid"),
            url.replace(HOST, "name:pass@vortoone.com"),
            url.replace(HOST, "vortoone.com:444"),
            url.replace("/download/", "/public/"),
            url.replace("0123456789abcdefABCDEF", "bad%2fticket"),
            url.replace("0.3.5", "0.3.4"),
            format!("{url}?query"),
            format!("{url}#fragment"),
            format!("{url}.zip"),
            format!("{url}/extra"),
        ] {
            assert!(
                managed
                    .download_request("0.3.5", &Url::parse(&candidate).unwrap())
                    .is_err(),
                "{candidate}"
            );
        }
    }

    #[test]
    fn gray_http_client_initializes_tls_before_any_public_update_check() {
        assert!(private_client().is_ok());
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn gray_unreadable_enrollment_does_not_become_public() {
        use std::os::unix::fs::PermissionsExt;
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("rollout").join("enrollment");
        assert_eq!(read_token(&path), Ok(None));
        let token = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcdef";
        save_token(&path, token).unwrap();
        assert_eq!(read_token(&path), Ok(Some(token.into())));
        fs::set_permissions(&path, fs::Permissions::from_mode(0o644)).unwrap();
        assert_eq!(read_token(&path), Err(STORE_ERROR));
    }

    #[cfg(target_os = "windows")]
    #[test]
    fn gray_dpapi_round_trip_has_no_plaintext_file() {
        let token = b"ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcdef";
        let protected = dpapi(token, true).unwrap();
        assert!(!protected.windows(token.len()).any(|bytes| bytes == token));
        assert_eq!(dpapi(&protected, false).unwrap(), token);
    }
}
