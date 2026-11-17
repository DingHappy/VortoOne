#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod backend;
mod model_settings;

use tauri::{
    menu::{Menu, SubmenuBuilder},
    webview::NewWindowResponse,
    Manager, WebviewUrl, WebviewWindowBuilder,
};

const RELEASES: &str = "https://github.com/DingHappy/VortoOne/releases";
const STABLE: &str = "https://github.com/DingHappy/VortoOne/releases/latest";
#[derive(Default)]
struct ConnectionGate(std::sync::Mutex<()>);
#[derive(Clone, Default)]
struct LiveAddress(std::sync::Arc<std::sync::atomic::AtomicU16>);

impl LiveAddress {
    fn set(&self, port: u16) { self.0.store(port, std::sync::atomic::Ordering::SeqCst); }
    fn port(&self) -> u16 { self.0.load(std::sync::atomic::Ordering::SeqCst) }
    fn allows(&self, url: &tauri::Url) -> bool {
        backend::own_page(url, self.0.load(std::sync::atomic::Ordering::SeqCst))
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
    WebviewWindowBuilder::new(app, label, WebviewUrl::App(path.into()))
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

fn live_window_capability(port: u16) -> tauri::ipc::CapabilityBuilder {
    tauri::ipc::CapabilityBuilder::new(format!("dev-live-window-{port}"))
        .local(false)
        .remote(format!("{}/*", backend::origin(port)))
        .webview(LIVE)
        .permission("core:window:allow-start-dragging")
        .permission("core:window:allow-internal-toggle-maximize")
}

/// 优先启动随包后端；源码模式只供未打包运行时的开发环境使用。
/// 在后台线程里跑：后端冷启动要几秒到几十秒，不能卡住界面。
fn connect(app: tauri::AppHandle) {
    std::thread::spawn(move || {
        let gate = app.state::<ConnectionGate>();
        let Ok(_guard) = gate.0.lock() else { return; };
        if let Err(error) = connect_now(&app) {
            eprintln!("连接后端失败: {error}");
            let message = format!("VortoOne Dev · 没有连上后端：{error}");
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.set_title(&message);
            }
        }
    });
}

fn connect_now(app: &tauri::AppHandle) -> Result<(), String> {
    let config_dir = app.path().app_config_dir().map_err(|e| e.to_string())?;
    let data = app.path().app_data_dir().map_err(|e| e.to_string())?;
    let resources = app.path().resource_dir().map_err(|e| e.to_string())?;
    let ui = resources.join("web");
    let runtime = resources.join("runtime").join(if cfg!(windows) { "vortocode-runtime.exe" } else { "vortocode-runtime" });
    let env_file = model_settings::env_file(&config_dir, &data);
    let first_run = runtime.is_file() && env_file.is_none();
    let config = if runtime.is_file() { None } else {
        let Ok(text) = std::fs::read_to_string(config_dir.join(backend::CONFIG_FILE)) else { return Ok(()); };
        Some(backend::parse_config(&text)?)
    };
    std::fs::create_dir_all(&data).map_err(|e| e.to_string())?;
    app.state::<backend::Backend>().stop();
    let (child, port, token) = if let Some(config) = config {
        backend::start(&config, &data.join("home"), &ui, &data.join("backend.log"))?
    } else {
        backend::start_bundled(&runtime, env_file.as_deref(), &data.join("home"), &ui, &data.join("backend.log"))?
    };
    if let Ok(mut slot) = app.state::<backend::Backend>().0.lock() {
        *slot = Some(child);
    }
    let url: tauri::Url = backend::sign_in_url(port, &token)?.parse().map_err(|_| "登录地址无效".to_string())?;
    app.add_capability(live_window_capability(port)).map_err(|e| e.to_string())?;
    app.state::<LiveAddress>().set(port);
    watch_consents(app.clone(), port, token.clone());
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
        let builder = WebviewWindowBuilder::new(&handle, LIVE, WebviewUrl::External(url))
            .title("VortoOne Dev")
            .initialization_script(include_str!("../../desktop-titlebar.js"))
            .inner_size(1440.0, 960.0)
            .min_inner_size(900.0, 600.0)
            .center()
            // 窗口只在后端自己的地址里走；外部链接交给系统浏览器
            .on_navigation(move |target| {
                if address.allows(target) {
                    return true;
                }
                if matches!(target.scheme(), "https" | "http") {
                    let _ = tauri_plugin_opener::open_url(target.as_str(), None::<&str>);
                }
                false
            })
            .on_new_window(|_, _| NewWindowResponse::Deny);
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
                if first_run {
                    let _ = show(&handle, "model-settings", "model-settings.html", "连接模型", 600.0, 650.0);
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
        let built = WebviewWindowBuilder::new(&handle, CONSENT, WebviewUrl::External(url))
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
    if std::env::args().any(|arg| arg == "--check-standalone") {
        let result = std::env::current_exe().map_err(|e| e.to_string()).and_then(|exe| {
            let contents = exe.parent().and_then(|p| p.parent()).ok_or("安装包路径无效")?;
            backend::verify_standalone(&contents.join("Resources"))
        });
        match result {
            Ok(()) => println!("{{\"standalone\":true,\"freshProfile\":true,\"login\":true,\"ui\":true,\"inferenceSent\":false}}"),
            Err(error) => { eprintln!("安装验收失败：{error}"); std::process::exit(1); }
        }
        return;
    }
    tauri::Builder::default()
        .manage(backend::Backend::default())
        .manage(ConnectionGate::default())
        .manage(LiveAddress::default())
        .invoke_handler(tauri::generate_handler![model_settings::model_settings_get, model_settings::model_settings_save])
        .setup(|app| {
            let menu = Menu::default(app.handle())?;
            let preview = SubmenuBuilder::new(app, "开发版")
                .text("connect", "连接本机后端")
                .text("models", "模型连接…")
                .separator()
                .text("desktop", "桌面界面（示例数据）")
                .text("characters", "史莱姆外观")
                .text("preferences", "偏好设置预览")
                .separator()
                .text("versions", "版本管理")
                .text("stable", "公开稳定版下载…")
                .text("releases", "管理 GitHub 发布…")
                .build()?;
            menu.append(&preview)?;
            app.set_menu(menu)?;
            let bundled = app.path().resource_dir()?.join("runtime").join(
                if cfg!(windows) { "vortocode-runtime.exe" } else { "vortocode-runtime" }).is_file();
            show(
                app.handle(),
                "main",
                if bundled { "starting.html" } else { "index.html?lang=zh-Hans" },
                if bundled { "VortoOne Dev" } else { "VortoOne Dev · 界面预览" },
                1440.0,
                960.0,
            )?;
            connect(app.handle().clone());
            Ok(())
        })
        .on_menu_event(|app, event| {
            let result = match event.id().as_ref() {
                "connect" => {
                    connect(app.clone());
                    return;
                }
                "models" => show(app, "model-settings", "model-settings.html", "连接模型", 600.0, 650.0),
                "desktop" => show(
                    app,
                    "main",
                    "index.html?lang=zh-Hans",
                    "VortoOne Dev · 界面预览",
                    1440.0,
                    960.0,
                ),
                "characters" => show(
                    app,
                    "characters",
                    "character-lab.html",
                    "VortoOne Dev · 史莱姆外观",
                    1280.0,
                    960.0,
                ),
                "preferences" => show(
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
                    "VortoOne Dev · 版本管理",
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
                eprintln!("无法打开开发版窗口: {error}");
            }
        })
        .build(tauri::generate_context!())
        .expect("VortoOne Dev failed to start")
        .run(|app, event| {
            // 退出时带走后端；被强杀时由后端看监护进程 pid 自己退出
            if let tauri::RunEvent::Exit = event {
                app.state::<backend::Backend>().stop();
            }
        });
}

#[cfg(test)]
mod tests {
    use super::*;

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
