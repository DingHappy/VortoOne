//! Native stable updates. Installation always requires user confirmation.

use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;
use tauri::{AppHandle, Manager};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
use tauri_plugin_updater::{Update, UpdaterExt};

#[cfg(target_os = "windows")]
const PORTABLE_UPDATE: (&str, &str) = ("windows-x86_64-nsis", "windows-x64.exe");
#[cfg(target_os = "linux")]
const PORTABLE_UPDATE: (&str, &str) = ("linux-x86_64-deb", "ubuntu-amd64.deb");

#[cfg(any(target_os = "windows", target_os = "linux"))]
enum BackendResume {
    Idle,
    Background,
    Workspace,
}

#[derive(Default)]
pub struct UpdateGate {
    checking: AtomicBool,
    #[cfg(any(target_os = "windows", target_os = "linux"))]
    stopped_processes: std::sync::Mutex<Option<BackendResume>>,
}

pub fn supported() -> bool {
    #[cfg(target_os = "macos")]
    {
        true
    }
    #[cfg(target_os = "windows")]
    {
        cfg!(target_arch = "x86_64")
            && matches!(
                tauri::utils::platform::bundle_type(),
                Some(tauri::utils::config::BundleType::Nsis)
            )
    }
    #[cfg(target_os = "linux")]
    {
        crate::deb_update::is_deb_bundle()
    }
}

pub fn check(app: AppHandle, manual: bool) {
    if app
        .state::<UpdateGate>()
        .checking
        .swap(true, Ordering::AcqRel)
    {
        return;
    }
    tauri::async_runtime::spawn(async move {
        let result = match find_update(&app).await {
            Ok(Some(update)) => install_update(&app, update).await,
            Ok(None) if manual => update_dialog(&app, "VortoOne 已经是最新版本。", false)
                .await
                .map(|_| ()),
            Err(message) if manual => Err(message),
            _ => Ok(()),
        };
        if let Err(message) = result {
            let _ = update_dialog(&app, message, false).await;
        }
        app.state::<UpdateGate>()
            .checking
            .store(false, Ordering::Release);
    });
}

async fn find_update(app: &AppHandle) -> Result<Option<Update>, &'static str> {
    let builder = app.updater_builder().configure_client(|client| {
        client
            .https_only(true)
            .connect_timeout(Duration::from_secs(15))
            .read_timeout(Duration::from_secs(30))
    });
    #[cfg(any(target_os = "windows", target_os = "linux"))]
    let builder = builder.target(PORTABLE_UPDATE.0);
    #[cfg(target_os = "windows")]
    let builder = builder.on_before_exit({
        let app = app.clone();
        // The updater exits directly after launching the native installer.
        move || stop_managed_processes(&app)
    });

    let updater = builder
        .build()
        .map_err(|_| "更新组件不可用，请打开下载页查看最新版本。")?;
    let update = updater
        .check()
        .await
        .map_err(|_| "无法检查更新，请稍后重试。")?;

    if let Some(update) = &update {
        if update.download_url.scheme() != "https" {
            return Err("更新包地址无效，请从下载页安装。");
        }
        #[cfg(any(target_os = "windows", target_os = "linux"))]
        if !portable_asset_matches(
            &update.raw_json,
            &update.download_url,
            &update.version,
            PORTABLE_UPDATE,
        ) {
            return Err("更新包与已安装的 VortoOne 不匹配，请从下载页安装。");
        }
    }
    Ok(update)
}

#[cfg(any(target_os = "windows", target_os = "linux", test))]
fn portable_asset_matches(
    manifest: &serde_json::Value,
    url: &tauri::Url,
    version: &str,
    (target, suffix): (&str, &str),
) -> bool {
    // A generic manifest entry could describe a different kind of installer.
    manifest["platforms"][target].is_object()
        && url.scheme() == "https"
        && url.path_segments().and_then(|mut parts| parts.next_back())
            == Some(format!("VortoOne_{version}_{suffix}").as_str())
}

async fn install_update(app: &AppHandle, update: Update) -> Result<(), &'static str> {
    #[cfg(target_os = "linux")]
    let instructions =
        "安装时需要系统管理员授权。本机正在进行的对话会中断，安装完成后 App 会自动重启。";
    #[cfg(not(target_os = "linux"))]
    let instructions = "安装后 App 会自动重启，本机正在进行的对话会中断。";
    let message = format!("下载并安装 VortoOne {}？\n\n{instructions}", update.version);
    if !update_dialog(app, message, true).await? {
        return Ok(());
    }

    // The updater authenticates the bytes and version against the stable channel key.
    let bytes = update
        .download(|_, _| {}, || {})
        .await
        .map_err(|_| "无法下载或验签更新，请稍后重试或从下载页安装。")?;

    let handle = app.clone();
    tauri::async_runtime::spawn_blocking(move || apply_update(&handle, update, bytes))
        .await
        .map_err(|_| "无法完成安装，请稍后重试或从下载页安装。")?
}

#[cfg(target_os = "macos")]
fn apply_update(app: &AppHandle, update: Update, bytes: Vec<u8>) -> Result<(), &'static str> {
    update
        .install(bytes)
        .map_err(|_| "无法安装更新，请稍后重试或从下载页安装。")?;
    restart_after_install(app);
}

#[cfg(target_os = "linux")]
fn apply_update(app: &AppHandle, update: Update, bytes: Vec<u8>) -> Result<(), &'static str> {
    use crate::deb_update::InstallOutcome;
    let result = {
        let gate = app.state::<crate::ConnectionGate>();
        let _guard = lock_for_portable_install(app, &gate)?;
        let outcome = crate::deb_update::install_verified_deb(&bytes, &update.version, || {
            stop_managed_processes(app)
        });
        if matches!(outcome, Ok(InstallOutcome::Installed)) {
            app.restart();
        }
        outcome
    };
    restore_managed_processes(app);
    result.map(|_| ())
}

#[cfg(target_os = "macos")]
fn restart_after_install(app: &AppHandle) -> ! {
    app.state::<crate::connector::ConnectorManager>().stop();
    app.state::<crate::backend::Backend>().stop();
    app.restart();
}

#[cfg(any(target_os = "windows", target_os = "linux"))]
fn stop_managed_processes(app: &AppHandle) {
    let backend = app.state::<crate::backend::Backend>();
    let resume = match backend.local_port() {
        Some(port) if app.state::<crate::LiveAddress>().port() == port => BackendResume::Workspace,
        Some(_) => BackendResume::Background,
        None => BackendResume::Idle,
    };
    if let Ok(mut saved) = app.state::<UpdateGate>().stopped_processes.lock() {
        *saved = Some(resume);
    }
    app.state::<crate::connector::ConnectorManager>().stop();
    backend.stop();
}

#[cfg(any(target_os = "windows", target_os = "linux"))]
fn restore_managed_processes(app: &AppHandle) {
    let resume = app
        .state::<UpdateGate>()
        .stopped_processes
        .lock()
        .ok()
        .and_then(|mut saved| saved.take());
    if let Some(resume) = resume {
        app.state::<crate::connector::ConnectorManager>()
            .reopen_after_failed_update();
        crate::connector::resume(app.clone());
        match resume {
            BackendResume::Workspace => crate::start_backend(app.clone(), true),
            BackendResume::Background => crate::start_backend(app.clone(), false),
            BackendResume::Idle => {}
        }
    }
}

#[cfg(target_os = "windows")]
fn apply_update(app: &AppHandle, update: Update, bytes: Vec<u8>) -> Result<(), &'static str> {
    if !bytes.starts_with(b"MZ") {
        return Err("更新包与已安装的 VortoOne 不匹配，请从下载页安装。");
    }
    let result = {
        let gate = app.state::<crate::ConnectionGate>();
        let _guard = lock_for_portable_install(app, &gate)?;
        update.install(bytes)
    };
    // A successful launch never returns on Windows. If it failed after our
    // pre-exit hook ran, bring back the processes and keep the current window.
    if result.is_err() {
        restore_managed_processes(app);
    }
    result.map_err(|_| "无法启动更新安装程序。请稍后重试或从下载页安装。")
}

#[cfg(any(target_os = "windows", target_os = "linux"))]
fn lock_for_portable_install<'a>(
    app: &AppHandle,
    gate: &'a crate::ConnectionGate,
) -> Result<std::sync::MutexGuard<'a, ()>, &'static str> {
    let guard = gate
        .0
        .try_lock()
        .map_err(|_| "本机服务正在启动，请稍后安装更新。")?;
    if app
        .state::<crate::shell::ShellState>()
        .is_installing_local_service()
    {
        return Err("本机服务正在设置，请稍后安装更新。");
    }
    Ok(guard)
}

async fn update_dialog(
    app: &AppHandle,
    message: impl Into<String>,
    confirm: bool,
) -> Result<bool, &'static str> {
    let app = app.clone();
    let message = message.into();
    tauri::async_runtime::spawn_blocking(move || {
        let mut dialog = app
            .dialog()
            .message(message)
            .title("VortoOne 更新")
            .kind(MessageDialogKind::Info);
        if let Some(window) = [crate::LIVE, "welcome", "main"]
            .iter()
            .find_map(|label| app.get_webview_window(label))
        {
            dialog = dialog.parent(&window);
        }
        if confirm {
            dialog = dialog.buttons(MessageDialogButtons::OkCancelCustom(
                "更新并重启".into(),
                "稍后".into(),
            ));
        }
        dialog.blocking_show()
    })
    .await
    .map_err(|_| "无法打开更新窗口。")
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn portable_updates_require_a_matching_static_target_and_direct_installer() {
        for (target, suffix) in [
            ("windows-x86_64-nsis", "windows-x64.exe"),
            ("linux-x86_64-deb", "ubuntu-amd64.deb"),
        ] {
            let asset = format!("VortoOne_0.3.3_{suffix}");
            let manifest = json!({"platforms": {(target): {"signature": "signature"}}});
            let url = |scheme: &str, name: &str| {
                tauri::Url::parse(&format!("{scheme}://release.example/download/{name}")).unwrap()
            };
            let matches = |manifest: &serde_json::Value, url: &tauri::Url| {
                portable_asset_matches(manifest, url, "0.3.3", (target, suffix))
            };
            assert!(matches(&manifest, &url("https", &asset)));
            assert!(!matches(&manifest, &url("http", &asset)));
            assert!(!matches(&manifest, &url("https", &format!("{asset}.zip"))));
            assert!(!matches(
                &manifest,
                &url("https", &format!("VortoOne_0.3.2_{suffix}"))
            ));
            assert!(!matches(
                &json!({"url": url("https", &asset).as_str()}),
                &url("https", &asset)
            ));
        }
    }
}
