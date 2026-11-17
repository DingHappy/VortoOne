#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod backend;

use tauri::{
    menu::{Menu, SubmenuBuilder},
    webview::NewWindowResponse,
    Manager, WebviewUrl, WebviewWindowBuilder,
};

const RELEASES: &str = "https://github.com/DingHappy/VortoOne/releases";
const STABLE: &str = "https://github.com/DingHappy/VortoOne/releases/latest";

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

/// 连接真实后端：读配置、启动后端、换一次登录、打开主窗口。没有配置文件时什么也不做（保持界面预览）。
/// 在后台线程里跑：后端冷启动要几秒到几十秒，不能卡住界面。
fn connect(app: tauri::AppHandle) {
    std::thread::spawn(move || {
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
    let Ok(text) = std::fs::read_to_string(config_dir.join(backend::CONFIG_FILE)) else {
        return Ok(());
    };
    let config = backend::parse_config(&text)?;
    let data = app.path().app_data_dir().map_err(|e| e.to_string())?;
    let ui = app.path().resource_dir().map_err(|e| e.to_string())?.join("web");
    std::fs::create_dir_all(&data).map_err(|e| e.to_string())?;
    app.state::<backend::Backend>().stop();
    let (child, port, token) = backend::start(&config, &data.join("home"), &ui, &data.join("backend.log"))?;
    if let Ok(mut slot) = app.state::<backend::Backend>().0.lock() {
        *slot = Some(child);
    }
    let url: tauri::Url = backend::sign_in_url(port, &token)?.parse().map_err(|_| "登录地址无效".to_string())?;
    app.add_capability(live_window_capability(port)).map_err(|e| e.to_string())?;
    let handle = app.clone();
    app.run_on_main_thread(move || {
        if let Some(old) = handle.get_webview_window(LIVE) {
            let _ = old.destroy();
        }
        let builder = WebviewWindowBuilder::new(&handle, LIVE, WebviewUrl::External(url))
            .title("VortoOne Dev")
            .initialization_script(include_str!("../../desktop-titlebar.js"))
            .inner_size(1440.0, 960.0)
            .min_inner_size(900.0, 600.0)
            .center()
            // 窗口只在后端自己的地址里走；外部链接交给系统浏览器
            .on_navigation(move |target| {
                if backend::own_page(target, port) {
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
            }
            Err(error) => eprintln!("无法打开主窗口: {error}"),
        }
    })
    .map_err(|e| e.to_string())
}

fn main() {
    tauri::Builder::default()
        .manage(backend::Backend::default())
        .setup(|app| {
            let menu = Menu::default(app.handle())?;
            let preview = SubmenuBuilder::new(app, "开发版")
                .text("connect", "连接本机后端")
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
            show(
                app.handle(),
                "main",
                "index.html?lang=zh-Hans",
                "VortoOne Dev · 界面预览",
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
