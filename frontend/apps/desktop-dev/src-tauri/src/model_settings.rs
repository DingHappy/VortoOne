//! 模型配置只允许随包的配置窗口读写；远端网页拿不到凭据或这些宿主命令。
use std::{collections::HashMap, fs, io::Write, path::{Path, PathBuf}};
use serde::{Deserialize, Serialize};
use tauri::Manager;

pub fn env_file(config: &Path, data: &Path) -> Option<PathBuf> {
    let saved = data.join("model.env");
    if saved.is_file() { return Some(saved); }
    // 保留旧开发版显式指定的模型配置，但运行程序始终优先使用随包后端。
    fs::read_to_string(config.join(crate::backend::CONFIG_FILE)).ok()
        .and_then(|text| crate::backend::json_string(&text, "env_file"))
        .map(PathBuf::from).filter(|path| path.is_absolute() && path.is_file())
}

fn values(path: Option<&Path>) -> HashMap<String, String> {
    path.and_then(|path| fs::read_to_string(path).ok())
        .map(|text| crate::backend::allowed_env(&text).into_iter().collect()).unwrap_or_default()
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ModelView { base_url: String, model: String, has_key: bool }

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ModelInput { base_url: String, model: String, api_key: String, clear_key: bool }

fn view(path: Option<&Path>) -> ModelView {
    let found = values(path);
    ModelView {
        base_url: found.get("OPENAI_API_BASE").cloned().unwrap_or_default(),
        model: found.get("DEFAULT_MODEL").or_else(|| found.get("VORTOONE_MODEL")).cloned().unwrap_or_default(),
        has_key: found.get("OPENAI_API_KEY").is_some_and(|key| !key.is_empty()),
    }
}

fn save(target: &Path, previous: Option<&Path>, input: ModelInput) -> Result<(), String> {
    let base = input.base_url.trim();
    let model = input.model.trim();
    if [base, model, &input.api_key].iter().any(|v| v.contains(['\r', '\n', '\0']))
        || base.len() > 2048 || model.is_empty() || model.len() > 256 || input.api_key.len() > 16384 {
        return Err("请填写有效的地址、模型名和 API Key，内容不能换行。".into());
    }
    let url: tauri::Url = base.parse().map_err(|_| "请填写完整的模型服务地址。")?;
    if !matches!(url.scheme(), "http" | "https") || url.host_str().is_none()
        || !url.username().is_empty() || url.password().is_some() || url.query().is_some() || url.fragment().is_some() {
        return Err("模型地址应使用 http 或 https，并且不包含账号、密码或查询参数。".into());
    }
    let mut found = values(previous);
    let key = if input.clear_key { String::new() } else if input.api_key.is_empty() {
        found.get("OPENAI_API_KEY").cloned().unwrap_or_default()
    } else { input.api_key };
    let quote = |s: &str| format!("\"{}\"", s.replace('\\', "\\\\").replace('"', "\\\"").replace('$', "\\$"));
    found.insert("OPENAI_API_BASE".into(), base.into());
    found.insert("DEFAULT_MODEL".into(), model.into());
    found.insert("OPENAI_API_KEY".into(), key);
    let sorted: std::collections::BTreeMap<_, _> = found.into_iter().collect();
    let text = sorted.iter().map(|(key, value)| format!("{key}={}\n", quote(value))).collect::<String>();
    let parent = target.parent().ok_or("配置目录无效")?;
    fs::create_dir_all(parent).map_err(|_| "无法创建模型配置目录")?;
    let temporary = parent.join(format!(".model-{}-{}.tmp", std::process::id(),
        std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_nanos()));
    let mut options = fs::OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)] {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let result = (|| -> std::io::Result<()> {
        let mut file = options.open(&temporary)?;
        file.write_all(text.as_bytes())?;
        file.sync_all()?;
        fs::rename(&temporary, target)
    })();
    if result.is_err() { let _ = fs::remove_file(&temporary); }
    result.map_err(|_| "无法保存模型配置".into())
}

fn trusted(label: &str, url: &tauri::Url) -> bool {
    label == "model-settings" && crate::packaged_url(url) && url.path() == "/model-settings.html"
}

fn check(window: &tauri::WebviewWindow) -> Result<(), String> {
    if trusted(window.label(), &window.url().map_err(|_| "无法验证配置窗口")?) { Ok(()) }
    else { Err("只有模型配置窗口可以使用此操作。".into()) }
}

#[tauri::command]
pub fn model_settings_get(window: tauri::WebviewWindow, app: tauri::AppHandle) -> Result<ModelView, String> {
    check(&window)?;
    let config = app.path().app_config_dir().map_err(|e| e.to_string())?;
    let data = app.path().app_data_dir().map_err(|e| e.to_string())?;
    Ok(view(env_file(&config, &data).as_deref()))
}

#[tauri::command]
pub fn model_settings_save(window: tauri::WebviewWindow, app: tauri::AppHandle, input: ModelInput) -> Result<(), String> {
    check(&window)?;
    let config = app.path().app_config_dir().map_err(|e| e.to_string())?;
    let data = app.path().app_data_dir().map_err(|e| e.to_string())?;
    save(&data.join("model.env"), env_file(&config, &data).as_deref(), input)?;
    crate::connect(app);
    let _ = window.close();
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn remote_or_wrong_window_cannot_read_or_write_credentials() {
        assert!(trusted("model-settings", &"tauri://localhost/model-settings.html".parse().unwrap()));
        for (label, url) in [("live", "http://127.0.0.1:1234/model-settings.html"),
            ("model-settings", "https://example.test/model-settings.html"),
            ("live", "tauri://localhost/model-settings.html"),
            ("model-settings", "tauri://localhost/index.html")] {
            assert!(!trusted(label, &url.parse().unwrap()));
        }
    }

    #[test]
    fn private_atomic_settings_round_trip_and_never_return_the_key() {
        let root = std::env::temp_dir().join(format!("vortoone-model-test-{}", std::process::id()));
        let target = root.join("model.env");
        fs::create_dir_all(&root).unwrap();
        let input = |key: &str, clear_key| ModelInput { base_url: "http://localhost:11434/v1".into(),
            model: "local-model".into(), api_key: key.into(), clear_key };
        save(&target, None, input("key#part$HOME\\\"", false)).unwrap();
        let response = serde_json::to_string(&view(Some(&target))).unwrap();
        assert!(!response.contains("key#part") && response.contains("\"hasKey\":true"));
        assert_eq!(values(Some(&target))["OPENAI_API_KEY"], "key#part$HOME\\\"");
        save(&target, Some(&target), input("", false)).unwrap();
        assert!(view(Some(&target)).has_key);
        save(&target, Some(&target), input("", true)).unwrap();
        assert!(!view(Some(&target)).has_key);
        assert!(save(&target, None, input("injected\nPATH=evil", false)).is_err());
        #[cfg(unix)] {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(fs::metadata(&target).unwrap().permissions().mode() & 0o777, 0o600);
        }
        fs::remove_dir_all(root).unwrap();
    }
}
