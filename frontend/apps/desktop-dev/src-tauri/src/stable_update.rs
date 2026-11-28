//! Native stable updates. Installation always requires user confirmation.

use crate::gray_update::{
    check_for_standard_update, CheckOutcome, Managed, Report, RolloutCheck, POLICY_ERROR,
};
use crate::update_progress::{Phase, UpdateProgress};
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc,
};
use std::time::{Duration, Instant};
use tauri::{AppHandle, Manager};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
use tauri_plugin_updater::{Update, Updater, UpdaterExt};

const MIN_PHASE_VISIBILITY: Duration = Duration::from_millis(350);

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
    checking: Arc<AtomicBool>,
    #[cfg(any(target_os = "windows", target_os = "linux"))]
    stopped_processes: std::sync::Mutex<Option<BackendResume>>,
}

pub struct UpdateOperation(Arc<AtomicBool>);

impl Drop for UpdateOperation {
    fn drop(&mut self) {
        self.0.store(false, Ordering::Release);
    }
}

pub fn begin(app: &AppHandle) -> Result<UpdateOperation, &'static str> {
    let checking = &app.state::<UpdateGate>().inner().checking;
    if checking.swap(true, Ordering::AcqRel) {
        return Err("另一个版本操作正在进行，请稍后重试。");
    }
    Ok(UpdateOperation(checking.clone()))
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
    let Ok(guard) = begin(&app) else { return };
    tauri::async_runtime::spawn(async move {
        check_with_guard(&app, manual, guard).await;
    });
}

pub async fn check_from_versions(app: &AppHandle) -> Result<CheckOutcome, &'static str> {
    let _guard = begin(app)?;
    let Some(managed) = Managed::read(app)? else {
        return Ok(CheckOutcome::EnrollmentInvalid);
    };
    match find_managed_update(app, &managed).await? {
        RolloutCheck::NoOffer(outcome) => Ok(outcome),
        RolloutCheck::Offer(update) => {
            let installed = install_update(app, update, Some(&managed)).await?;
            Ok(if installed {
                CheckOutcome::Installing
            } else {
                CheckOutcome::Declined
            })
        }
    }
}

async fn check_with_guard(app: &AppHandle, manual: bool, _guard: UpdateOperation) {
    if let Err(message) = run_check(app, manual).await {
        let _ = update_dialog(app, message, false).await;
    }
}

async fn run_check(app: &AppHandle, manual: bool) -> Result<(), &'static str> {
    let managed = match check_for_standard_update(app).await {
        Ok(managed) => managed,
        Err(error) if manual => return Err(error),
        Err(_) => return Ok(()),
    };
    let (device, legacy, update) = match managed {
        Some(managed) => {
            let legacy = managed.legacy;
            let update = match restore_managed_update(app, managed.outcome) {
                Ok(RolloutCheck::Offer(update)) => Ok(Some(update)),
                Ok(RolloutCheck::NoOffer(CheckOutcome::EnrollmentInvalid)) if legacy => {
                    Err(POLICY_ERROR)
                }
                Ok(RolloutCheck::NoOffer(CheckOutcome::EnrollmentInvalid)) => {
                    Err("暂时无法检查更新，请稍后重试。")
                }
                Ok(RolloutCheck::NoOffer(_)) => Ok(None),
                Err(error) => Err(error),
            };
            (Some(managed.device), legacy, update)
        }
        None => (None, false, find_public_update(app).await),
    };
    match update {
        Ok(Some(update)) => install_update(app, update, device.as_ref())
            .await
            .map(|_| ()),
        Ok(None) if manual => {
            let message = if legacy {
                "这台设备目前没有可用的灰度更新。"
            } else {
                "VortoOne 已经是最新版本。"
            };
            update_dialog(app, message, false).await.map(|_| ())
        }
        Err(message) if manual => Err(message),
        _ => Ok(()),
    }
}

fn build_updater(app: &AppHandle, target: Option<&str>) -> Result<Updater, &'static str> {
    let builder = app.updater_builder().configure_client(|client| {
        client
            .https_only(true)
            .connect_timeout(Duration::from_secs(15))
            .read_timeout(Duration::from_secs(30))
    });
    let builder = match target {
        Some(target) => builder.target(target),
        None => builder,
    };
    #[cfg(target_os = "windows")]
    let builder = builder.on_before_exit({
        let app = app.clone();
        // The updater exits directly after launching the native installer.
        move || stop_managed_processes(&app)
    });

    builder
        .build()
        .map_err(|_| "更新组件不可用，请打开下载页查看最新版本。")
}

async fn find_managed_update(
    app: &AppHandle,
    managed: &Managed,
) -> Result<RolloutCheck<Update>, &'static str> {
    restore_managed_update(app, managed.check(app).await?)
}

fn restore_managed_update(
    app: &AppHandle,
    outcome: RolloutCheck<serde_json::Value>,
) -> Result<RolloutCheck<Update>, &'static str> {
    let manifest = match outcome {
        RolloutCheck::Offer(manifest) => manifest,
        RolloutCheck::NoOffer(outcome) => return Ok(RolloutCheck::NoOffer(outcome)),
    };
    let updater = build_updater(app, Some(crate::gray_update::TARGET.0))?;
    let update = updater
        .restore_update(manifest)
        .map_err(|_| "灰度更新元数据无效，请联系发布管理员。")?;
    Ok(match update {
        Some(update) => RolloutCheck::Offer(update),
        None => RolloutCheck::NoOffer(CheckOutcome::NoUpdate),
    })
}

async fn find_public_update(app: &AppHandle) -> Result<Option<Update>, &'static str> {
    #[cfg(target_os = "macos")]
    let target = None;
    #[cfg(any(target_os = "windows", target_os = "linux"))]
    let target = Some(PORTABLE_UPDATE.0);
    let updater = build_updater(app, target)?;
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

async fn install_update(
    app: &AppHandle,
    update: Update,
    managed: Option<&Managed>,
) -> Result<bool, &'static str> {
    #[cfg(target_os = "linux")]
    let instructions =
        "安装时需要系统管理员授权。本机正在进行的对话会中断，安装完成后 App 会自动重启。";
    #[cfg(not(target_os = "linux"))]
    let instructions = "安装后 App 会自动重启，本机正在进行的对话会中断。";
    let message = format!("下载并安装 VortoOne {}？\n\n{instructions}", update.version);
    if !update_dialog(app, message, true).await? {
        if let Some(managed) = managed {
            let _ = managed.report(&update.version, Report::Declined).await;
        }
        return Ok(false);
    }

    let version = update.version.clone();
    let progress = UpdateProgress::open(app, &version);
    let mut verification_started = Instant::now();
    let mut show_verifying = || {
        progress.show_phase(Phase::Verifying);
        verification_started = Instant::now();
    };
    // The updater authenticates the bytes and version against the stable channel key.
    let bytes = if let Some(managed) = managed {
        let verified = managed
            .download(&version, &update.download_url, |received, total| {
                progress.downloaded(received, total);
            })
            .await
            .and_then(|bytes| {
                show_verifying();
                update
                    .verify(&bytes)
                    .map_err(|_| "灰度更新验签失败，安装已停止。")?;
                Ok(bytes)
            });
        match verified {
            Ok(bytes) => bytes,
            Err(error) => {
                let _ = managed.report(&version, Report::Failed).await;
                return Err(error);
            }
        }
    } else {
        let mut received = 0_u64;
        update
            .download(
                |chunk, total| {
                    received = received.saturating_add(chunk as u64);
                    progress.downloaded(
                        received,
                        total.filter(|size| *size > 0 && *size >= received),
                    );
                },
                show_verifying,
            )
            .await
            .map_err(|_| "无法下载或验签更新，请稍后重试或从下载页安装。")?
    };

    // The signature check may finish before the progress view's next frame.
    let wait = phase_time_left(verification_started);
    if !wait.is_zero() {
        let _ = tauri::async_runtime::spawn_blocking(move || std::thread::sleep(wait)).await;
    }
    if let Some(managed) = managed {
        managed.report(&version, Report::Installing).await?;
    }
    progress.show_phase(Phase::Installing);
    let handle = app.clone();
    let install_progress = progress.clone();
    let result = tauri::async_runtime::spawn_blocking(move || {
        apply_update(&handle, update, bytes, &install_progress)
    })
    .await
    .unwrap_or(Err("无法完成安装，请稍后重试。"));
    if let Some(managed) = managed {
        match result {
            Err(_) => {
                let _ = managed.report(&version, Report::Failed).await;
            }
            Ok(false) => {
                let _ = managed.report(&version, Report::Declined).await;
            }
            Ok(true) => {}
        }
    }
    result
}

fn phase_time_left(since: Instant) -> Duration {
    MIN_PHASE_VISIBILITY.saturating_sub(since.elapsed())
}

#[cfg(target_os = "macos")]
fn apply_update(
    app: &AppHandle,
    update: Update,
    bytes: Vec<u8>,
    progress: &UpdateProgress,
) -> Result<bool, &'static str> {
    let installing_since = Instant::now();
    update
        .install(bytes)
        .map_err(|_| "无法安装更新，请稍后重试或从下载页安装。")?;
    restart_after_install(app, progress, installing_since);
}

#[cfg(target_os = "linux")]
fn apply_update(
    app: &AppHandle,
    update: Update,
    bytes: Vec<u8>,
    progress: &UpdateProgress,
) -> Result<bool, &'static str> {
    use crate::deb_update::InstallOutcome;
    let installing_since = Instant::now();
    let result = {
        let gate = app.state::<crate::ConnectionGate>();
        let _guard = lock_for_portable_install(app, &gate)?;
        let outcome = crate::deb_update::install_verified_deb(&bytes, &update.version, || {
            stop_managed_processes(app)
        });
        if matches!(outcome, Ok(InstallOutcome::Installed)) {
            restart_after_install(app, progress, installing_since);
        }
        outcome
    };
    restore_managed_processes(app);
    result.map(|outcome| matches!(outcome, InstallOutcome::Installed))
}

#[cfg(any(target_os = "macos", target_os = "linux"))]
fn restart_after_install(
    app: &AppHandle,
    progress: &UpdateProgress,
    installing_since: Instant,
) -> ! {
    std::thread::sleep(phase_time_left(installing_since));
    progress.show_phase(Phase::Restarting);
    let restarting_since = Instant::now();
    #[cfg(target_os = "macos")]
    {
        app.state::<crate::connector::ConnectorManager>().stop();
        app.state::<crate::backend::Backend>().stop();
    }
    std::thread::sleep(phase_time_left(restarting_since));
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
fn apply_update(
    app: &AppHandle,
    update: Update,
    bytes: Vec<u8>,
    _progress: &UpdateProgress,
) -> Result<bool, &'static str> {
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
    result
        .map(|_| true)
        .map_err(|_| "无法启动更新安装程序。请稍后重试或从下载页安装。")
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
        if let Some(window) = ["versions", crate::LIVE, "welcome", "main"]
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
