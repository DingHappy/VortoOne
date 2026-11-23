//! 首次引导和偏好设置窗口（`frontend/apps/desktop` 的窗口界面，连真外壳那一份）要的宿主能力。
//!
//! 接口照上游桌面端窗口与外壳之间的约定（`host/tauri.ts`），实现是自己写的：参照它的 ADR 0066，
//! 不迁入对方的 Rust。第 1 步只实现「家的列表、首次引导、这台电脑上的服务」用得到的；其余能力（连接的电脑、
//! 刘海、菜单栏、更新）按「没有」回答，窗口里对应的地方不显示。
//!
//! 每个命令先核对调用的窗口：只认随包页面 `shell/index.html` 里标签是 welcome / prefs 的窗口。
//! 主窗口打开的是服务的网页，它调不到这里的任何命令。

use crate::{backend, places, reach};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tauri::{Emitter, Manager, WebviewUrl, WebviewWindowBuilder};

pub const PAGE: &str = "shell/index.html";
const WINDOWS: [&str; 2] = ["welcome", "prefs"];
const KEEP_BACKUPS: usize = 2;

#[derive(Default)]
pub struct ShellState {
    phase: Mutex<String>,
    welcome_mode: Mutex<Option<String>>,
    /// 别处交来、还没被欢迎窗口取走的连接链接（只留最新的一个）
    link: Mutex<Option<String>>,
    installing: AtomicBool,
    cancel: AtomicBool,
    /// 正在打开或关掉「让别的设备连进来」；人点了不等了。
    reaching: AtomicBool,
    reach_stop: AtomicBool,
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
    let reshare = local.public_url.is_some() && local.reach_port != Some(port);
    places::write_local(&config, &local)?;
    places::write_servers(&config, &list)?;
    set_phase(app, "running");
    if reshare {
        // 端口换了（上次的被占了）：Tailscale 那边改分享新端口；要人去开 HTTPS 的不在这里等
        let handle = app.clone();
        std::thread::spawn(move || {
            if reach::serve(port, &mut |_| {}, &|| true).is_ok() {
                let Ok((config, data)) = dirs(&handle) else { return };
                let mut local = places::read_local(&config, &data);
                local.reach_port = Some(port);
                let _ = places::write_local(&config, &local);
            }
        });
    }
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
    let reach = local.public_url.is_some();
    // 开着却连不上 Tailscale：别人这会儿连不进来
    let tailnet_down = reach && reach::tailnet()["state"] != "on";
    json!({
        "bundled": bundled,
        "installed": if local.installed { "app" } else { "none" },
        "run": local.run,
        // 所有者就是 App 保管的所有者密钥：装好就有
        "owner": local.installed,
        "phase": phase,
        "url": url,
        "publicUrl": local.public_url,
        "reach": reach,
        "usage": false,
        "version": app.package_info().version.to_string(),
        "dir": data.display().to_string(),
        "systemName": std::env::var("USER").ok(),
        "serving": false,
        "claim": Value::Null,
        "backTo": Value::Null,
        "held": local.held,
        "heldUntil": local.held_until,
        "tailnetDown": tailnet_down,
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
    crate::show_live(app, url)
}

fn choose(app: &tauri::AppHandle, server: &str, page: Option<&str>) -> Result<(), String> {
    let (config, data) = dirs(app)?;
    let origin = places::origin(server).ok_or("只能打开 https 地址，或这台电脑上的 http 地址")?;
    let mut list = places::read_servers(&config);
    list.choose(&origin)?;
    places::write_servers(&config, &list)?;
    let local = places::read_local(&config, &data);
    if local.url.as_deref() == Some(origin.as_str()) || local.is_public(&origin)
        || (places::is_loopback(&origin) && local.installed) {
        // A saved Tailscale alias points to this service. Handover goes to the live loopback child.
        let local_port = app.state::<backend::Backend>().local_port();
        if let Some(port) = local_port.filter(|_| phase(app) == "running") {
            let key = backend::owner_key(&data.join(places::OWNER_KEY_FILE))?;
            let url = backend::sign_in_url(port, &key)?.parse().map_err(|_| "登录地址无效".to_string())?;
            app.state::<crate::LiveAddress>().set(port);
            return crate::show_live(app, url);
        }
        crate::connect(app.clone());
        return Ok(());
    }
    if places::is_loopback(&origin) {
        // 这台电脑上别人起的服务：照样只走它自己的页面
        app.state::<crate::LiveAddress>().set_origin(&origin, 0);
        let url = format!("{origin}{}", page_path(page)?).parse().map_err(|_| "地址不对".to_string())?;
        return crate::show_live(app, url);
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
    let local = places::read_local(&config, &data);
    let bundled = app.path().resource_dir().is_ok_and(|root| {
        root.join("runtime").join(backend::BUNDLED_RUNTIME).is_file()
    });
    let owner_name = short(choice.name, 80);
    places::prepare_local_home(&data, bundled && !local.installed, owner_name.as_deref()).map_err(|e| e.to_string())?;
    backend::owner_key(&data.join(places::OWNER_KEY_FILE))?;
    emit_step(app, "database", "done", None);
    if cancelled() { return Err("cancelled".into()); }
    emit_step(app, "start", "active", None);
    let mut local = places::read_local(&config, &data);
    local.run = true;
    local.held = false;
    local.held_until = None;
    local.erased = None;
    if let Some(name) = owner_name { local.owner_name = Some(name); }
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
        emit_step(app, "reach", "active", None);
        let answer = set_reach(app, true);
        if answer["ok"] == true {
            emit_step(app, "reach", "done", None);
        } else {
            // 没打开不算装失败：服务照样能在这台电脑上用，之后在偏好设置里再开
            emit_step(app, "reach", "warn", Some(answer["problem"].clone()));
        }
    }
    Ok(())
}

// ── 让别的设备经 Tailscale 连进来 ─────────────────────────────────────────────

fn reach_stage(app: &tauri::AppHandle, stage: Value) {
    let _ = app.emit("local-server://reach", stage);
}

fn write_public(data: &Path, url: Option<&str>) -> Result<(), String> {
    let path = data.join(places::PUBLIC_URL_FILE);
    match url {
        Some(url) => std::fs::write(&path, url).map_err(|e| e.to_string()),
        None => match std::fs::remove_file(&path) {
            Err(error) if error.kind() != std::io::ErrorKind::NotFound => Err(error.to_string()),
            _ => Ok(()),
        },
    }
}

/// 打开或关掉；答复照窗口认的 `ReachResult`。打开后服务带着对外地址重启一次。
fn set_reach(app: &tauri::AppHandle, on: bool) -> Value {
    let problem = |reason: Value| json!({"ok": false, "problem": reason});
    let Ok((config, data)) = dirs(app) else { return problem(json!({"reason": "serve"})) };
    let state = app.state::<ShellState>();
    state.reach_stop.store(false, Ordering::SeqCst);
    let mut local = places::read_local(&config, &data);
    let running = phase(app) == "running";
    if !on {
        if let Some(url) = local.public_url.take() { reach::unserve(&url); }
        local.reach_port = None;
        if write_public(&data, None).and_then(|_| places::write_local(&config, &local)).is_err() {
            return problem(json!({"reason": "back"}));
        }
        if running {
            reach_stage(app, json!({"stage": "restarting"}));
            if crate::connect_now(app).is_err() { return problem(json!({"reason": "back"})); }
        }
        let back = places::read_local(&config, &data).url.unwrap_or_default();
        return json!({"ok": true, "url": back});
    }
    let Some(port) = local.url.as_deref().filter(|_| running).and_then(|u| u.rsplit(':').next()?.parse::<u16>().ok()) else {
        return problem(json!({"reason": "start"}));
    };
    reach_stage(app, json!({"stage": "sharing"}));
    let stop = || app.state::<ShellState>().reach_stop.load(Ordering::SeqCst);
    let public = match reach::serve(port, &mut |url| reach_stage(app, json!({"stage": "approve", "url": url})), &stop) {
        Ok(public) => public,
        Err(why) => return problem(why.to_json()),
    };
    local.public_url = Some(public.clone());
    local.reach_port = Some(port);
    if write_public(&data, Some(&public)).and_then(|_| places::write_local(&config, &local)).is_err() {
        return problem(json!({"reason": "serve"}));
    }
    reach_stage(app, json!({"stage": "restarting"}));
    if crate::connect_now(app).is_err() {
        return problem(json!({"reason": "start"}));
    }
    json!({"ok": true, "url": public})
}

fn stop_local(app: &tauri::AppHandle) {
    app.state::<backend::Backend>().stop();
    set_phase(app, "stopped");
}

fn private_backup_file(target: &Path) -> Result<std::fs::File, String> {
    let mut options = std::fs::OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)] {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let archive = options.open(target).map_err(|_| "无法创建私有备份文件，或同名备份已存在")?;
    #[cfg(unix)] {
        use std::os::unix::fs::PermissionsExt;
        archive.set_permissions(std::fs::Permissions::from_mode(0o600)).map_err(|_| "无法限制备份文件权限")?;
    }
    Ok(archive)
}

fn backup(app: &tauri::AppHandle) -> Result<String, String> {
    let (config, data) = dirs(app)?;
    let local = places::read_local(&config, &data);
    let was_running = phase(app) == "running";
    // 停着备份：数据库和文件不会写到一半
    if was_running { stop_local(app); }
    let result = (|| {
    let folder = data.join("backups");
    std::fs::create_dir_all(&folder).map_err(|e| e.to_string())?;
    let target = folder.join(format!("vortoone-{}.tar.gz", stamp()));
    let mut parts = vec!["home".to_string()];
    for extra in [places::OWNER_KEY_FILE, "model.env"] {
        if data.join(extra).is_file() { parts.push(extra.into()); }
    }
    let archive = private_backup_file(&target)?;
    // tar writes into the already-open 0600 file, with no public-permission creation window.
    let made = Command::new("tar").arg("-czf").arg("-").arg("-C").arg(&data).args(&parts)
        .stdin(std::process::Stdio::null()).stdout(std::process::Stdio::from(archive)).status();
    match made {
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
    }
    })();
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
    if !ask(app, "恢复这份家的备份", "恢复聊天、设置等家数据；备份中的额外文件另存保留，供你找回。当前家数据会在旁边保留。\n\n恢复后，已连接的电脑需要重新配对。", "确认恢复") {
        return Err("已取消恢复".into());
    }
    let was_running = phase(app) == "running";
    stop_local(app);
    let restored = crate::connector::restore_home(app, &data, &path);
    if restored.is_ok() || was_running { crate::connect(app.clone()); }
    restored
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
pub async fn discover(window: tauri::WebviewWindow, app: tauri::AppHandle) -> Result<Vec<Candidate>, String> {
    check(&window)?;
    let mut found = Vec::new();
    if phase(&app) == "running" {
        if let Some(url) = local_origin(&app) { found.push(Candidate { server: url, place: "here" }); }
    }
    // tailnet 里按约定起名的设备（`tailscale status` 最多等 3 秒：放到后台线程）
    let tailnet = tauri::async_runtime::spawn_blocking(|| reach::status_json().map(|s| reach::candidates(&s)))
        .await.ok().flatten().unwrap_or_default();
    found.extend(tailnet.into_iter().map(|server| Candidate { server, place: "tailnet" }));
    Ok(found)
}

// ── 连接链接（vortoone://connect）────────────────────────────────────────────
// 外壳只收、只转交：链接原样交给欢迎窗口，由它解析、核对那个服务、用大字显示地址并等人确认。
// 链接本身不能配对电脑、启动服务或安装任何东西（HOME_AND_MEMBERS_DESIGN 第 4 节）。

const LINK_MAX: usize = 2048;

/// 收不收这个链接：只收 `vortoone://connect`，2 KB 以内，没有空白和控制字符。别的一律丢掉不提示。
pub fn accepted_link(raw: &str) -> Option<String> {
    let text = raw.trim();
    if text.is_empty() || text.len() > LINK_MAX || text.chars().any(|c| c.is_control() || c.is_whitespace()) {
        return None;
    }
    let lower = text.to_ascii_lowercase();
    let rest = lower.strip_prefix("vortoone://")?;
    let after = rest.strip_prefix('/').unwrap_or(rest).strip_prefix("connect")?;
    matches!(after.chars().next(), None | Some('?') | Some('/') | Some('#')).then(|| text.to_string())
}

/// 系统交来一个链接（点了链接、扫了码）：留着，打开欢迎窗口，告诉它来取。
pub fn receive_link(app: &tauri::AppHandle, raw: &str) {
    let Some(link) = accepted_link(raw) else { return };
    if let Ok(mut slot) = app.state::<ShellState>().link.lock() {
        *slot = Some(link);
    }
    if let Err(error) = open_window(app, "welcome") {
        eprintln!("无法打开欢迎窗口: {error}");
    }
    let _ = app.emit("link://pending", ());
}

#[tauri::command]
pub fn link_take(window: tauri::WebviewWindow, app: tauri::AppHandle) -> Result<Option<String>, String> {
    check(&window)?;
    if window.label() != "welcome" {
        return Ok(None);                  // 只交给欢迎窗口
    }
    Ok(app.state::<ShellState>().link.lock().ok().and_then(|mut link| link.take()))
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
pub async fn local_server_reach(window: tauri::WebviewWindow, app: tauri::AppHandle, on: bool) -> Result<Value, String> {
    check(&window)?;
    if app.state::<ShellState>().reaching.swap(true, Ordering::SeqCst) { return Err("正在设置".into()); }
    let handle = app.clone();
    // 可能要等人去 Tailscale 的管理页打开 HTTPS：放到后台线程，不卡窗口
    let answer = tauri::async_runtime::spawn_blocking(move || set_reach(&handle, on)).await.map_err(|e| e.to_string());
    app.state::<ShellState>().reaching.store(false, Ordering::SeqCst);
    answer
}

#[tauri::command]
pub fn local_server_reach_stop(window: tauri::WebviewWindow, app: tauri::AppHandle) -> Result<(), String> {
    check(&window)?;
    app.state::<ShellState>().reach_stop.store(true, Ordering::SeqCst);
    Ok(())
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
pub async fn local_server_restore(window: tauri::WebviewWindow, app: tauri::AppHandle, file: String) -> Result<(), String> {
    check(&window)?;
    tauri::async_runtime::spawn_blocking(move || restore(&app, &file)).await.map_err(|_| "本机恢复未完成，请检查当前服务状态")?
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
pub async fn tailscale_status(window: tauri::WebviewWindow) -> Result<Value, String> {
    check(&window)?;
    tauri::async_runtime::spawn_blocking(reach::tailnet).await.map_err(|e| e.to_string())
}

/// Tailscale 的 Mac App、下载页，或 tailnet 管理里打开 HTTPS 的那一页。
#[tauri::command]
pub fn tailscale_open(window: tauri::WebviewWindow, page: Option<String>) -> Result<(), String> {
    check(&window)?;
    let url = match page.as_deref() {
        Some("https") => "https://login.tailscale.com/admin/dns",
        Some("app") | None if reach::has_app() => {
            Command::new("open").arg("-a").arg("Tailscale").status().map_err(|e| e.to_string())?;
            return Ok(());
        }
        _ => "https://tailscale.com/download",
    };
    tauri_plugin_opener::open_url(url, None::<&str>).map_err(|e| e.to_string())
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
    #[cfg(unix)]
    fn credential_backup_file_is_private_before_writing_and_cannot_replace_a_saved_backup() {
        use std::io::Write;
        use std::os::unix::fs::PermissionsExt;
        let root = std::env::temp_dir().join(format!("vortoone-private-archive-{}", crate::connector_secrets::random_id().unwrap()));
        std::fs::create_dir_all(&root).unwrap();
        let path = root.join("vortoone-backup.tar.gz");
        let mut archive = private_backup_file(&path).unwrap();
        assert_eq!(archive.metadata().unwrap().permissions().mode() & 0o777, 0o600);
        archive.write_all(b"private backup fixture").unwrap();
        drop(archive);
        assert!(private_backup_file(&path).is_err());
        assert_eq!(std::fs::read(&path).unwrap(), b"private backup fixture");
        std::fs::remove_dir_all(root).unwrap();
    }

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
    fn only_connect_links_are_taken() {
        for ok in ["vortoone://connect?server=https%3A%2F%2Fbox.tail1.ts.net#token=abc",
                   "VortoOne:///connect", "vortoone://connect/?server=x"] {
            assert_eq!(accepted_link(ok).as_deref(), Some(ok), "{ok}");
        }
        let long = format!("vortoone://connect?server=x&pad={}", "a".repeat(LINK_MAX));
        for no in ["vortoone://pair?server=x&link=ABCD-EFGH", "vortoone://connectx?server=x", "other://connect?server=x",
                   "https://box.example/connect", "vortoone://connect?server=x y", "vortoone://connect?server=\u{7}x", "", long.as_str()] {
            assert_eq!(accepted_link(no), None, "{no}");
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
