//! 首次引导和偏好设置窗口（`frontend/apps/desktop` 的窗口界面，连真外壳那一份）要的宿主能力。
//!
//! 接口照 agentes.chat 桌面端窗口与外壳之间的约定（`host/tauri.ts`），实现是自己写的：参照它的 ADR 0066，
//! 不迁入对方的 Rust。第 1 步只实现「家的列表、首次引导、这台电脑上的服务」用得到的；其余能力（连接的电脑、
//! 刘海、菜单栏、更新）按「没有」回答，窗口里对应的地方不显示。
//!
//! 每个命令先核对调用的窗口：只认随包页面 `shell/index.html` 里标签是 welcome / prefs 的窗口。
//! 主窗口打开的是服务的网页，它调不到这里的任何命令。

use crate::{backend, places};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tauri::{Emitter, Manager, WebviewUrl, WebviewWindowBuilder};

pub const PAGE: &str = "shell/index.html";
const WINDOWS: [&str; 2] = ["welcome", "prefs"];
const KEEP_BACKUPS: usize = 2;

#[derive(Default)]
pub struct ShellState {
    phase: Mutex<String>,
    welcome_mode: Mutex<Option<String>>,
    installing: AtomicBool,
    cancel: AtomicBool,
}

fn now_ms() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_millis() as u64).unwrap_or(0)
}

fn stamp() -> String {
    // 文件名里的时间：秒数够分开，不依赖时区库
    format!("{}", now_ms() / 1000)
}

fn trusted(label: &str, url: &tauri::Url) -> bool {
    WINDOWS.contains(&label) && crate::packaged_url(url) && url.path() == format!("/{PAGE}")
}

fn check(window: &tauri::WebviewWindow) -> Result<(), String> {
    let url = window.url().map_err(|_| "无法核对窗口".to_string())?;
    if trusted(window.label(), &url) { Ok(()) } else { Err("这个窗口不能使用宿主命令".into()) }
}

fn dirs(app: &tauri::AppHandle) -> Result<(PathBuf, PathBuf), String> {
    Ok((app.path().app_config_dir().map_err(|e| e.to_string())?, app.path().app_data_dir().map_err(|e| e.to_string())?))
}

// ── 状态 ────────────────────────────────────────────────────────────────────

fn phase(app: &tauri::AppHandle) -> String {
    let found = app.state::<ShellState>().phase.lock().map(|p| p.clone()).unwrap_or_default();
    if found.is_empty() { "off".into() } else { found }
}

/// 本机服务的阶段变了：记下并告诉所有窗口。
pub fn set_phase(app: &tauri::AppHandle, value: &str) {
    if let Ok(mut slot) = app.state::<ShellState>().phase.lock() {
        *slot = value.to_string();
    }
    let _ = app.emit("local-server://status", status(app));
}

/// 本机服务起来了（端口可能换了）：改列表里它的地址，当前没选别处时就选它。
pub fn local_started(app: &tauri::AppHandle, port: u16) -> Result<(), String> {
    let (config, data) = dirs(app)?;
    let url = backend::origin(port);
    let mut local = places::read_local(&config, &data);
    let mut list = places::read_servers(&config);
    list.moved(local.url.as_deref(), &url);
    if list.current.as_deref().is_none_or(places::is_loopback) {
        list.choose(&url)?;
    }
    local.url = Some(url);
    local.installed = true;
    places::write_local(&config, &local)?;
    places::write_servers(&config, &list)?;
    set_phase(app, "running");
    Ok(())
}

fn backups(data: &Path, copy_to: Option<&str>) -> Vec<Value> {
    let mut files: Vec<(u64, String)> = std::fs::read_dir(data.join("backups")).into_iter().flatten().flatten()
        .filter_map(|entry| {
            let path = entry.path();
            let name = path.file_name()?.to_string_lossy().to_string();
            if !(name.starts_with("vortoone-") && name.ends_with(".tar.gz")) { return None; }
            let at = entry.metadata().ok()?.modified().ok()?.duration_since(UNIX_EPOCH).ok()?.as_millis() as u64;
            Some((at, path.display().to_string()))
        }).collect();
    files.sort_by(|a, b| b.0.cmp(&a.0));
    let _ = copy_to;
    files.into_iter().map(|(at, path)| json!({"path": path, "at": at})).collect()
}

pub fn status(app: &tauri::AppHandle) -> Value {
    let Ok((config, data)) = dirs(app) else { return json!({}) };
    let local = places::read_local(&config, &data);
    let bundled = app.path().resource_dir().map(|r| r.join("runtime").join("vortocode-runtime").is_file()).unwrap_or(false);
    let phase = phase(app);
    let url = if phase == "running" { local.url.clone() } else { None };
    json!({
        "bundled": bundled,
        "installed": if local.installed { "app" } else { "none" },
        "run": local.run,
        // 所有者就是 App 保管的所有者密钥：装好就有
        "owner": local.installed,
        "phase": phase,
        "url": url,
        "publicUrl": Value::Null,
        "reach": false,
        "usage": false,
        "version": app.package_info().version.to_string(),
        "dir": data.display().to_string(),
        "systemName": std::env::var("USER").ok(),
        "serving": false,
        "claim": Value::Null,
        "backTo": Value::Null,
        "held": local.held,
        "heldUntil": local.held_until,
        "tailnetDown": false,
        "backups": {"nightly": local.nightly, "copyTo": local.copy_to, "files": backups(&data, local.copy_to.as_deref()),
                    "problem": Value::Null},
        "awake": false,
        "eraseTo": local.installed.then(|| data.join(format!("erased-{}", stamp())).display().to_string()),
        "erased": local.erased,
    })
}

// ── 窗口 ────────────────────────────────────────────────────────────────────

/// 打开（或叫到前面）首次引导 / 偏好设置窗口。
pub fn open_window(app: &tauri::AppHandle, name: &str) -> Result<(), String> {
    if !WINDOWS.contains(&name) {
        return Err("没有这个窗口".into());
    }
    if let Some(window) = app.get_webview_window(name) {
        let _ = window.show();
        let _ = window.set_focus();
        let _ = app.emit("window://shown", name);
        return Ok(());
    }
    let (title, width, height) = if name == "welcome" { ("VortoOne", 980.0, 700.0) } else { ("偏好设置", 900.0, 680.0) };
    WebviewWindowBuilder::new(app, name, WebviewUrl::App(PAGE.into()))
        .title(title)
        .inner_size(width, height)
        .min_inner_size(720.0, 540.0)
        .center()
        .on_navigation(crate::packaged_url)
        .on_new_window(|_, _| tauri::webview::NewWindowResponse::Deny)
        .build()
        .map_err(|e| e.to_string())?;
    Ok(())
}

/// 首次引导和偏好设置窗口能用的核心权限（收发事件、管自己的窗口）；只给随包页面。
pub fn capability() -> tauri::ipc::CapabilityBuilder {
    tauri::ipc::CapabilityBuilder::new("dev-shell-windows").windows(WINDOWS).permission("core:default")
}

// ── 打开哪个服务 ────────────────────────────────────────────────────────────

fn local_origin(app: &tauri::AppHandle) -> Option<String> {
    let (config, data) = dirs(app).ok()?;
    places::read_local(&config, &data).url
}

fn page_path(page: Option<&str>) -> Result<String, String> {
    match page {
        None => Ok("/".into()),
        Some(p) if p.starts_with('/') && !p.starts_with("//") && !p.contains(['\\', '\r', '\n']) && p.len() <= 512 => Ok(p.into()),
        Some(_) => Err("页面地址不对".into()),
    }
}

/// 主窗口打开别处的服务（它自己的登录页或给定页面）。
pub fn open_remote(app: &tauri::AppHandle, server: &str, page: Option<&str>) -> Result<(), String> {
    let origin = places::origin(server).ok_or("只能打开 https 地址")?;
    let url: tauri::Url = format!("{origin}{}", page_path(page)?).parse().map_err(|_| "地址不对".to_string())?;
    app.state::<crate::LiveAddress>().set_origin(&origin, 0);
    let id = format!("dev-live-{}", origin.replace(|c: char| !c.is_ascii_alphanumeric(), "-"));
    app.add_capability(crate::live_window_capability_for(&origin, &id)).map_err(|e| e.to_string())?;
    crate::show_live(app, url, false)
}

fn choose(app: &tauri::AppHandle, server: &str, page: Option<&str>) -> Result<(), String> {
    let (config, data) = dirs(app)?;
    let origin = places::origin(server).ok_or("只能打开 https 地址，或这台电脑上的 http 地址")?;
    let mut list = places::read_servers(&config);
    list.choose(&origin)?;
    places::write_servers(&config, &list)?;
    let local = places::read_local(&config, &data);
    if local.url.as_deref() == Some(origin.as_str()) || (places::is_loopback(&origin) && local.installed) {
        if phase(app) == "running" {
            let key = backend::owner_key(&data.join(places::OWNER_KEY_FILE))?;
            let port: u16 = origin.rsplit(':').next().and_then(|p| p.parse().ok()).ok_or("地址不对")?;
            let url = backend::sign_in_url(port, &key)?.parse().map_err(|_| "登录地址无效".to_string())?;
            app.state::<crate::LiveAddress>().set(port);
            return crate::show_live(app, url, false);
        }
        crate::connect(app.clone());
        return Ok(());
    }
    if places::is_loopback(&origin) {
        // 这台电脑上别人起的服务：照样只走它自己的页面
        app.state::<crate::LiveAddress>().set_origin(&origin, 0);
        let url = format!("{origin}{}", page_path(page)?).parse().map_err(|_| "地址不对".to_string())?;
        return crate::show_live(app, url, false);
    }
    open_remote(app, &origin, page)
}

// ── 这台电脑上的服务：装、启停、备份、恢复、清空 ──────────────────────────────

fn emit_step(app: &tauri::AppHandle, step: &str, state: &str, reason: Option<Value>) {
    let mut change = json!({"step": step, "state": state});
    if let Some(reason) = reason {
        change["reason"] = reason;
    }
    let _ = app.emit("local-server://step", change);
}

#[derive(Deserialize)]
pub struct LocalChoice {
    #[serde(default)]
    reach: bool,
    #[serde(default)]
    name: Option<String>,
    #[serde(default)]
    email: Option<String>,
}

fn short(value: Option<String>, limit: usize) -> Option<String> {
    value.map(|v| v.trim().chars().filter(|c| !c.is_control()).take(limit).collect::<String>()).filter(|v| !v.is_empty())
}

fn install(app: &tauri::AppHandle, choice: LocalChoice) -> Result<(), String> {
    let state = app.state::<ShellState>();
    let cancelled = || state.cancel.load(Ordering::SeqCst);
    let (config, data) = dirs(app)?;
    emit_step(app, "database", "active", None);
    std::fs::create_dir_all(data.join("home")).map_err(|e| e.to_string())?;
    backend::owner_key(&data.join(places::OWNER_KEY_FILE))?;
    emit_step(app, "database", "done", None);
    if cancelled() { return Err("cancelled".into()); }
    emit_step(app, "start", "active", None);
    let mut local = places::read_local(&config, &data);
    local.run = true;
    local.held = false;
    local.held_until = None;
    local.erased = None;
    if let Some(name) = short(choice.name.clone(), 80) { local.owner_name = Some(name); }
    if let Some(email) = short(choice.email.clone(), 254) { local.owner_email = Some(email); }
    places::write_local(&config, &local)?;
    let started = crate::connect_now(app).and_then(|_| {
        // 没有随包后端也没配源码路径时 connect_now 什么都不做：那不算起来了
        if phase(app) == "running" { Ok(()) } else { Err("这个安装包里没有本机服务".into()) }
    });
    if let Err(error) = started {
        emit_step(app, "start", "failed", None);
        return Err(format!("start: {error}"));
    }
    emit_step(app, "start", "done", None);
    if cancelled() {
        app.state::<backend::Backend>().stop();
        let mut local = places::read_local(&config, &data);
        local.run = false;
        let _ = places::write_local(&config, &local);
        set_phase(app, "off");
        return Err("cancelled".into());
    }
    // 所有者：App 保管的所有者密钥就是所有者，不另建账号
    emit_step(app, "account", "done", None);
    // 这台电脑就是它的 agent 干活的电脑：服务本来就跑在这里
    emit_step(app, "computer", "done", None);
    if choice.reach {
        // 让手机和别的电脑经 Tailscale 连进来：第 1 步的第 3 个 PR 做
        emit_step(app, "reach", "warn", Some(json!({"reason": "serve"})));
    }
    Ok(())
}

fn stop_local(app: &tauri::AppHandle) {
    app.state::<backend::Backend>().stop();
    set_phase(app, "stopped");
}

fn backup(app: &tauri::AppHandle) -> Result<String, String> {
    let (config, data) = dirs(app)?;
    let local = places::read_local(&config, &data);
    let was_running = phase(app) == "running";
    // 停着备份：数据库和文件不会写到一半
    if was_running { stop_local(app); }
    let folder = data.join("backups");
    std::fs::create_dir_all(&folder).map_err(|e| e.to_string())?;
    let target = folder.join(format!("vortoone-{}.tar.gz", stamp()));
    let mut parts = vec!["home".to_string()];
    for extra in [places::OWNER_KEY_FILE, "model.env"] {
        if data.join(extra).is_file() { parts.push(extra.into()); }
    }
    let made = Command::new("tar").arg("-czf").arg(&target).arg("-C").arg(&data).args(&parts).status();
    let result = match made {
        Ok(code) if code.success() => {
            if let Some(copy_to) = local.copy_to.as_deref() {
                let _ = std::fs::copy(&target, Path::new(copy_to).join(target.file_name().unwrap_or_default()));
            }
            let mut files = backups(&data, None);
            for old in files.drain(KEEP_BACKUPS.min(files.len())..) {
                if let Some(path) = old["path"].as_str() { let _ = std::fs::remove_file(path); }
            }
            Ok(target.display().to_string())
        }
        _ => {
            let _ = std::fs::remove_file(&target);
            Err("备份没有完成".into())
        }
    };
    if was_running { crate::connect(app.clone()); }
    result
}

fn restore(app: &tauri::AppHandle, file: &str) -> Result<(), String> {
    let (config, data) = dirs(app)?;
    let local = places::read_local(&config, &data);
    let path = Path::new(file).canonicalize().map_err(|_| "没有这份备份".to_string())?;
    let mut allowed = vec![data.join("backups")];
    if let Some(copy_to) = local.copy_to.as_deref() { allowed.push(PathBuf::from(copy_to)); }
    let name = path.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default();
    if !(name.starts_with("vortoone-") && name.ends_with(".tar.gz"))
        || !allowed.iter().filter_map(|d| d.canonicalize().ok()).any(|d| path.starts_with(&d)) {
        return Err("只能从这里的备份恢复".into());
    }
    stop_local(app);
    let aside = data.join(format!("home.before-restore-{}", stamp()));
    if data.join("home").exists() {
        std::fs::rename(data.join("home"), &aside).map_err(|e| e.to_string())?;
    }
    let unpacked = Command::new("tar").arg("-xzf").arg(&path).arg("-C").arg(&data).arg("home").status();
    if !matches!(unpacked, Ok(code) if code.success()) || !data.join("home").is_dir() {
        let _ = std::fs::remove_dir_all(data.join("home"));
        let _ = std::fs::rename(&aside, data.join("home"));
        crate::connect(app.clone());
        return Err("备份解不开，已经放回原来的数据".into());
    }
    crate::connect(app.clone());
    Ok(())
}

fn erase(app: &tauri::AppHandle) -> Result<String, String> {
    let (config, data) = dirs(app)?;
    let mut local = places::read_local(&config, &data);
    app.state::<backend::Backend>().stop();
    let target = data.join(format!("erased-{}", stamp()));
    std::fs::create_dir_all(&target).map_err(|e| e.to_string())?;
    for name in ["home", places::OWNER_KEY_FILE, "backend.port"] {
        if data.join(name).exists() {
            std::fs::rename(data.join(name), target.join(name)).map_err(|e| e.to_string())?;
        }
    }
    let mut list = places::read_servers(&config);
    if let Some(url) = local.url.as_deref() { list.remove(url); }
    places::write_servers(&config, &list)?;
    local = places::LocalState { erased: Some(target.display().to_string()), ..places::LocalState::default() };
    places::write_local(&config, &local)?;
    if let Some(window) = app.get_webview_window(crate::LIVE) { let _ = window.close(); }
    set_phase(app, "off");
    if let Ok(mut mode) = app.state::<ShellState>().welcome_mode.lock() { *mode = Some("owner".into()); }
    open_window(app, "welcome")?;
    Ok(target.display().to_string())
}

// ── 搬家：导出并停用这里、从搬家包导入（菜单「开发版」；排除规则只在后端 home_move.py 一处） ──────

/// 用随包后端（源码开发时用 backend.json 的 Python）在这个家的目录里跑一个子命令，读它打的那一行 JSON。
fn runtime_tool(app: &tauri::AppHandle, args: &[&str]) -> Result<Value, String> {
    let (config, data) = dirs(app)?;
    let runtime = app.path().resource_dir().map_err(|e| e.to_string())?.join("runtime").join("vortocode-runtime");
    let home = data.join("home");
    std::fs::create_dir_all(&home).map_err(|e| e.to_string())?;
    let mut command = if runtime.is_file() {
        let mut command = Command::new(&runtime);
        command.env_remove("PYTHONPATH");
        command
    } else {
        let text = std::fs::read_to_string(config.join(backend::CONFIG_FILE)).map_err(|_| "这个安装包里没有本机服务".to_string())?;
        let source = backend::parse_config(&text)?;
        let mut command = Command::new(&source.python);
        command.args(["-m", "src.runtime_entry"]).env("PYTHONPATH", &source.source);
        command
    };
    let output = command.args(args).current_dir(&home).env("PYTHONNOUSERSITE", "1").env_remove("PYTHONHOME")
        .stdin(std::process::Stdio::null()).output().map_err(|e| format!("后端没能运行：{e}"))?;
    let text = String::from_utf8_lossy(&output.stdout);
    let answer: Value = text.lines().rev().find(|l| !l.trim().is_empty()).and_then(|l| serde_json::from_str(l).ok())
        .ok_or_else(|| "后端没有给出结果".to_string())?;
    if answer["ok"] == true { Ok(answer) } else { Err(answer["error"].as_str().unwrap_or("没有完成").to_string()) }
}

fn tell(app: &tauri::AppHandle, title: &str, text: &str, error: bool) {
    use tauri_plugin_dialog::{DialogExt, MessageDialogKind};
    let kind = if error { MessageDialogKind::Error } else { MessageDialogKind::Info };
    app.dialog().message(text).title(title).kind(kind).blocking_show();
}

fn ask(app: &tauri::AppHandle, title: &str, text: &str, yes: &str) -> bool {
    use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
    app.dialog().message(text).title(title).kind(MessageDialogKind::Warning)
        .buttons(MessageDialogButtons::OkCancelCustom(yes.into(), "取消".into())).blocking_show()
}

/// 「搬家：导出并停用这里…」：导出成搬家包，这台电脑上不再启动这个家；数据挪到 `moved-<时间>` 留着。
pub fn move_out(app: tauri::AppHandle) {
    std::thread::spawn(move || {
        use tauri_plugin_dialog::DialogExt;
        let Ok((config, data)) = dirs(&app) else { return };
        if !data.join("home").join(".vortoone").is_dir() {
            return tell(&app, "搬家", "这台电脑上还没有可以搬的家。", true);
        }
        if !ask(&app, "搬家", "把这台电脑上的家导出成一个搬家包，在新的地方导入。\n\n导出后这里不再启动它（同一个空间不能在两处同时接活）；\
                  这里的数据挪到旁边留着，不删。手机、AI 应用到新家要重新连，模型要重新填。", "导出并停用这里") {
            return;
        }
        let Some(path) = app.dialog().file().add_filter("VortoOne 搬家包", &["gz"])
            .set_file_name(format!("vortoone-home-{}.tar.gz", stamp())).blocking_save_file()
            .and_then(|f| f.into_path().ok()) else { return };
        let was_running = phase(&app) == "running";
        stop_local(&app);
        let target = path.display().to_string();
        match runtime_tool(&app, &["home-export", &target, "--move"]) {
            Ok(_) => {
                let aside = data.join(format!("moved-{}", stamp()));
                let moved = std::fs::create_dir_all(&aside).and_then(|_| std::fs::rename(data.join("home"), aside.join("home")));
                let mut list = places::read_servers(&config);
                let mut local = places::read_local(&config, &data);
                if let Some(url) = local.url.as_deref() { list.remove(url); }
                local = places::LocalState { erased: Some(aside.display().to_string()), owner_name: local.owner_name,
                                             owner_email: local.owner_email, ..places::LocalState::default() };
                let _ = places::write_servers(&config, &list);
                let _ = places::write_local(&config, &local);
                if let Some(live) = app.get_webview_window(crate::LIVE) { let _ = live.close(); }
                set_phase(&app, "off");
                let note = if moved.is_ok() { format!("这里的数据留在 {}。", aside.display()) } else { String::new() };
                tell(&app, "搬家", &format!("搬家包在 {target}。\n\n在新的地方打开 VortoOne Dev，用菜单「开发版 → 从搬家包导入…」导入它。{note}"), false);
                let _ = open_window(&app, "welcome");
            }
            Err(error) => {
                if was_running { crate::connect(app.clone()); }
                tell(&app, "搬家没有完成", &error, true);
            }
        }
    });
}

/// 「从搬家包导入…」：导入到这台电脑上的家；这里已有数据时先问要不要替换（原来的挪到旁边留着）。
pub fn move_in(app: tauri::AppHandle) {
    std::thread::spawn(move || {
        use tauri_plugin_dialog::DialogExt;
        let Ok((config, data)) = dirs(&app) else { return };
        let Some(path) = app.dialog().file().add_filter("VortoOne 搬家包", &["gz"]).blocking_pick_file()
            .and_then(|f| f.into_path().ok()) else { return };
        let package = path.display().to_string();
        stop_local(&app);
        let mut result = runtime_tool(&app, &["home-import", &package]);
        if matches!(&result, Err(e) if e.contains("已经有数据")) {
            if !ask(&app, "导入搬家包", "这台电脑上的家里已经有数据。要用搬家包替换吗？原来的会挪到旁边留着，不删。", "替换") {
                if places::read_local(&config, &data).should_start(now_ms()) { crate::connect(app.clone()); }
                return;
            }
            result = runtime_tool(&app, &["home-import", &package, "--replace"]);
        }
        match result {
            Ok(_) => {
                let mut local = places::read_local(&config, &data);
                local.installed = true;
                local.run = true;
                local.held = false;
                local.held_until = None;
                local.erased = None;
                let _ = places::write_local(&config, &local);
                if let Some(welcome) = app.get_webview_window("welcome") { let _ = welcome.hide(); }
                crate::connect(app.clone());
                tell(&app, "导入好了", "聊天、记忆、agent 和成员都搬过来了。手机、AI 应用要重新连；模型在「开发版 → 模型连接…」重新填。", false);
            }
            Err(error) => {
                if places::read_local(&config, &data).should_start(now_ms()) { crate::connect(app.clone()); }
                tell(&app, "导入没有完成", &error, true);
            }
        }
    });
}

// ── Tailscale：只读状态（让别的设备连进来在第 3 个 PR 做） ─────────────────────

fn output_within(mut command: Command, limit: Duration) -> Option<String> {
    let mut child = command.stdout(std::process::Stdio::piped()).stderr(std::process::Stdio::null()).spawn().ok()?;
    let started = Instant::now();
    loop {
        match child.try_wait() {
            Ok(Some(_)) => break,
            Ok(None) if started.elapsed() < limit => std::thread::sleep(Duration::from_millis(50)),
            _ => { let _ = child.kill(); let _ = child.wait(); return None; }
        }
    }
    let mut text = String::new();
    use std::io::Read as _;
    child.stdout.take()?.read_to_string(&mut text).ok()?;
    Some(text)
}

fn tailnet() -> Value {
    let app = Path::new("/Applications/Tailscale.app").is_dir();
    let cli = places::find_command("tailscale", std::env::var_os("HOME").as_deref().map(Path::new)).or_else(|| {
        let bundled = PathBuf::from("/Applications/Tailscale.app/Contents/MacOS/Tailscale");
        bundled.is_file().then_some(bundled)
    });
    let Some(cli) = cli else {
        return if app { json!({"state": "off", "app": true}) } else { json!({"state": "missing"}) };
    };
    let mut command = Command::new(cli);
    command.args(["status", "--json"]);
    let parsed: Option<Value> = output_within(command, Duration::from_secs(2)).and_then(|t| serde_json::from_str(&t).ok());
    match parsed {
        Some(v) if v["BackendState"] == "Running" => {
            let device = v["Self"]["DNSName"].as_str().unwrap_or_default().trim_end_matches('.').to_string();
            json!({"state": "on", "device": device})
        }
        _ => json!({"state": "off", "app": app}),
    }
}

// ── 命令 ────────────────────────────────────────────────────────────────────

#[derive(Serialize)]
pub struct Candidate {
    server: String,
    #[serde(rename = "where")]
    place: &'static str,
}

#[tauri::command]
pub fn servers_get(window: tauri::WebviewWindow, app: tauri::AppHandle) -> Result<places::ServerList, String> {
    check(&window)?;
    Ok(places::read_servers(&dirs(&app)?.0))
}

#[tauri::command]
pub fn server_choose(window: tauri::WebviewWindow, app: tauri::AppHandle, server: String, page: Option<String>) -> Result<(), String> {
    check(&window)?;
    choose(&app, &server, page.as_deref())
}

#[tauri::command]
pub fn server_remove(window: tauri::WebviewWindow, app: tauri::AppHandle, server: String) -> Result<(), String> {
    check(&window)?;
    let config = dirs(&app)?.0;
    let mut list = places::read_servers(&config);
    list.remove(&server);
    places::write_servers(&config, &list)?;
    if places::origin(&server).is_some_and(|o| o == app.state::<crate::LiveAddress>().origin()) {
        if let Some(live) = app.get_webview_window(crate::LIVE) { let _ = live.close(); }
        app.state::<crate::LiveAddress>().set_origin("", 0);
    }
    Ok(())
}

#[tauri::command]
pub fn server_sign_out(window: tauri::WebviewWindow, app: tauri::AppHandle, server: String) -> Result<(), String> {
    check(&window)?;
    // 退出登录要带着主窗口在那个服务上的 Cookie：由主窗口自己的页面发出
    if places::origin(&server).is_some_and(|o| o == app.state::<crate::LiveAddress>().origin()) {
        if let Some(live) = app.get_webview_window(crate::LIVE) {
            live.eval("fetch('/api/auth/sign-out',{method:'POST'}).finally(()=>location.replace('/login'))")
                .map_err(|e| e.to_string())?;
        }
    }
    Ok(())
}

#[tauri::command]
pub fn workspaces_get(window: tauri::WebviewWindow) -> Result<Vec<Value>, String> {
    check(&window)?;
    Ok(Vec::new())
}

#[tauri::command]
pub fn welcome_mode_take(window: tauri::WebviewWindow, app: tauri::AppHandle) -> Result<Option<String>, String> {
    check(&window)?;
    Ok(app.state::<ShellState>().welcome_mode.lock().ok().and_then(|mut mode| mode.take()))
}

#[tauri::command]
pub fn welcome_add(window: tauri::WebviewWindow, app: tauri::AppHandle) -> Result<(), String> {
    check(&window)?;
    if let Ok(mut mode) = app.state::<ShellState>().welcome_mode.lock() { *mode = Some("add".into()); }
    open_window(&app, "welcome")
}

#[tauri::command]
pub fn discover(window: tauri::WebviewWindow, app: tauri::AppHandle) -> Result<Vec<Candidate>, String> {
    check(&window)?;
    let mut found = Vec::new();
    if phase(&app) == "running" {
        if let Some(url) = local_origin(&app) { found.push(Candidate { server: url, place: "here" }); }
    }
    Ok(found)
}

#[tauri::command]
pub fn link_take(window: tauri::WebviewWindow) -> Result<Option<String>, String> {
    check(&window)?;
    Ok(None)
}

#[tauri::command]
pub fn open_external(window: tauri::WebviewWindow, url: String) -> Result<(), String> {
    check(&window)?;
    let parsed: tauri::Url = url.parse().map_err(|_| "地址不对".to_string())?;
    if !matches!(parsed.scheme(), "https" | "http") { return Err("只能打开网页地址".into()); }
    tauri_plugin_opener::open_url(parsed.as_str(), None::<&str>).map_err(|e| e.to_string())
}

#[tauri::command]
pub fn show_window(window: tauri::WebviewWindow, app: tauri::AppHandle, name: String) -> Result<(), String> {
    check(&window)?;
    open_window(&app, &name)
}

#[tauri::command]
pub fn hide_window(window: tauri::WebviewWindow, app: tauri::AppHandle, name: String) -> Result<(), String> {
    check(&window)?;
    if let Some(target) = WINDOWS.contains(&name.as_str()).then(|| app.get_webview_window(&name)).flatten() {
        let _ = target.hide();
    }
    Ok(())
}

#[tauri::command]
pub fn app_info(window: tauri::WebviewWindow, app: tauri::AppHandle) -> Result<Value, String> {
    check(&window)?;
    Ok(json!({"version": app.package_info().version.to_string(), "os": std::env::consts::OS,
              "arch": std::env::consts::ARCH, "system": Value::Null, "graphics": Value::Null}))
}

#[tauri::command]
pub fn local_server_status(window: tauri::WebviewWindow, app: tauri::AppHandle) -> Result<Value, String> {
    check(&window)?;
    Ok(status(&app))
}

#[tauri::command]
pub async fn local_server_install(window: tauri::WebviewWindow, app: tauri::AppHandle, choice: LocalChoice) -> Result<(), String> {
    check(&window)?;
    let state = app.state::<ShellState>();
    if state.installing.swap(true, Ordering::SeqCst) { return Err("正在设置".into()); }
    state.cancel.store(false, Ordering::SeqCst);
    let handle = app.clone();
    let result = tauri::async_runtime::spawn_blocking(move || install(&handle, choice)).await
        .map_err(|e| e.to_string()).and_then(|r| r);
    app.state::<ShellState>().installing.store(false, Ordering::SeqCst);
    result
}

#[tauri::command]
pub fn local_server_cancel(window: tauri::WebviewWindow, app: tauri::AppHandle) -> Result<(), String> {
    check(&window)?;
    app.state::<ShellState>().cancel.store(true, Ordering::SeqCst);
    Ok(())
}

#[tauri::command]
pub fn local_server_start(window: tauri::WebviewWindow, app: tauri::AppHandle) -> Result<(), String> {
    check(&window)?;
    let (config, data) = dirs(&app)?;
    let mut local = places::read_local(&config, &data);
    if !local.installed { return Err("这台电脑上还没有设置服务".into()); }
    local.run = true;
    local.held = false;
    local.held_until = None;
    places::write_local(&config, &local)?;
    crate::connect(app);
    Ok(())
}

#[tauri::command]
pub fn local_server_stop(window: tauri::WebviewWindow, app: tauri::AppHandle, forget: bool, minutes: Option<u64>) -> Result<(), String> {
    check(&window)?;
    let (config, data) = dirs(&app)?;
    let mut local = places::read_local(&config, &data);
    if forget {
        local.run = false;
        local.held = false;
        local.held_until = None;
    } else {
        local.held = true;
        local.held_until = minutes.map(|m| now_ms() + m.min(24 * 60) * 60_000);
    }
    places::write_local(&config, &local)?;
    stop_local(&app);
    if let (false, Some(until)) = (forget, local.held_until) {
        // 停一段时间：到点自己起来（期间有人点了「启动」或改了停多久，就不管了）
        let handle = app.clone();
        std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(until.saturating_sub(now_ms())));
            let Ok((config, data)) = dirs(&handle) else { return };
            let mut current = places::read_local(&config, &data);
            if current.held && current.held_until == Some(until) {
                current.held = false;
                current.held_until = None;
                if places::write_local(&config, &current).is_ok() { crate::connect(handle); }
            }
        });
    }
    Ok(())
}

#[tauri::command]
pub fn local_server_restart(window: tauri::WebviewWindow, app: tauri::AppHandle) -> Result<(), String> {
    check(&window)?;
    crate::connect(app);
    Ok(())
}

#[tauri::command]
pub fn local_server_reach(window: tauri::WebviewWindow, on: bool) -> Result<Value, String> {
    check(&window)?;
    // 经 Tailscale 让别的设备连进来：第 1 步的第 3 个 PR 做；现在如实回答做不到
    if on { Ok(json!({"ok": false, "problem": {"reason": "serve"}})) } else { Ok(json!({"ok": true, "url": ""})) }
}

#[tauri::command]
pub fn local_server_backup(window: tauri::WebviewWindow, app: tauri::AppHandle) -> Result<String, String> {
    check(&window)?;
    backup(&app)
}

#[tauri::command]
pub fn local_server_backups_set(window: tauri::WebviewWindow, app: tauri::AppHandle, nightly: bool, copy_to: Option<String>) -> Result<(), String> {
    check(&window)?;
    let (config, data) = dirs(&app)?;
    let mut local = places::read_local(&config, &data);
    if let Some(folder) = copy_to.as_deref() {
        if !Path::new(folder).is_absolute() || !Path::new(folder).is_dir() { return Err("要选一个已有的文件夹".into()); }
    }
    local.nightly = nightly;
    local.copy_to = copy_to;
    places::write_local(&config, &local)?;
    let _ = app.emit("local-server://status", status(&app));
    Ok(())
}

#[tauri::command]
pub fn local_server_restore(window: tauri::WebviewWindow, app: tauri::AppHandle, file: String) -> Result<(), String> {
    check(&window)?;
    restore(&app, &file)
}

#[tauri::command]
pub fn local_server_erase(window: tauri::WebviewWindow, app: tauri::AppHandle) -> Result<String, String> {
    check(&window)?;
    erase(&app)
}

#[tauri::command]
pub fn local_server_diagnostics(window: tauri::WebviewWindow, app: tauri::AppHandle) -> Result<String, String> {
    check(&window)?;
    let (_, data) = dirs(&app)?;
    let home = std::env::var_os("HOME").map(PathBuf::from);
    let log = std::fs::read_to_string(data.join("backend.log")).unwrap_or_default();
    let tail: Vec<&str> = log.lines().rev().take(40).collect();
    Ok(format!("VortoOne Dev {} · {} {}\n状态：{}\n\n这台电脑上的环境：\n{}\n\n后端日志（末尾）：\n{}",
               app.package_info().version, std::env::consts::OS, std::env::consts::ARCH, status(&app),
               places::tools_report(&places::tools(home.as_deref())),
               tail.into_iter().rev().collect::<Vec<_>>().join("\n")))
}

#[tauri::command]
pub fn tailscale_status(window: tauri::WebviewWindow) -> Result<Value, String> {
    check(&window)?;
    Ok(tailnet())
}

#[tauri::command]
pub fn tailscale_open(window: tauri::WebviewWindow, page: Option<String>) -> Result<(), String> {
    check(&window)?;
    let _ = page;
    if Path::new("/Applications/Tailscale.app").is_dir() {
        Command::new("open").arg("-a").arg("Tailscale").status().map_err(|e| e.to_string())?;
        Ok(())
    } else {
        tauri_plugin_opener::open_url("https://tailscale.com/download", None::<&str>).map_err(|e| e.to_string())
    }
}

#[tauri::command]
pub fn keep_running(window: tauri::WebviewWindow) -> Result<Value, String> {
    check(&window)?;
    Ok(json!({"autoLogin": Value::Null, "autoRestart": Value::Null}))
}

#[tauri::command]
pub fn reveal_folder(window: tauri::WebviewWindow, app: tauri::AppHandle, path: String) -> Result<(), String> {
    check(&window)?;
    // 只在 Finder 里显示 App 自己的数据目录里的东西
    let data = dirs(&app)?.1.canonicalize().map_err(|e| e.to_string())?;
    let target = Path::new(&path).canonicalize().map_err(|_| "没有这个文件夹".to_string())?;
    if !target.starts_with(&data) { return Err("只能显示 App 自己的数据".into()); }
    Command::new("open").arg("-R").arg(&target).status().map_err(|e| e.to_string())?;
    Ok(())
}

#[tauri::command]
pub fn quit_app(window: tauri::WebviewWindow, app: tauri::AppHandle) -> Result<(), String> {
    check(&window)?;
    app.exit(0);
    Ok(())
}

/// 第 1 步没有的能力：连接的电脑（运行器）、刘海、菜单栏、自动更新、系统权限、配对。窗口里对应的地方据此不显示；
/// 要新东西的（加运行器、配对、替页面发请求）如实回答没有。参数一律不读。
macro_rules! absent {
    ($($name:ident => $answer:expr),* $(,)?) => {
        $(
            #[tauri::command]
            pub fn $name(window: tauri::WebviewWindow) -> Result<Value, String> {
                check(&window)?;
                $answer
            }
        )*
    };
}

absent! {
    runners_list => Ok(json!([])),
    pick_folder => Ok(Value::Null),
    setup_state_get => Ok(Value::Null),
    notch_info => Ok(Value::Null),
    notch_place => Ok(Value::Null),
    autostart_get => Ok(json!(false)),
    update_install => Ok(json!(false)),
    screen_access => Ok(json!(false)),
    screen_request => Ok(json!(false)),
    disk_access => Ok(json!(false)),
    pair_ask => Ok(json!(false)),
    runner_send => Ok(Value::Null),
    runner_remove => Ok(Value::Null),
    set_tray_state => Ok(Value::Null),
    tray_visible => Ok(Value::Null),
    notify => Ok(Value::Null),
    update_cancel => Ok(Value::Null),
    notch_set => Ok(Value::Null),
    this_mac_reply => Ok(Value::Null),
    app_menu_set => Ok(Value::Null),
    this_mac_listening => Ok(Value::Null),
    notify_site_set => Ok(Value::Null),
    local_server_reach_stop => Ok(Value::Null),
    local_server_usage => Ok(Value::Null),
    local_server_sync => Ok(Value::Null),
    open_system_settings => Ok(Value::Null),
    privacy_open => Ok(Value::Null),
    setup_state_set => Ok(Value::Null),
    autostart_set => Ok(Value::Null),
    network_ask => Ok(Value::Null),
    runner_add => Err("这一版还不能把这台电脑接给 agent 用".into()),
    pair_link_info => Err("unreachable".into()),
    site_fetch => Err("这一版还没有这项功能".into()),
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_the_packaged_shell_page_may_call() {
        for (label, url) in [("welcome", "tauri://localhost/shell/index.html"), ("prefs", "http://tauri.localhost/shell/index.html")] {
            assert!(trusted(label, &url.parse().unwrap()), "{label} {url}");
        }
        for (label, url) in [("live", "tauri://localhost/shell/index.html"), ("welcome", "http://127.0.0.1:4321/shell/index.html"),
                             ("welcome", "tauri://localhost/model-settings.html"), ("prefs", "https://evil.example/shell/index.html")] {
            assert!(!trusted(label, &url.parse().unwrap()), "{label} {url}");
        }
    }

    #[test]
    fn pages_stay_on_the_server() {
        assert_eq!(page_path(None).unwrap(), "/");
        assert_eq!(page_path(Some("/login")).unwrap(), "/login");
        for bad in ["//evil.example/x", "https://evil.example", "/a\nb", "login"] {
            assert!(page_path(Some(bad)).is_err(), "{bad}");
        }
    }
}
