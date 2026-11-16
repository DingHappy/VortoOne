#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

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

fn main() {
    tauri::Builder::default()
        .setup(|app| {
            let menu = Menu::default(app.handle())?;
            let preview = SubmenuBuilder::new(app, "开发版")
                .text("desktop", "桌面界面")
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
            Ok(())
        })
        .on_menu_event(|app, event| {
            let result = match event.id().as_ref() {
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
        .run(tauri::generate_context!())
        .expect("VortoOne Dev failed to start");
}

#[cfg(test)]
mod tests {
    use super::*;

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
