//! Native, user-initiated stable updates. The webview has no updater permission.

use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;
use tauri::{AppHandle, Manager};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
use tauri_plugin_updater::UpdaterExt;

#[derive(Default)]
pub struct UpdateGate(AtomicBool);

pub fn check(app: AppHandle) {
    if app.state::<UpdateGate>().0.swap(true, Ordering::AcqRel) {
        return;
    }
    tauri::async_runtime::spawn(async move {
        if let Err(message) = check_and_install(&app).await {
            let _ = update_dialog(&app, message, false).await;
        }
        app.state::<UpdateGate>().0.store(false, Ordering::Release);
    });
}

async fn check_and_install(app: &AppHandle) -> Result<(), &'static str> {
    let updater = app
        .updater_builder()
        .configure_client(|client| {
            client
                .connect_timeout(Duration::from_secs(15))
                .read_timeout(Duration::from_secs(30))
        })
        .build()
        .map_err(|_| "更新组件不可用，请打开下载页查看最新版本。")?;

    let update = updater
        .check()
        .await
        .map_err(|_| "无法检查更新，请稍后重试。")?;
    let Some(update) = update else {
        update_dialog(app, "VortoOne 已经是最新版本。", false).await?;
        return Ok(());
    };
    let message = format!(
        "下载并安装 VortoOne {}？\n\n安装后 App 会自动重启，本机正在进行的对话会中断。",
        update.version,
    );
    if !update_dialog(app, message, true).await? {
        return Ok(());
    }

    // The plugin authenticates the downloaded bytes with the pinned public key
    // before replacing the application.
    update
        .download_and_install(|_, _| {}, || {})
        .await
        .map_err(|_| "无法下载、验签或安装更新，请稍后重试或从下载页安装。")?;
    app.state::<crate::connector::ConnectorManager>().stop();
    app.state::<crate::backend::Backend>().stop();
    app.restart();
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
