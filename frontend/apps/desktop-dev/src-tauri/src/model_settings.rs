//! 模型配置只允许随包的配置窗口读写；远端网页拿不到凭据或这些宿主命令。
use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeMap, HashMap, HashSet},
    fs,
    io::Write,
    path::{Path, PathBuf},
    sync::Mutex,
};
use tauri::Manager;

// Keep in sync with the bundled runtime's default when OPENAI_API_BASE is empty.
const DEFAULT_BASE_URL: &str = "https://api.openai.com/v1";
const PROFILE_HEADER: &str = "# VortoOne model profiles: ";
const LEGACY_PROFILE_ID: &str = "existing";
const PROFILE_LIMIT: usize = 20;
static MODEL_SETTINGS_WRITE: Mutex<()> = Mutex::new(());

pub fn env_file(config: &Path, data: &Path) -> Option<PathBuf> {
    let saved = data.join("model.env");
    if saved.is_file() {
        return Some(saved);
    }
    // 保留旧开发版显式指定的模型配置，但运行程序始终优先使用随包后端。
    fs::read_to_string(config.join(crate::backend::CONFIG_FILE))
        .ok()
        .and_then(|text| crate::backend::json_string(&text, "env_file"))
        .map(PathBuf::from)
        .filter(|path| path.is_absolute() && path.is_file())
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ModelView {
    profiles: Vec<ProfileView>,
    default_id: Option<String>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct ProfileView {
    id: String,
    base_url: String,
    model: String,
    has_key: bool,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ModelInput {
    profiles: Vec<ProfileInput>,
    default_id: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct ProfileInput {
    id: String,
    base_url: String,
    model: String,
    api_key: String,
    clear_key: bool,
}

#[derive(Deserialize, Serialize)]
struct Profile {
    id: String,
    base_url: String,
    model: String,
    api_key: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    custom_headers: Option<String>,
}

// The default stays in ordinary dotenv fields for the bundled runtime. The
// other profiles are part of the same atomic file, and never enter its environment.
#[derive(Deserialize, Serialize)]
struct StoredProfiles {
    version: u32,
    default_id: String,
    additional: Vec<Profile>,
}

struct ModelSettings {
    env: HashMap<String, String>,
    profiles: Vec<Profile>,
}

impl ModelSettings {
    fn read(path: Option<&Path>) -> Result<Self, String> {
        let text = path
            .map(fs::read_to_string)
            .transpose()
            .map_err(|_| "无法读取模型配置。")?
            .unwrap_or_default();
        let env: HashMap<_, _> = crate::backend::allowed_env(&text).into_iter().collect();
        let mut metadata = text
            .lines()
            .filter_map(|line| line.strip_prefix(PROFILE_HEADER));
        let invalid = || "已保存的模型列表无法读取。请恢复配置后再试。".to_string();
        let profiles = if let Some(metadata_text) = metadata.next() {
            let stored: StoredProfiles =
                serde_json::from_str(metadata_text).map_err(|_| invalid())?;
            if stored.version != 1 || metadata.next().is_some() {
                return Err(invalid());
            }
            let primary = Profile {
                id: stored.default_id.clone(),
                base_url: env.get("OPENAI_API_BASE").ok_or_else(invalid)?.clone(),
                model: env.get("DEFAULT_MODEL").ok_or_else(invalid)?.clone(),
                api_key: env.get("OPENAI_API_KEY").ok_or_else(invalid)?.clone(),
                custom_headers: env.get("OPENAI_CUSTOM_HEADERS").cloned(),
            };
            let mut profiles = vec![primary];
            profiles.extend(stored.additional);
            validate_ids(
                profiles.iter().map(|profile| profile.id.as_str()),
                &stored.default_id,
            )
            .map_err(|_| invalid())?;
            profiles
        } else {
            legacy_profile(&env).into_iter().collect()
        };
        Ok(Self { env, profiles })
    }

    fn view(&self) -> ModelView {
        ModelView {
            default_id: self.profiles.first().map(|profile| profile.id.clone()),
            profiles: self
                .profiles
                .iter()
                .map(|profile| ProfileView {
                    id: profile.id.clone(),
                    base_url: profile.base_url.clone(),
                    model: profile.model.clone(),
                    has_key: !profile.api_key.trim().is_empty(),
                })
                .collect(),
        }
    }
}

fn effective_base<'a>(found: &'a HashMap<String, String>, inherited: Option<&'a str>) -> &'a str {
    found
        .get("OPENAI_API_BASE")
        .map(String::as_str)
        .or(inherited)
        .map(str::trim)
        .filter(|base| !base.is_empty())
        .unwrap_or(DEFAULT_BASE_URL)
}

fn legacy_profile(found: &HashMap<String, String>) -> Option<Profile> {
    let inherited = std::env::var("OPENAI_API_BASE").ok();
    let api_key = found.get("OPENAI_API_KEY").cloned().unwrap_or_default();
    let base_url = if api_key.trim().is_empty() {
        found.get("OPENAI_API_BASE").cloned().unwrap_or_default()
    } else {
        effective_base(found, inherited.as_deref()).to_owned()
    };
    let model = ["DEFAULT_MODEL", "OPENAI_MODEL", "VORTOONE_MODEL"]
        .iter()
        .filter_map(|name| found.get(*name))
        .find(|model| !model.trim().is_empty())
        .cloned()
        .unwrap_or_default();
    if [&base_url, &model, &api_key]
        .iter()
        .all(|value| value.trim().is_empty())
    {
        return None;
    }
    Some(Profile {
        id: LEGACY_PROFILE_ID.into(),
        base_url,
        model,
        api_key,
        custom_headers: found.get("OPENAI_CUSTOM_HEADERS").cloned(),
    })
}

// The URL parser also accepts some malformed spellings. Those may be saved with an
// explicit key as before, but never cause a stored credential to be reused.
fn model_key_origin(base: &str) -> Option<String> {
    strict_model_url(base).map(|url| url.origin().ascii_serialization())
}

fn strict_model_url(base: &str) -> Option<tauri::Url> {
    let (_, rest) = base.split_once("://")?;
    let authority = rest.split('/').next()?;
    let malformed_escape = base.as_bytes().iter().enumerate().any(|(index, byte)| {
        *byte == b'%'
            && !base
                .as_bytes()
                .get(index + 1..index + 3)
                .is_some_and(|escape| escape.iter().all(u8::is_ascii_hexdigit))
    });
    if authority.is_empty()
        || authority.ends_with(':')
        || authority.contains(['@', '%'])
        || base
            .chars()
            .any(|c| c.is_whitespace() || c.is_control() || c == '\\')
        || malformed_escape
    {
        return None;
    }
    let url: tauri::Url = base.parse().ok()?;
    if !matches!(url.scheme(), "http" | "https")
        || url.host_str().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return None;
    }
    Some(url)
}

fn keyless_loopback(base: &str) -> bool {
    if base
        .chars()
        .any(|c| c.is_whitespace() || c.is_ascii_control() || matches!(c, '%' | '\\' | '?' | '#'))
    {
        return false;
    }
    let Some((scheme, rest)) = base.split_once("://") else {
        return false;
    };
    if !scheme.eq_ignore_ascii_case("http") && !scheme.eq_ignore_ascii_case("https") {
        return false;
    }
    let authority = rest.split('/').next().unwrap_or_default();
    let (host, port) = authority
        .split_once(':')
        .map_or((authority, None), |(host, port)| (host, Some(port)));
    (host == "127.0.0.1" || host.eq_ignore_ascii_case("localhost"))
        && port.is_none_or(|raw| {
            raw.parse::<u16>()
                .is_ok_and(|port| port != 0 && port.to_string() == raw)
        })
}

fn valid_profile_id(id: &str) -> bool {
    !id.is_empty()
        && id.len() <= 64
        && id
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
}

fn validate_ids<'a>(ids: impl Iterator<Item = &'a str>, default_id: &str) -> Result<(), String> {
    let mut seen = HashSet::new();
    for id in ids {
        if !valid_profile_id(id) || !seen.insert(id) || seen.len() > PROFILE_LIMIT {
            return Err("模型列表无效，请重新打开设置。".into());
        }
    }
    if !seen.contains(default_id) {
        return Err("请选择一个默认模型。".into());
    }
    Ok(())
}

fn profile_at_origin<'a>(id: &str, base: &str, profiles: &'a [Profile]) -> Option<&'a Profile> {
    let origin = model_key_origin(base)?;
    profiles.iter().find(|profile| {
        profile.id == id && model_key_origin(&profile.base_url).as_ref() == Some(&origin)
    })
}

fn profile_api_key<'a>(
    base: &str,
    supplied: &'a str,
    clear: bool,
    previous: Option<&'a Profile>,
) -> Result<&'a str, String> {
    let key = if clear {
        ""
    } else if supplied.trim().is_empty() {
        previous.map_or("", |profile| profile.api_key.as_str())
    } else {
        supplied
    };
    if !key.trim().is_empty() {
        return Ok(key);
    }
    if !keyless_loopback(base) {
        return Err("请填写 API Key。只有本机回环地址（localhost 或 127.0.0.1）允许留空。".into());
    }
    Ok("")
}

fn validated_profile(input: ProfileInput, previous: &[Profile]) -> Result<Profile, String> {
    let base = input.base_url.trim();
    let model = input.model.trim();
    if [base, model, &input.api_key]
        .iter()
        .any(|v| v.contains(['\r', '\n', '\0']))
        || base.len() > 2048
        || model.is_empty()
        || model.len() > 256
        || input.api_key.len() > 16384
    {
        return Err("请填写有效的地址、模型名和 API Key，内容不能换行。".into());
    }
    let url: tauri::Url = base.parse().map_err(|_| "请填写完整的模型服务地址。")?;
    if !matches!(url.scheme(), "http" | "https")
        || url.host_str().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return Err("模型地址应使用 http 或 https，并且不包含账号、密码或查询参数。".into());
    }
    let previous = profile_at_origin(&input.id, base, previous);
    let key = profile_api_key(base, &input.api_key, input.clear_key, previous)?.to_owned();
    Ok(Profile {
        id: input.id,
        base_url: base.into(),
        model: model.into(),
        api_key: key,
        custom_headers: previous.and_then(|profile| profile.custom_headers.clone()),
    })
}

#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
mod model_list {
    use super::{profile_api_key, profile_at_origin, strict_model_url, valid_profile_id, Profile};
    use reqwest::{Client, StatusCode};
    use serde::{Deserialize, Serialize};
    use std::{collections::HashSet, net::SocketAddr, time::Duration};
    use tauri::Url;

    const MAX_LIST_BYTES: usize = 1024 * 1024;
    const MAX_MODELS: usize = 500;

    #[derive(Deserialize)]
    #[serde(rename_all = "camelCase")]
    pub struct Input {
        id: String,
        base_url: String,
        api_key: String,
        clear_key: bool,
    }

    #[derive(Serialize)]
    pub struct Output {
        models: Vec<String>,
    }

    impl Input {
        fn request<'a>(&'a self, profiles: &'a [Profile]) -> Result<(Url, &'a str), String> {
            let base = self.base_url.trim();
            if !valid_profile_id(&self.id)
                || self.api_key.len() > 16384
                || self.api_key.contains(['\r', '\n', '\0'])
            {
                return Err("模型配置或 API Key 无效。".into());
            }
            let mut endpoint = strict_model_url(base)
                .filter(|_| self.base_url.len() <= 2048)
                .ok_or("请填写有效的 HTTP 或 HTTPS 模型服务地址。")?;
            let path = format!("{}/models", endpoint.path().trim_end_matches('/'));
            endpoint.set_path(&path);
            let previous = profile_at_origin(&self.id, base, profiles);
            // The returned reference may point to the saved profile as well as this input.
            let key = profile_api_key(base, &self.api_key, self.clear_key, previous)?;
            Ok((endpoint, key))
        }
    }

    fn model_client(endpoint: &Url) -> Result<Client, String> {
        let _ = rustls::crypto::ring::default_provider().install_default();
        let mut client = Client::builder()
            .redirect(reqwest::redirect::Policy::none())
            .connect_timeout(Duration::from_secs(5))
            .timeout(Duration::from_secs(12));
        if matches!(endpoint.host_str(), Some("localhost" | "127.0.0.1")) {
            // Keep the URL hostname for TLS certificate validation and the HTTP Host header.
            client = client
                .no_proxy()
                .resolve("localhost", SocketAddr::from(([127, 0, 0, 1], 0)));
        }
        client.build().map_err(|_| "无法建立模型服务连接。".into())
    }

    fn parse_models(body: &[u8]) -> Result<Output, String> {
        let invalid = "服务未返回可用的模型列表。你可以手动填写模型 ID。";
        let value: serde_json::Value = serde_json::from_slice(body).map_err(|_| invalid)?;
        let rows = value
            .get("data")
            .and_then(serde_json::Value::as_array)
            .ok_or(invalid)?;
        let mut seen = HashSet::new();
        let models = rows
            .iter()
            .filter_map(|row| row.get("id").and_then(serde_json::Value::as_str))
            .map(str::trim)
            .filter(|id| !id.is_empty() && id.len() <= 256 && !id.chars().any(char::is_control))
            .filter(|id| seen.insert(*id))
            .take(MAX_MODELS)
            .map(str::to_owned)
            .collect();
        Ok(Output { models })
    }

    pub async fn fetch(input: Input, profiles: &[Profile]) -> Result<Output, String> {
        let (endpoint, key) = input.request(profiles)?;
        let client = model_client(&endpoint)?;
        let mut request = client
            .get(endpoint)
            .header(reqwest::header::ACCEPT, "application/json");
        if !key.is_empty() {
            request = request.bearer_auth(key);
        }
        let request_error = |error: reqwest::Error| -> String {
            if error.is_timeout() {
                "获取模型列表超时，请稍后重试。".into()
            } else {
                "无法获取模型列表，请检查服务地址或手动填写模型 ID。".into()
            }
        };
        let mut response = request.send().await.map_err(request_error)?;
        let status = response.status();
        if matches!(status, StatusCode::UNAUTHORIZED | StatusCode::FORBIDDEN) {
            return Err("服务拒绝访问模型列表，请确认 API Key 有效。".into());
        }
        if status.is_redirection() {
            return Err("服务返回了重定向，请填写实际的服务地址。".into());
        }
        if !status.is_success() {
            return Err(format!(
                "服务未返回模型列表（HTTP {}）。你可以手动填写模型 ID。",
                status.as_u16()
            ));
        }
        let too_large = "模型列表过大。你可以手动填写模型 ID。";
        if response
            .content_length()
            .is_some_and(|size| size > MAX_LIST_BYTES as u64)
        {
            return Err(too_large.into());
        }
        let mut body = Vec::new();
        while let Some(chunk) = response.chunk().await.map_err(request_error)? {
            if chunk.len() > MAX_LIST_BYTES - body.len() {
                return Err(too_large.into());
            }
            body.extend_from_slice(&chunk);
        }
        parse_models(&body)
    }

    #[cfg(test)]
    mod tests {
        use super::*;
        use serde_json::json;
        use std::{
            io::{BufRead, BufReader, Write},
            net::TcpListener,
            thread,
        };

        fn input(id: &str, base: &str, key: &str) -> Input {
            Input {
                id: id.into(),
                base_url: base.into(),
                api_key: key.into(),
                clear_key: false,
            }
        }

        #[test]
        fn list_endpoint_only_reuses_the_key_for_the_same_profile_and_origin() {
            let profiles = [Profile {
                id: "saved".into(),
                base_url: "https://api.example.test/v1".into(),
                model: "model".into(),
                api_key: "test-saved-key".into(),
                custom_headers: None,
            }];
            let same = input("saved", "https://API.EXAMPLE.TEST:443/gateway/v1///", "");
            let (url, key) = same.request(&profiles).unwrap();
            assert_eq!(url.as_str(), "https://api.example.test/gateway/v1/models");
            assert_eq!(key, "test-saved-key");
            let encoded = input("saved", "https://api.example.test/gateway%2Fname", "");
            assert_eq!(
                encoded.request(&profiles).unwrap().0.path(),
                "/gateway%2Fname/models"
            );
            for rejected in [
                input("other", "https://api.example.test/v1", ""),
                input("saved", "https://other.example.test/v1", ""),
                input("saved", "http://api.example.test/v1", ""),
                input("saved", "https://api.example.test:8443/v1", ""),
                input(
                    "saved",
                    "https://api.example.test/v1?key=test",
                    "explicit-key",
                ),
                input("saved", "https:///api.example.test/v1", "explicit-key"),
                input("new", "http://127.1/v1", ""),
            ] {
                assert!(rejected.request(&profiles).is_err());
            }
            let local = input("new", "http://localhost:11434/v1", "");
            assert_eq!(local.request(&profiles).unwrap().1, "");
            let mut clear = same;
            clear.clear_key = true;
            assert!(clear.request(&profiles).is_err());
        }

        #[test]
        fn model_list_uses_unique_bounded_valid_ids_in_service_order() {
            let mut rows = vec![
                json!({"id": " first "}),
                json!({"id": "first"}),
                json!({"id": ""}),
                json!({"id": "with\nnewline"}),
                json!({"id": "o".repeat(257)}),
                json!({"id": 1}),
            ];
            rows.extend((0..MAX_MODELS).map(|index| json!({"id": format!("model-{index}")})));
            let body = serde_json::to_vec(&json!({"data": rows})).unwrap();
            let models = parse_models(&body).unwrap().models;
            assert_eq!(models.len(), MAX_MODELS);
            assert_eq!(&models[..2], ["first", "model-0"]);
            assert_eq!(models.last().unwrap(), &format!("model-{}", MAX_MODELS - 2));
            assert!(parse_models(br#"{"data":[]}"#).unwrap().models.is_empty());
            assert!(parse_models(br#"{"models":["other-shape"]}"#).is_err());
        }

        #[test]
        fn requests_model_list_over_loopback_without_following_redirects() {
            let listener = TcpListener::bind(("127.0.0.1", 0)).unwrap();
            let port = listener.local_addr().unwrap().port();
            let server = thread::spawn(move || {
                for (expected_auth, response) in [
                    (true, "HTTP/1.1 200 OK\r\nContent-Length: 33\r\nConnection: close\r\n\r\n{\"data\":[{\"id\":\"fixture-model\"}]}"),
                    (false, "HTTP/1.1 302 Found\r\nLocation: /elsewhere\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"),
                ] {
                    let (mut connection, _) = listener.accept().unwrap();
                    connection.set_read_timeout(Some(Duration::from_secs(5))).unwrap();
                    let reader = BufReader::new(&connection);
                    let request: Vec<_> = reader.lines().map(Result::unwrap).take_while(|line| !line.is_empty()).collect();
                    assert_eq!(request.first().unwrap(), "GET /v1/models HTTP/1.1");
                    assert_eq!(request.iter().any(|line| line.eq_ignore_ascii_case("authorization: Bearer test-key")), expected_auth);
                    connection.write_all(response.as_bytes()).unwrap();
                }
            });
            let base = format!("http://localhost:{port}/v1");
            let first = tauri::async_runtime::block_on(fetch(input("new", &base, "test-key"), &[]));
            let second = tauri::async_runtime::block_on(fetch(input("new", &base, ""), &[]));
            server.join().unwrap();
            assert_eq!(first.unwrap().models, ["fixture-model"]);
            assert!(second.err().unwrap().contains("重定向"));
        }
    }
}

fn save(target: &Path, previous: Option<&Path>, input: ModelInput) -> Result<bool, String> {
    let current = ModelSettings::read(previous)?;
    validate_ids(
        input.profiles.iter().map(|profile| profile.id.as_str()),
        &input.default_id,
    )?;
    let mut profiles: Vec<_> = input
        .profiles
        .into_iter()
        .map(|profile| validated_profile(profile, &current.profiles))
        .collect::<Result<_, _>>()?;
    let index = profiles
        .iter()
        .position(|profile| profile.id == input.default_id)
        .ok_or("请选择一个默认模型。")?;
    let primary = profiles.remove(index);
    let active_changed = previous != Some(target)
        || current.profiles.first().is_none_or(|old| {
            old.base_url != primary.base_url
                || old.model != primary.model
                || old.api_key != primary.api_key
                || old.custom_headers != primary.custom_headers
        });
    let mut env: BTreeMap<_, _> = current.env.into_iter().collect();
    // These two legacy names are aliases for the default, not separate settings.
    env.remove("OPENAI_MODEL");
    env.remove("VORTOONE_MODEL");
    env.insert("OPENAI_API_BASE".into(), primary.base_url);
    env.insert("DEFAULT_MODEL".into(), primary.model);
    env.insert("OPENAI_API_KEY".into(), primary.api_key);
    match primary.custom_headers {
        Some(headers) => {
            env.insert("OPENAI_CUSTOM_HEADERS".into(), headers);
        }
        None => {
            env.remove("OPENAI_CUSTOM_HEADERS");
        }
    }

    let metadata = StoredProfiles {
        version: 1,
        default_id: primary.id,
        additional: profiles,
    };
    let json = serde_json::to_string(&metadata).map_err(|_| "无法保存模型配置")?;
    let quote = |value: &str| {
        format!(
            "\"{}\"",
            value
                .replace('\\', "\\\\")
                .replace('"', "\\\"")
                .replace('$', "\\$")
        )
    };
    let mut text = format!("{PROFILE_HEADER}{json}\n");
    for (key, value) in env {
        text.push_str(&format!("{key}={}\n", quote(&value)));
    }
    atomic_write(target, &text)?;
    Ok(active_changed)
}

fn atomic_write(target: &Path, text: &str) -> Result<(), String> {
    let parent = target.parent().ok_or("配置目录无效")?;
    fs::create_dir_all(parent).map_err(|_| "无法创建模型配置目录")?;
    let temporary = parent.join(format!(
        ".model-{}-{}.tmp",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos()
    ));
    let mut options = fs::OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let result = (|| -> std::io::Result<()> {
        let mut file = options.open(&temporary)?;
        file.write_all(text.as_bytes())?;
        file.sync_all()?;
        fs::rename(&temporary, target)
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result.map_err(|_| "无法保存模型配置".into())
}

fn trusted(label: &str, url: &tauri::Url) -> bool {
    label == "model-settings" && crate::packaged_url(url) && url.path() == "/model-settings.html"
}

fn check(window: &tauri::WebviewWindow) -> Result<(), String> {
    if trusted(
        window.label(),
        &window.url().map_err(|_| "无法验证配置窗口")?,
    ) {
        Ok(())
    } else {
        Err("只有模型配置窗口可以使用此操作。".into())
    }
}

#[tauri::command]
pub fn model_settings_get(
    window: tauri::WebviewWindow,
    app: tauri::AppHandle,
) -> Result<ModelView, String> {
    check(&window)?;
    let config = app.path().app_config_dir().map_err(|e| e.to_string())?;
    let data = app.path().app_data_dir().map_err(|e| e.to_string())?;
    Ok(ModelSettings::read(env_file(&config, &data).as_deref())?.view())
}

#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
#[tauri::command]
pub async fn model_settings_list_models(
    window: tauri::WebviewWindow,
    app: tauri::AppHandle,
    input: model_list::Input,
) -> Result<model_list::Output, String> {
    check(&window)?;
    let config = app
        .path()
        .app_config_dir()
        .map_err(|_| "无法访问模型配置。")?;
    let data = app
        .path()
        .app_data_dir()
        .map_err(|_| "无法访问模型配置。")?;
    let settings = ModelSettings::read(env_file(&config, &data).as_deref())?;
    model_list::fetch(input, &settings.profiles).await
}

#[tauri::command]
pub fn model_settings_save(
    window: tauri::WebviewWindow,
    app: tauri::AppHandle,
    input: ModelInput,
) -> Result<(), String> {
    check(&window)?;
    let config = app.path().app_config_dir().map_err(|e| e.to_string())?;
    let data = app.path().app_data_dir().map_err(|e| e.to_string())?;
    let guard = MODEL_SETTINGS_WRITE
        .lock()
        .map_err(|_| "模型配置暂时不可用，请重试。")?;
    let active_changed = save(
        &data.join("model.env"),
        env_file(&config, &data).as_deref(),
        input,
    )?;
    drop(guard);
    if active_changed {
        crate::connect(app);
    }
    let _ = window.close();
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn profile(id: &str, address: &str, model: &str, key: &str) -> ProfileInput {
        ProfileInput {
            id: id.into(),
            base_url: address.into(),
            model: model.into(),
            api_key: key.into(),
            clear_key: false,
        }
    }

    fn settings(default: &str, profiles: Vec<ProfileInput>) -> ModelInput {
        ModelInput {
            default_id: default.into(),
            profiles,
        }
    }

    fn values(path: &Path) -> HashMap<String, String> {
        crate::backend::allowed_env(&fs::read_to_string(path).unwrap())
            .into_iter()
            .collect()
    }

    fn test_root(suffix: &str) -> PathBuf {
        let root =
            std::env::temp_dir().join(format!("vortoone-model-{suffix}-{}", std::process::id()));
        fs::create_dir_all(&root).unwrap();
        root
    }

    #[test]
    fn remote_or_wrong_window_cannot_read_or_write_credentials() {
        assert!(trusted(
            "model-settings",
            &"tauri://localhost/model-settings.html".parse().unwrap()
        ));
        for (label, url) in [
            ("live", "http://127.0.0.1:1234/model-settings.html"),
            ("model-settings", "https://example.test/model-settings.html"),
            ("live", "tauri://localhost/model-settings.html"),
            ("model-settings", "tauri://localhost/index.html"),
        ] {
            assert!(!trusted(label, &url.parse().unwrap()));
        }
    }

    #[test]
    fn only_an_exact_local_endpoint_can_be_saved_without_a_key() {
        for base in [
            "http://127.1/v1",
            "http://user@127.0.0.1/v1",
            "http://127.0.0.1:0/v1",
            "http://127.0.0.1:080/v1",
            "http://localhost.example.test/v1",
            "http://192.0.2.1/v1",
            "http://localhost/v1?",
            "http://localhost/%76%31",
        ] {
            assert!(!keyless_loopback(base), "{base}");
        }
        for base in [
            "http://127.0.0.1:11434/v1",
            "http://LOCALHOST/v1",
            "https://localhost:80/v1",
        ] {
            assert!(keyless_loopback(base), "{base}");
        }
        let root = test_root("invalid");
        let target = root.join("model.env");
        let request = settings(
            "cloud",
            vec![profile(
                "cloud",
                "https://provider.example.test/v1",
                "example-model",
                " ",
            )],
        );
        let error = save(&target, None, request).unwrap_err();
        assert!(error.contains("API Key"));
        assert!(!target.exists());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn blank_key_reuse_is_confined_to_the_effective_service_origin() {
        let root = test_root("origin");
        let target = root.join("model.env");
        let previous = root.join("legacy.env");
        let old_key = "fixture-old-key";
        let fresh_key = "fixture-fresh-key";
        let inherited = Some("https://other.example/v1");
        assert_eq!(effective_base(&HashMap::new(), None), DEFAULT_BASE_URL);
        assert_eq!(
            effective_base(&HashMap::new(), inherited),
            inherited.unwrap()
        );
        fs::write(
            &previous,
            format!("OPENAI_API_KEY={old_key}\nOPENAI_API_BASE=\n"),
        )
        .unwrap();
        assert_eq!(
            ModelSettings::read(Some(&previous))
                .unwrap()
                .view()
                .profiles[0]
                .base_url,
            DEFAULT_BASE_URL
        );
        let input = |address, key| {
            settings(
                LEGACY_PROFILE_ID,
                vec![profile(LEGACY_PROFILE_ID, address, "fixture-model", key)],
            )
        };
        save(
            &target,
            Some(&previous),
            input("https://API.OPENAI.COM:443/alternate/v1", ""),
        )
        .unwrap();
        assert!(values(&target)["OPENAI_API_KEY"] == old_key);
        for changed in [
            "https://other.example/v1",
            "http://api.openai.com/v1",
            "https://api.openai.com:8443/v1",
        ] {
            let error = save(&target, Some(&target), input(changed, "")).unwrap_err();
            assert!(error.contains("API Key"));
            assert!(values(&target)["OPENAI_API_KEY"] == old_key);
        }
        save(
            &target,
            Some(&target),
            input("http://127.0.0.1:11434/v1", ""),
        )
        .unwrap();
        assert!(!ModelSettings::read(Some(&target)).unwrap().view().profiles[0].has_key);
        save(
            &target,
            Some(&target),
            input("https://other.example/v1", fresh_key),
        )
        .unwrap();
        assert!(values(&target)["OPENAI_API_KEY"] == fresh_key);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn malformed_endpoints_do_not_identify_an_origin_for_key_reuse() {
        for malformed in [
            "https:/api.openai.com/v1",
            "https:///api.openai.com/v1",
            "https://@api.openai.com/v1",
            "https://api.openai.com:/v1",
            "https://api.openai.com/invalid%xx",
            "https://api.openai.com/v1?",
            "https://api.openai.com/v1#",
            "https://api.openai.com/with space",
        ] {
            assert!(model_key_origin(malformed).is_none(), "{malformed}");
        }
        assert_eq!(
            model_key_origin("https://API.OPENAI.COM:443/v1/encoded%20segment"),
            model_key_origin(DEFAULT_BASE_URL)
        );
    }

    #[test]
    fn private_atomic_settings_round_trip_and_never_return_the_key() {
        let root = test_root("private");
        let target = root.join("model.env");
        let input = |key, clear_key| {
            let mut local = profile("local", "http://localhost:11434/v1", "local-model", key);
            local.clear_key = clear_key;
            settings("local", vec![local])
        };
        save(&target, None, input("key#part$HOME\\\"", false)).unwrap();
        let response =
            serde_json::to_string(&ModelSettings::read(Some(&target)).unwrap().view()).unwrap();
        assert!(!response.contains("key#part") && response.contains("\"hasKey\":true"));
        assert_eq!(values(&target)["OPENAI_API_KEY"], "key#part$HOME\\\"");
        save(&target, Some(&target), input("", false)).unwrap();
        assert!(ModelSettings::read(Some(&target)).unwrap().view().profiles[0].has_key);
        save(&target, Some(&target), input("", true)).unwrap();
        assert!(!ModelSettings::read(Some(&target)).unwrap().view().profiles[0].has_key);
        assert!(save(&target, None, input("injected\nPATH=evil", false)).is_err());
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(
                fs::metadata(&target).unwrap().permissions().mode() & 0o777,
                0o600
            );
        }
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn legacy_profiles_migrate_and_only_the_current_default_reaches_the_runtime() {
        let root = test_root("profiles");
        let previous = root.join("legacy.env");
        let target = root.join("model.env");
        let cloud = "https://provider.example.test/v1";
        let local = "http://localhost:11434/v1";
        fs::write(
            &previous,
            format!(
                "OPENAI_API_BASE={cloud}\nOPENAI_MODEL=cloud-model\nOPENAI_API_KEY=key-one\n\
             OPENAI_CUSTOM_HEADERS=x-gateway-token:legacy-token\nOPENAI_MAX_RETRIES=2\n",
            ),
        )
        .unwrap();
        let legacy = ModelSettings::read(Some(&previous)).unwrap().view();
        assert_eq!(legacy.default_id.as_deref(), Some(LEGACY_PROFILE_ID));
        assert_eq!(legacy.profiles[0].model, "cloud-model");
        let inputs = |local_key| {
            vec![
                profile(LEGACY_PROFILE_ID, cloud, "cloud-model", ""),
                profile("local", local, "local-model:7b", local_key),
            ]
        };
        assert!(save(
            &target,
            Some(&previous),
            settings(LEGACY_PROFILE_ID, inputs("key-two"))
        )
        .unwrap());
        let env = values(&target);
        assert!(env.values().all(
            |value| !matches!(value.as_str(), "key-two" | "local-model:7b") && value != local
        ));
        assert_eq!(env["OPENAI_API_KEY"], "key-one");
        assert_eq!(env["OPENAI_CUSTOM_HEADERS"], "x-gateway-token:legacy-token");
        assert!(!save(
            &target,
            Some(&target),
            settings(LEGACY_PROFILE_ID, inputs("key-two-replaced"))
        )
        .unwrap());
        assert!(save(&target, Some(&target), settings("local", inputs(""))).unwrap());
        let env = values(&target);
        assert_eq!(env["OPENAI_API_BASE"], local);
        assert_eq!(env["DEFAULT_MODEL"], "local-model:7b");
        assert_eq!(env["OPENAI_API_KEY"], "key-two-replaced");
        assert_eq!(env["OPENAI_MAX_RETRIES"], "2");
        assert!(!env.contains_key("OPENAI_CUSTOM_HEADERS"));
        assert!(!env
            .values()
            .any(|value| matches!(value.as_str(), "key-one" | "cloud-model") || value == cloud));
        let view = ModelSettings::read(Some(&target)).unwrap().view();
        assert_eq!(view.default_id.as_deref(), Some("local"));
        assert_eq!(view.profiles.len(), 2);
        assert!(view.profiles.iter().all(|profile| profile.has_key));
        assert!(save(
            &target,
            Some(&target),
            settings(LEGACY_PROFILE_ID, inputs(""))
        )
        .unwrap());
        assert_eq!(
            values(&target)["OPENAI_CUSTOM_HEADERS"],
            "x-gateway-token:legacy-token"
        );
        assert_eq!(values(&target)["OPENAI_API_KEY"], "key-one");
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn invalid_collection_or_unowned_profile_key_cannot_overwrite_the_settings() {
        let root = test_root("guard");
        let target = root.join("model.env");
        let cloud = "https://provider.example.test/v1";
        let make = |id, key| profile(id, cloud, "fixture-model", key);
        save(
            &target,
            None,
            settings("one", vec![make("one", "fixture-key")]),
        )
        .unwrap();
        let saved = fs::read(&target).unwrap();
        for invalid in [
            settings("one", vec![make("one", ""), make("one", "")]),
            settings("missing", vec![make("one", "")]),
            settings("one", vec![make("one", ""), make("two", "")]),
            settings(
                "one",
                vec![make("one", ""), make("two/invalid", "new-fixture-key")],
            ),
        ] {
            assert!(save(&target, Some(&target), invalid).is_err());
            assert_eq!(fs::read(&target).unwrap(), saved);
        }
        fs::write(
            &target,
            format!("{PROFILE_HEADER}invalid\nDEFAULT_MODEL=example\n"),
        )
        .unwrap();
        assert!(ModelSettings::read(Some(&target)).is_err());
        assert!(save(
            &target,
            Some(&target),
            settings("one", vec![make("one", "new-fixture-key")])
        )
        .is_err());
        fs::remove_dir_all(root).unwrap();
    }
}
