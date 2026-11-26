#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod backend;
mod places;
mod reach;
mod shell;
mod model_settings;
mod connector;
mod connector_secrets;
#[cfg(target_os = "linux")]
mod deb_update;
#[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
mod stable_update;

use tauri::{
    menu::{Menu, SubmenuBuilder},
    webview::{DownloadEvent, NewWindowResponse},
    Manager, WebviewUrl, WebviewWindowBuilder,
};

const RELEASES: &str = "https://github.com/DingHappy/VortoOne/releases";
const STABLE: &str = "https://github.com/DingHappy/VortoOne/releases/latest";

fn is_stable_channel(config: &tauri::utils::config::Config) -> bool {
    config.identifier == "com.vorto.vortoone"
}

fn desktop_menu_name(config: &tauri::utils::config::Config) -> &'static str {
    if is_stable_channel(config) { "工作区" } else { "开发版" }
}

/// 关窗口时只藏起来的窗口：macOS 上的主窗口和欢迎窗口（App 照常留在 Dock 里）。其它系统照旧关掉。
fn hides_on_close(label: &str) -> bool {
    cfg!(target_os = "macos") && (label == LIVE || label == "welcome")
}

fn desktop_window<'a>(
    app: &'a tauri::AppHandle,
    label: &str,
    url: WebviewUrl,
) -> WebviewWindowBuilder<'a, tauri::Wry, tauri::AppHandle> {
    let window = WebviewWindowBuilder::new(app, label, url);
    // WKWebView supports separate persistent stores on macOS 14 and later.
    // Keep a single stable store across this app's windows and releases.
    if cfg!(target_os = "macos") && is_stable_channel(app.config()) {
        return window.data_store_identifier([
            0x05, 0xd6, 0x30, 0x93, 0x9c, 0x2e, 0x4f, 0x95,
            0xb8, 0x08, 0x64, 0xe0, 0x64, 0xb5, 0xdd, 0x17,
        ]);
    }
    window
}

fn computer_download(url: &tauri::Url) -> bool {
    url.query().is_none() && url.fragment().is_none()
        && url.path().strip_prefix("/api/computers/downloads/").is_some_and(|id| {
            id.len() == 32 && id.bytes().all(|byte| byte.is_ascii_digit() || matches!(byte, b'a'..=b'f'))
        })
}
#[derive(Default)]
struct ConnectionGate(std::sync::Mutex<()>);
/// 主窗口现在打开的服务（scheme://host[:port]）。导航闸据此放行：只在这个服务自己的页面里走，别的交给系统浏览器。
/// 本机服务另记端口，盯授权请求的线程据此知道后端换没换。
#[derive(Clone, Default)]
struct LiveAddress(std::sync::Arc<std::sync::Mutex<(String, u16)>>);

impl LiveAddress {
    fn set(&self, port: u16) { self.set_origin(&backend::origin(port), port); }
    fn set_origin(&self, origin: &str, local_port: u16) {
        if let Ok(mut slot) = self.0.lock() { *slot = (origin.to_string(), local_port); }
    }
    fn port(&self) -> u16 { self.0.lock().map(|slot| slot.1).unwrap_or(0) }
    fn origin(&self) -> String { self.0.lock().map(|slot| slot.0.clone()).unwrap_or_default() }
    fn allows(&self, url: &tauri::Url) -> bool {
        let current = self.origin();
        !current.is_empty() && url.username().is_empty() && url.password().is_none()
            && places::origin(url.as_str()).is_some_and(|o| o == current)
    }
}

fn packaged_url(url: &tauri::Url) -> bool {
    (url.scheme() == "tauri" && url.host_str() == Some("localhost"))
        || (url.scheme() == "http" && url.host_str() == Some("tauri.localhost"))
        || url.as_str() == "about:blank"
}

fn show(
    app: &tauri::AppHandle,
    label: &str,
    path: &str,
    title: &str,
    width: f64,
    height: f64,
) -> tauri::Result<()> {
    if let Some(window) = app.get_webview_window(label) {
        window.show()?;
        window.set_focus()?;
        return Ok(());
    }
    desktop_window(app, label, WebviewUrl::App(path.into()))
        .title(title)
        .inner_size(width, height)
        .min_inner_size(900.0_f64.min(width), 600.0_f64.min(height))
        .center()
        .on_navigation(packaged_url)
        .on_new_window(|_, _| NewWindowResponse::Deny)
        .build()?;
    Ok(())
}

const LIVE: &str = "live";
const SETUP_MODEL_PATH: &str = "/onboarding/native-model-settings";

fn setup_model_navigation(source: &tauri::Url, target: &tauri::Url, live_origin: &str, managed_origin: &str) -> bool {
    source.path() == "/onboarding"
        && target.path() == SETUP_MODEL_PATH
        && target.query().is_none() && target.fragment().is_none()
        && live_origin == managed_origin
        && places::origin(source.as_str()).as_deref() == Some(managed_origin)
        && places::origin(target.as_str()).as_deref() == Some(managed_origin)
}

fn open_setup_model_settings(app: &tauri::AppHandle, target: &tauri::Url) {
    let Some(managed) = app.state::<backend::Backend>().local_origin() else { return; };
    let Some(live) = app.get_webview_window(LIVE) else { return; };
    let Ok(source) = live.url() else { return; };
    let Ok(data) = app.path().app_data_dir() else { return; };
    if setup_model_navigation(&source, target, &app.state::<LiveAddress>().origin(), &managed)
        && places::desktop_setup_at_model(&data.join("home")) {
        if let Err(error) = show(app, "model-settings", "model-settings.html", "连接模型", 600.0, 650.0) {
            eprintln!("无法打开模型配置窗口: {error}");
        }
    }
}

fn live_window_capability(port: u16) -> tauri::ipc::CapabilityBuilder {
    live_window_capability_for(&backend::origin(port), &format!("dev-live-window-{port}"))
}

/// 主窗口里服务自己的页面只能拖动、双击标题栏放大窗口；拿不到任何宿主命令。
fn live_window_capability_for(origin: &str, id: &str) -> tauri::ipc::CapabilityBuilder {
    tauri::ipc::CapabilityBuilder::new(id)
        .local(false)
        .remote(format!("{origin}/*"))
        .webview(LIVE)
        .permission("core:window:allow-start-dragging")
        .permission("core:window:allow-internal-toggle-maximize")
}

/// 优先启动随包后端；源码模式只供未打包运行时的开发环境使用。
/// 在后台线程里跑：后端冷启动要几秒到几十秒，不能卡住界面。
fn connect(app: tauri::AppHandle) {
    start_backend(app, true);
}

fn start_backend(app: tauri::AppHandle, open_workspace: bool) {
    std::thread::spawn(move || {
        if let Err(error) = start_backend_now(&app, open_workspace) {
            eprintln!("连接后端失败: {error}");
            let message = format!("{} · 没有连上后端：{error}", app.package_info().name);
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.set_title(&message);
            }
        }
    });
}

fn connect_now(app: &tauri::AppHandle) -> Result<(), String> {
    start_backend_now(app, true)
}

fn start_backend_now(app: &tauri::AppHandle, open_workspace: bool) -> Result<(), String> {
    let gate = app.state::<ConnectionGate>();
    let _guard = gate.0.lock().map_err(|_| "本机服务状态不可用，请稍后重试。")?;
    let config_dir = app.path().app_config_dir().map_err(|e| e.to_string())?;
    let data = app.path().app_data_dir().map_err(|e| e.to_string())?;
    let resources = app.path().resource_dir().map_err(|e| e.to_string())?;
    let ui = resources.join("web");
    let runtime = resources.join("runtime").join(backend::BUNDLED_RUNTIME);
    let env_file = model_settings::env_file(&config_dir, &data);
    let config = if runtime.is_file() { None } else {
        let Ok(text) = std::fs::read_to_string(config_dir.join(backend::CONFIG_FILE)) else { return Ok(()); };
        Some(backend::parse_config(&text)?)
    };
    std::fs::create_dir_all(&data).map_err(|e| e.to_string())?;
    app.state::<backend::Backend>().stop();
    shell::set_phase(app, "starting");
    let key = backend::owner_key(&data.join(places::OWNER_KEY_FILE))?;
    let started = if let Some(config) = config {
        backend::start(&config, &data.join("home"), &ui, &data.join("backend.log"), &key)
    } else {
        backend::start_bundled(
            &runtime, env_file.as_deref(), &data.join("home"), &ui, &data.join("backend.log"), &key,
            &app.package_info().version.to_string(),
        )
    };
    let (mut child, port, token, native) = match started {
        Ok(found) => found,
        Err(error) => {
            shell::set_phase(app, "failing");
            return Err(error);
        }
    };
    if let Err(error) = shell::local_started(app, port) {
        let _ = child.kill();
        let _ = child.wait();
        return Err(error);
    }
    app.state::<backend::Backend>().install(child, native)?;
    if !open_workspace {
        connector::resume(app.clone());
        return Ok(());
    }
    let outcome = (|| {
        let url: tauri::Url = backend::sign_in_url(port, &token)?.parse().map_err(|_| "登录地址无效".to_string())?;
        app.add_capability(live_window_capability(port)).map_err(|e| e.to_string())?;
        app.state::<LiveAddress>().set(port);
        watch_consents(app.clone(), port, token.clone());
        show_live(app, url)
    })();
    if outcome.is_ok() { connector::resume(app.clone()); }
    else { app.state::<backend::Backend>().stop(); }
    outcome
}

fn desktop_titlebar_script() -> String {
    let platform = if cfg!(target_os = "macos") {
        "mac"
    } else {
        std::env::consts::OS
    };
    let script = include_str!("../../desktop-titlebar.js");
    format!("((platform) => {{\n{script}\n}})({platform:?});")
}

/// 主窗口打开 url（本机服务的交接登录地址，或别处服务的页面）。已开着就原地导航。
fn show_live(app: &tauri::AppHandle, url: tauri::Url) -> Result<(), String> {
    let handle = app.clone();
    app.run_on_main_thread(move || {
        if let Some(window) = handle.get_webview_window(LIVE) {
            // 同名 WebView 销毁后的异步清理会影响刚重建的窗口；重连只导航现有窗口。
            // 导航闸读取当前端口，仍不允许旧服务或其它本机地址进入主窗口。
            if let Err(error) = window.navigate(url) {
                eprintln!("无法重新连接主窗口: {error}");
            }
            let _ = window.show();
            let _ = window.set_focus();
            return;
        }
        let address = handle.state::<LiveAddress>().inner().clone();
        let download_address = address.clone();
        let setup_handle = handle.clone();
        let builder = desktop_window(&handle, LIVE, WebviewUrl::External(url))
            .title(&handle.package_info().name)
            .initialization_script(desktop_titlebar_script())
            .inner_size(1440.0, 960.0)
            .min_inner_size(900.0, 600.0)
            .center()
            // 窗口只在后端自己的地址里走；外部链接交给系统浏览器
            .on_navigation(move |target| {
                if target.path() == SETUP_MODEL_PATH && address.allows(target) {
                    // WebView2 must finish this navigation callback before opening another window.
                    let app = setup_handle.clone();
                    let target = target.clone();
                    tauri::async_runtime::spawn_blocking(move || open_setup_model_settings(&app, &target));
                    return false;
                }
                if address.allows(target) {
                    return true;
                }
                if matches!(target.scheme(), "https" | "http") {
                    let _ = tauri_plugin_opener::open_url(target.as_str(), None::<&str>);
                }
                false
            })
            .on_new_window(|_, _| NewWindowResponse::Deny)
            .on_download(move |window, event| match event {
                DownloadEvent::Requested { url, destination } => {
                    download_address.allows(&url) && computer_download(&url)
                        && window.app_handle().path().download_dir().ok()
                            .is_some_and(|directory| destination.parent() == Some(directory.as_path()))
                        && !destination.exists()
                }
                _ => true,
            });
        #[cfg(target_os = "macos")]
        let builder = builder
            .title_bar_style(tauri::TitleBarStyle::Overlay)
            .hidden_title(true)
            .traffic_light_position(tauri::LogicalPosition::new(18.0, 18.0));
        let built = builder.build();
        match built {
            // 连上了：预览窗口让位（菜单里仍可打开）
            Ok(_) => {
                if let Some(preview) = handle.get_webview_window("main") {
                    let _ = preview.close();
                }
            }
            Err(error) => eprintln!("无法打开主窗口: {error}"),
        }
    })
    .map_err(|e| e.to_string())
}

const CONSENT: &str = "consent";

/// Claude Code 这类本机 AI 应用登录时会打开 `/autorizar` 确认页，可主人在系统浏览器里登录不了
/// （主人令牌只在本进程手里）。这里盯着后端：本机应用一发起授权，就在已登录的小窗口里打开确认页。
/// 点「允许」后页面跳往应用的回调地址，那是别的地址，交给系统浏览器送到应用手里，小窗口随即关掉。
/// 后端换了端口（重连）就停下，由新连接自己的线程接着看。
fn watch_consents(app: tauri::AppHandle, port: u16, token: String) {
    std::thread::spawn(move || {
        let mut opened = std::collections::HashSet::new();
        loop {
            std::thread::sleep(std::time::Duration::from_millis(1500));
            if app.state::<LiveAddress>().port() != port {
                return;
            }
            for id in backend::consent_requests(port, &token) {
                if opened.insert(id.clone()) {
                    open_consent(&app, port, &id);
                }
            }
        }
    });
}

fn open_consent(app: &tauri::AppHandle, port: u16, id: &str) {
    let Ok(url) = backend::consent_url(port, id).parse::<tauri::Url>() else { return; };
    let handle = app.clone();
    let _ = app.run_on_main_thread(move || {
        if let Some(window) = handle.get_webview_window(CONSENT) {
            let _ = window.navigate(url);
            let _ = window.show();
            let _ = window.set_focus();
            return;
        }
        let closer = handle.clone();
        let built = desktop_window(&handle, CONSENT, WebviewUrl::External(url))
            .title("连接 AI 应用")
            .inner_size(560.0, 760.0)
            .center()
            .on_navigation(move |target| {
                if backend::own_page(target, port) {
                    return true;
                }
                // 应用的回调（本机回环或 cursor://）：交给系统去打开，确认页的事办完了
                if matches!(target.scheme(), "https" | "http" | "cursor") {
                    let _ = tauri_plugin_opener::open_url(target.as_str(), None::<&str>);
                    let later = closer.clone();
                    let _ = closer.run_on_main_thread(move || {
                        if let Some(window) = later.get_webview_window(CONSENT) {
                            let _ = window.close();
                        }
                    });
                }
                false
            })
            .on_new_window(|_, _| NewWindowResponse::Deny)
            .build();
        match built {
            Ok(window) => { let _ = window.set_focus(); }
            Err(error) => eprintln!("无法打开授权确认窗口: {error}"),
        }
    });
}

fn main() {
    let context = tauri::generate_context!();
    if std::env::args_os().any(|arg| arg == "--check-standalone") {
        #[cfg(any(target_os = "windows", target_os = "linux"))]
        if is_stable_channel(context.config()) && !stable_update::supported() {
            eprintln!("安装验收失败：正式版安装包缺少更新组件所需的安装类型。");
            std::process::exit(1);
        }
        let result = tauri::utils::platform::resource_dir(context.package_info(), &tauri::utils::Env::default())
            .map_err(|error| error.to_string())
            .and_then(|resources| backend::verify_standalone(&resources, &context.package_info().version.to_string()));
        match result {
            Ok(()) => println!("{{\"standalone\":true,\"freshProfile\":true,\"login\":true,\"ui\":true,\"inferenceSent\":false}}"),
            Err(error) => { eprintln!("安装验收失败：{error}"); std::process::exit(1); }
        }
        return;
    }
    let builder = tauri::Builder::default().plugin(tauri_plugin_dialog::init());
    #[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
    let builder = if is_stable_channel(context.config()) && stable_update::supported() {
        builder
            .manage(stable_update::UpdateGate::default())
            .plugin(tauri_plugin_updater::Builder::new().build())
    } else {
        builder
    };
    builder
        .manage(backend::Backend::default())
        .manage(ConnectionGate::default())
        .manage(LiveAddress::default())
        .manage(shell::ShellState::default())
        .manage(connector::ConnectorManager::default())
        .invoke_handler(tauri::generate_handler![
            model_settings::model_settings_get, model_settings::model_settings_save,
            connector::computers_get, connector::computer_pair_preview, connector::computer_pair_confirm,
            connector::computer_remove, connector::computers_open, connector::computer_folder_settings,
            connector::pair_link_take,
            connector::computer_browser_share,
            connector::computer_desktop_share,
            connector::task_browser_enabled, connector::task_browser_set,
            shell::servers_get, shell::server_choose, shell::server_remove, shell::server_sign_out, shell::workspaces_get,
            shell::welcome_mode_take, shell::welcome_add, shell::discover, shell::link_take, shell::open_external,
            shell::show_window, shell::hide_window, shell::app_info, shell::quit_app, shell::reveal_folder,
            shell::local_server_status, shell::local_server_install, shell::local_server_cancel, shell::local_server_start,
            shell::local_server_stop, shell::local_server_restart, shell::local_server_reach, shell::local_server_backup,
            shell::local_server_backups_set, shell::local_server_restore, shell::local_server_erase,
            shell::local_server_diagnostics, shell::tailscale_status, shell::tailscale_open, shell::keep_running,
            shell::runners_list, shell::pick_folder, shell::setup_state_get, shell::notch_info, shell::notch_place,
            shell::autostart_get, shell::update_install, shell::screen_access, shell::screen_request, shell::disk_access,
            shell::pair_ask, shell::runner_send, shell::runner_remove, shell::set_tray_state, shell::tray_visible,
            shell::notify, shell::update_cancel, shell::notch_set, shell::this_mac_reply, shell::app_menu_set,
            shell::this_mac_listening, shell::notify_site_set, shell::local_server_reach_stop, shell::local_server_usage,
            shell::local_server_sync, shell::open_system_settings, shell::privacy_open, shell::setup_state_set,
            shell::autostart_set, shell::network_ask, shell::runner_add, shell::pair_link_info, shell::site_fetch,
        ])
        // macOS：关掉主窗口或欢迎窗口只是藏起来，App 和本机服务照常在 Dock 里；点 Dock 图标再打开，⌘Q 才退出
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                if hides_on_close(window.label()) {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .setup(|app| {
            let stable = is_stable_channel(app.config());
            let menu = Menu::default(app.handle())?;
            let mut product_menu = SubmenuBuilder::new(app, desktop_menu_name(app.config()))
                .text("connect", "连接本机后端")
                .text("models", "模型连接…")
                .text("computers", "连接的电脑…")
                .separator();
            if !stable {
                product_menu = product_menu
                    .text("desktop", "桌面界面（示例数据）")
                    .text("characters", "史莱姆外观");
            }
            product_menu = product_menu
                .text("settings", "偏好设置…")
                .text("welcome", "添加或切换服务…")
                .text("move-out", "搬家：导出并停用这里…")
                .text("move-in", "从搬家包导入…");
            if !stable {
                product_menu = product_menu.text("preferences", "偏好设置预览（示例数据）");
            }
            product_menu = product_menu.separator().text("versions", "版本管理");
            if stable {
                #[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
                if stable_update::supported() {
                    product_menu = product_menu.text("check-update", "检查更新…");
                }
                product_menu = product_menu.text("stable", "下载最新版本…");
            } else {
                product_menu = product_menu
                    .text("stable", "公开稳定版下载…")
                    .text("releases", "管理 GitHub 发布…");
            }
            menu.append(&product_menu.build()?)?;
            app.set_menu(menu)?;
            app.add_capability(shell::capability())?;
            let bundled = app.path().resource_dir()?
                .join("runtime").join(backend::BUNDLED_RUNTIME).is_file();
            if stable && !bundled {
                return Err(std::io::Error::other("正式版缺少随包后端，请重新安装 VortoOne").into());
            }
            connector::resume(app.handle().clone());
            // 先打开哪个：上次选的服务；什么都还没选就是首次引导（HOME_AND_MEMBERS_DESIGN 第 4 节）
            let (config, data) = (app.path().app_config_dir()?, app.path().app_data_dir()?);
            let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_millis() as u64).unwrap_or(0);
            match places::opening(&places::read_servers(&config), &places::read_local(&config, &data), now) {
                places::Opening::Local => {
                    show(
                        app.handle(),
                        "main",
                        if bundled { "starting.html" } else { "index.html?lang=zh-Hans" },
                        if bundled { &app.package_info().name } else { "VortoOne Dev · 界面预览" },
                        1440.0,
                        960.0,
                    )?;
                    connect(app.handle().clone());
                }
                places::Opening::Remote(server) => {
                    if let Err(error) = shell::open_remote(app.handle(), &server, None) {
                        eprintln!("无法打开 {server}: {error}");
                        shell::open_window(app.handle(), "welcome").map_err(std::io::Error::other)?;
                    }
                }
                places::Opening::Welcome => shell::open_window(app.handle(), "welcome").map_err(std::io::Error::other)?,
            }
            #[cfg(any(target_os = "windows", target_os = "linux"))]
            if stable && stable_update::supported() {
                stable_update::check(app.handle().clone(), false);
            }
            Ok(())
        })
        .on_menu_event(|app, event| {
            let result = match event.id().as_ref() {
                #[cfg(any(target_os = "macos", target_os = "windows", target_os = "linux"))]
                "check-update" if is_stable_channel(app.config()) && stable_update::supported() => {
                    stable_update::check(app.clone(), true);
                    return;
                }
                "connect" => {
                    connect(app.clone());
                    return;
                }
                "models" => show(app, "model-settings", "model-settings.html", "连接模型", 600.0, 650.0),
                "computers" => {
                    if let Err(error) = connector::open(app) { eprintln!("{error}"); }
                    return;
                }
                "move-out" => {
                    shell::move_out(app.clone());
                    return;
                }
                "move-in" => {
                    shell::move_in(app.clone());
                    return;
                }
                "settings" | "welcome" => {
                    if let Err(error) = shell::open_window(app, if event.id().as_ref() == "settings" { "prefs" } else { "welcome" }) {
                        eprintln!("无法打开窗口: {error}");
                    }
                    return;
                }
                "desktop" if !is_stable_channel(app.config()) => show(
                    app,
                    "main",
                    "index.html?lang=zh-Hans",
                    "VortoOne Dev · 界面预览",
                    1440.0,
                    960.0,
                ),
                "characters" if !is_stable_channel(app.config()) => show(
                    app,
                    "characters",
                    "character-lab.html",
                    "VortoOne Dev · 史莱姆外观",
                    1280.0,
                    960.0,
                ),
                "preferences" if !is_stable_channel(app.config()) => show(
                    app,
                    "preferences",
                    "windows/index.html?window=prefs&scenario=selfhost&lang=zh-Hans&bare=1",
                    "VortoOne Dev · 偏好设置预览",
                    1100.0,
                    800.0,
                ),
                "versions" => show(
                    app,
                    "versions",
                    "versions.html",
                    &format!("{} · 版本管理", app.package_info().name),
                    900.0,
                    660.0,
                ),
                "stable" | "releases" => {
                    let url = if event.id().as_ref() == "stable" {
                        STABLE
                    } else {
                        RELEASES
                    };
                    if let Err(error) = tauri_plugin_opener::open_url(url, None::<&str>) {
                        eprintln!("无法打开发布页: {error}");
                    }
                    return;
                }
                _ => return,
            };
            if let Err(error) = result {
                eprintln!("无法打开 VortoOne 窗口: {error}");
            }
        })
        .build(context)
        .expect("VortoOne failed to start")
        .run(|app, event| match event {
            // 退出时带走后端；被强杀时由后端看监护进程 pid 自己退出
            tauri::RunEvent::Exit => {
                app.state::<connector::ConnectorManager>().stop();
                app.state::<backend::Backend>().stop();
            }
            // 点 Dock 图标而没有窗口在显示：把藏起来的主窗口（还没有就欢迎窗口）拿回来
            #[cfg(target_os = "macos")]
            tauri::RunEvent::Reopen { has_visible_windows: false, .. } => {
                if let Some(window) = [LIVE, "welcome"].into_iter().find_map(|label| app.get_webview_window(label)) {
                    let _ = window.show();
                    let _ = window.unminimize();
                    let _ = window.set_focus();
                }
            }
            // 点了 vortoone:// 链接（Info.plist 登记了这个协议；冷启动时也走这里）
            #[cfg(target_os = "macos")]
            tauri::RunEvent::Opened { urls } => {
                for url in urls {
                    shell::receive_link(app, url.as_str());
                }
            }
            _ => {}
        });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn closing_the_main_or_welcome_window_hides_it_on_macos_only() {
        for label in [LIVE, "welcome"] {
            assert_eq!(hides_on_close(label), cfg!(target_os = "macos"));
        }
        for label in ["prefs", "models", "computers", "versions"] {
            assert!(!hides_on_close(label));
        }
    }

    #[test]
    #[cfg(any(target_os = "macos", target_os = "linux", target_os = "windows"))]
    fn native_titlebar_script_receives_the_build_platform() {
        #[cfg(target_os = "macos")]
        let expected = "mac";
        #[cfg(target_os = "linux")]
        let expected = "linux";
        #[cfg(target_os = "windows")]
        let expected = "windows";
        assert!(desktop_titlebar_script().ends_with(&format!("}})({expected:?});")));
    }

    #[test]
    fn download_matches_only_a_transfer_id_without_parameters() {
        let id = "a".repeat(32);
        let prefix = "https://home.example/api/computers/downloads/";
        let url = tauri::Url::parse(&format!("{prefix}{id}")).unwrap();
        assert!(computer_download(&url));
        for tail in [format!("{id}/extra"), format!("{id}?next=1"), format!("{id}#preview"), "x".repeat(32)] {
            assert!(!computer_download(&tauri::Url::parse(&format!("{prefix}{tail}")).unwrap()));
        }
    }

    #[test]
    fn reconnect_navigation_uses_the_current_backend_and_rejects_the_old_port() {
        let address = LiveAddress::default();
        address.set(4321);
        let nav = address.clone();
        assert!(nav.allows(&"http://127.0.0.1:4321/home".parse().unwrap()));
        address.set(4322);
        assert!(nav.allows(&"http://127.0.0.1:4322/api/auth/handover/code".parse().unwrap()));
        for rejected in ["http://127.0.0.1:4321/", "http://localhost:4322/", "https://example.test/"] {
            assert!(!nav.allows(&rejected.parse().unwrap()));
        }
    }

    #[test]
    fn onboarding_model_command_only_accepts_the_managed_setup_page() {
        let managed = "http://127.0.0.1:4321";
        let page = format!("{managed}/onboarding").parse().unwrap();
        let command = format!("{managed}{SETUP_MODEL_PATH}").parse().unwrap();
        assert!(setup_model_navigation(&page, &command, managed, managed));
        let wrong_page = format!("{managed}/settings").parse().unwrap();
        assert!(!setup_model_navigation(&wrong_page, &command, managed, managed));
        let remote = "https://example.test";
        assert!(!setup_model_navigation(&page, &command, remote, managed));
        for target in [
            "http://127.0.0.1:4321/onboarding/native-model-settings?or=1",
            "http://127.0.0.1:4321/onboarding/native-model-settings#anything",
            "http://user@127.0.0.1:4321/onboarding/native-model-settings",
            "http://127.0.0.1:4322/onboarding/native-model-settings",
            "https://example.test/onboarding/native-model-settings",
        ] {
            assert!(!setup_model_navigation(&page, &target.parse().unwrap(), managed, managed));
        }
    }

    #[test]
    fn only_the_live_backend_can_drag_its_own_window() {
        use tauri::ipc::RuntimeCapability;
        use tauri::utils::acl::capability::{CapabilityFile, PermissionEntry};
        let CapabilityFile::Capability(cap) = live_window_capability(4321).build() else { panic!("one capability expected") };
        assert!(!cap.local);
        assert!(cap.windows.is_empty());
        assert_eq!(cap.webviews, [LIVE]);
        assert_eq!(cap.remote.unwrap().urls, ["http://127.0.0.1:4321/*"]);
        let permissions: Vec<_> = cap.permissions.iter().map(|permission| match permission {
            PermissionEntry::PermissionRef(id) => id.get().to_owned(),
            _ => panic!("unscoped window permission expected"),
        }).collect();
        assert_eq!(permissions, ["core:window:allow-start-dragging", "core:window:allow-internal-toggle-maximize"]);
    }

    #[test]
    fn navigation_stays_in_packaged_pages() {
        for url in [
            "tauri://localhost/index.html?lang=zh-Hans",
            "http://tauri.localhost/versions.html",
            "about:blank",
        ] {
            assert!(packaged_url(&url.parse().unwrap()), "{url}");
        }
        for url in [
            "https://github.com/DingHappy/VortoOne",
            "https://tauri.localhost/index.html",
            "http://tauri.localhost.evil/",
            "tauri://remote/index.html",
            "file:///etc/passwd",
            "javascript:alert(1)",
        ] {
            assert!(!packaged_url(&url.parse().unwrap()), "{url}");
        }
    }
}
