//! 家放在哪（HOME_AND_MEMBERS_DESIGN 第 4 节）：这个 App 打开的服务列表、当前打开哪一个，以及这台电脑上
//! 由 App 自己管的那个服务的状态。参照上游 ADR 0066 的做法，独立实现。
//!
//! - `servers.json`（配置目录）：列表与当前的那一个。只收 https，或本机回环上的 http。
//! - `local-server.json`（配置目录）：这台电脑上的服务装没装、要不要随 App 启动、被人停了多久、备份设置。
//!   装过开发版、还没有这个文件的（数据目录里已有 `home/`），按「已装、随 App 启动」接着用，行为不变。
//! - 这里不启动任何东西，也不碰网络；进程与窗口在 `shell.rs`。

use serde::{Deserialize, Serialize};
use std::fs;
use std::io::{ErrorKind, Read};
use std::path::{Path, PathBuf};

pub const SERVERS_FILE: &str = "servers.json";
pub const LOCAL_FILE: &str = "local-server.json";
pub const OWNER_KEY_FILE: &str = "owner-key";
/// 经 Tailscale 对外的地址，后端启动时读它设 `VORTOONE_PUBLIC_URL`（外壳写；没开就没有这个文件）。
pub const PUBLIC_URL_FILE: &str = "public-url";
const MAX_SERVERS: usize = 50;
const DESKTOP_SETUP: &str = ".vortoone/desktop-setup.json";
const LEGACY_DESKTOP_SETUP: &str = ".vortoone-desktop-setup.json";
const MAX_SETUP_BYTES: u64 = 8192;

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct ServerList {
    pub current: Option<String>,
    pub servers: Vec<String>,
}

/// 一个地址的规范写法（scheme://host[:port]，不带路径）；不收的返回 None。
/// 只收 https，或本机回环（127.0.0.1 / localhost / [::1]）上的 http：别的网络上不用明文传登录凭据。
pub fn origin(raw: &str) -> Option<String> {
    let url: tauri::Url = raw.trim().parse().ok()?;
    if !url.username().is_empty() || url.password().is_some() {
        return None;
    }
    let host = url.host_str()?.to_ascii_lowercase();
    let loopback = matches!(host.as_str(), "127.0.0.1" | "localhost" | "[::1]");
    match url.scheme() {
        "https" => {}
        "http" if loopback => {}
        _ => return None,
    }
    let port = url.port().map(|p| format!(":{p}")).unwrap_or_default();
    Some(format!("{}://{host}{port}", url.scheme()))
}

pub fn is_loopback(server: &str) -> bool {
    server.starts_with("http://127.0.0.1:") || server.starts_with("http://localhost:") || server.starts_with("http://[::1]:")
}

pub fn read_servers(config: &Path) -> ServerList {
    let parsed: ServerList = fs::read_to_string(config.join(SERVERS_FILE)).ok()
        .and_then(|text| serde_json::from_str(&text).ok()).unwrap_or_default();
    // 读到的也过一遍规矩：手改坏的地址不进窗口
    let servers: Vec<String> = parsed.servers.iter().filter_map(|s| origin(s)).fold(Vec::new(), |mut all, s| {
        if !all.contains(&s) { all.push(s); }
        all
    });
    let current = parsed.current.and_then(|c| origin(&c)).filter(|c| servers.contains(c));
    ServerList { current, servers }
}

pub fn write_servers(config: &Path, list: &ServerList) -> Result<(), String> {
    write_json(&config.join(SERVERS_FILE), list)
}

impl ServerList {
    /// 加进列表并设为当前的那一个。
    pub fn choose(&mut self, server: &str) -> Result<(), String> {
        let server = origin(server).ok_or("只能打开 https 地址，或这台电脑上的 http 地址")?;
        if !self.servers.contains(&server) {
            if self.servers.len() >= MAX_SERVERS {
                return Err("列表里的服务太多了，先移除一些".into());
            }
            self.servers.push(server.clone());
        }
        self.current = Some(server);
        Ok(())
    }

    pub fn remove(&mut self, server: &str) {
        let Some(server) = origin(server) else { return };
        self.servers.retain(|s| s != &server);
        if self.current.as_deref() == Some(server.as_str()) {
            self.current = None;
        }
    }

    /// 这台电脑上的服务换了地址（端口被占后换了一个）：列表里旧的那条换成新的，当前的跟着换。
    pub fn moved(&mut self, old: Option<&str>, new: &str) {
        let Some(new) = origin(new) else { return };
        if let Some(old) = old.and_then(origin) {
            if old != new {
                for item in self.servers.iter_mut() {
                    if *item == old { *item = new.clone(); }
                }
                if self.current.as_deref() == Some(old.as_str()) {
                    self.current = Some(new.clone());
                }
                let mut seen = Vec::new();
                self.servers.retain(|s| if seen.contains(s) { false } else { seen.push(s.clone()); true });
            }
        }
    }
}

/// 这台电脑上的服务，App 记着的部分。
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", default)]
pub struct LocalState {
    pub installed: bool,
    /// 随 App 启动。
    pub run: bool,
    /// 被人停了：到 Start 为止，或到 `held_until`（毫秒）为止。
    pub held: bool,
    pub held_until: Option<u64>,
    /// 上一次在用的地址（换端口时据此改列表）。
    pub url: Option<String>,
    pub nightly: bool,
    pub copy_to: Option<String>,
    /// 清空重来时旧数据挪到了哪里。
    pub erased: Option<String>,
    /// 首次引导填的所有者名字和邮箱。所有者是 App 保管的所有者密钥，登录不靠它们；留给以后的账号与云端。
    pub owner_name: Option<String>,
    pub owner_email: Option<String>,
    /// 经 Tailscale 让别的设备连进来：开着时对外的地址，和分享的是哪个本机端口。
    pub public_url: Option<String>,
    pub reach_port: Option<u16>,
}

impl Default for LocalState {
    fn default() -> Self {
        LocalState { installed: false, run: false, held: false, held_until: None, url: None, nightly: true,
                     copy_to: None, erased: None, owner_name: None, owner_email: None, public_url: None,
                     reach_port: None }
    }
}

pub fn read_local(config: &Path, data: &Path) -> LocalState {
    if let Some(found) = fs::read_to_string(config.join(LOCAL_FILE)).ok()
        .and_then(|text| serde_json::from_str::<LocalState>(&text).ok()) {
        return found;
    }
    // 早于这个文件的开发版：数据目录里有 home/ 就是装过、一直随 App 启动的，照旧
    let existing = data.join("home").is_dir();
    LocalState { installed: existing, run: existing, ..LocalState::default() }
}

pub fn write_local(config: &Path, state: &LocalState) -> Result<(), String> {
    write_json(&config.join(LOCAL_FILE), state)
}

/// Only an install that creates a new home may put the service into first-run setup.
pub fn prepare_local_home(data: &Path, seed_setup: bool, owner_display_name: Option<&str>) -> std::io::Result<()> {
    fs::create_dir_all(data)?;
    let home = data.join("home");
    match fs::create_dir(&home) {
        Ok(()) if seed_setup => {
            let path = home.join(DESKTOP_SETUP);
            let state = path.parent().expect("setup marker has a parent");
            let mut options = fs::OpenOptions::new();
            options.write(true).create_new(true);
            #[cfg(unix)]
            {
                use std::os::unix::fs::OpenOptionsExt;
                options.mode(0o600);
            }
            let result = (|| {
                fs::create_dir(state)?;
                let mut file = options.open(&path)?;
                let result = (|| {
                    let mut setup = serde_json::json!({"step": "purpose"});
                    if let Some(name) = owner_display_name {
                        setup["ownerDisplayName"] = serde_json::json!(name);
                    }
                    serde_json::to_writer(&mut file, &setup).map_err(std::io::Error::other)?;
                    file.sync_all()?;
                    #[cfg(unix)]
                    for directory in [state, &home, data] {
                        fs::File::open(directory)?.sync_all()?;
                    }
                    Ok(())
                })();
                drop(file);
                if result.is_err() { let _ = fs::remove_file(&path); }
                result
            })();
            if result.is_err() {
                let _ = fs::remove_dir(state);
                let _ = fs::remove_dir(&home);
            }
            result
        }
        Ok(()) => Ok(()),
        Err(error) if error.kind() == ErrorKind::AlreadyExists && home.is_dir() => Ok(()),
        Err(error) => Err(error),
    }
}

pub fn desktop_setup_at_model(home: &Path) -> bool {
    let mut path = home.join(DESKTOP_SETUP);
    if path.symlink_metadata().is_err_and(|error| error.kind() == ErrorKind::NotFound) {
        path = home.join(LEGACY_DESKTOP_SETUP);
    }
    let Ok(metadata) = path.symlink_metadata() else { return false; };
    if !metadata.is_file() || metadata.len() > MAX_SETUP_BYTES { return false; }
    let mut data = Vec::new();
    if fs::File::open(path).and_then(|file| file.take(MAX_SETUP_BYTES + 1).read_to_end(&mut data)).is_err()
        || data.len() as u64 > MAX_SETUP_BYTES {
        return false;
    }
    serde_json::from_slice::<serde_json::Value>(&data).is_ok_and(|value| {
        let step = value.get("step").and_then(|step| step.as_str());
        match value.get("mode").and_then(|mode| mode.as_str()) {
            Some("personal") => matches!(step, Some("model" | "presets" | "about" | "team")),
            Some("team") if value.get("name").and_then(|name| name.as_str()).is_some_and(|name| !name.trim().is_empty()) =>
                matches!(step, Some("model" | "presets" | "about" | "team" | "invite")),
            _ => false,
        }
    })
}

impl LocalState {
    /// `server` 是不是这台电脑上的服务经 Tailscale 对外的地址：那还是这里，照本机的办法打开和登录。
    pub fn is_public(&self, server: &str) -> bool {
        self.installed && self.public_url.as_deref().is_some_and(|url| origin(url).as_deref() == origin(server).as_deref())
    }

    /// 现在该不该随 App 启动：装了、要随 App 启动、没被人停着（停一段时间的到点了就不算停着）。
    pub fn should_start(&self, now_ms: u64) -> bool {
        self.installed && self.run && !(self.held && self.held_until.is_none_or(|until| now_ms < until))
    }
}

/// App 刚打开时做什么。
#[derive(Debug, PartialEq)]
pub enum Opening {
    /// 启动这台电脑上的服务，主窗口打开它。
    Local,
    /// 主窗口打开别处的这个服务（它自己的登录页）。
    Remote(String),
    /// 什么都还没选：首次引导。
    Welcome,
}

pub fn opening(list: &ServerList, local: &LocalState, now_ms: u64) -> Opening {
    let local_url = local.url.as_deref().and_then(origin);
    match &list.current {
        Some(current) if local_url.as_deref() == Some(current.as_str()) || local.is_public(current)
            || (is_loopback(current) && local.installed) => {
            if local.should_start(now_ms) { Opening::Local } else { Opening::Welcome }
        }
        Some(current) if !is_loopback(current) => Opening::Remote(current.clone()),
        _ if local.should_start(now_ms) => Opening::Local,
        _ => Opening::Welcome,
    }
}

fn write_json<T: Serialize>(path: &Path, value: &T) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let text = serde_json::to_string_pretty(value).map_err(|e| e.to_string())?;
    let temporary = path.with_extension("tmp");
    fs::write(&temporary, text).map_err(|e| e.to_string())?;
    fs::rename(&temporary, path).map_err(|e| e.to_string())
}

// ── 环境检查：这台电脑上有哪些 agent 干活会用到的东西 ─────────────────────────────

#[derive(Clone, Debug, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct Tool {
    pub id: &'static str,
    pub name: &'static str,
    /// 找到的路径；没找到为 None。
    pub path: Option<String>,
    /// 没有它时，哪类活干不了。
    pub needed_for: &'static str,
    /// 怎么装。
    pub install: &'static str,
}

const BROWSERS: [&str; 4] = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
];

/// 在 PATH 和几个常见位置里找一个命令。App 从 Finder 打开时 PATH 很短，所以另看用户目录和 Homebrew。
pub fn find_command(name: &str, home: Option<&Path>) -> Option<PathBuf> {
    let mut dirs: Vec<PathBuf> = std::env::var_os("PATH").map(|p| std::env::split_paths(&p).collect()).unwrap_or_default();
    dirs.extend(["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"].map(PathBuf::from));
    if let Some(home) = home {
        dirs.extend([".local/bin", ".claude/local", ".opencode/bin", ".npm-global/bin"].map(|d| home.join(d)));
    }
    dirs.into_iter().map(|d| d.join(name)).find(|p| p.is_file())
}

pub fn tools(home: Option<&Path>) -> Vec<Tool> {
    let browser = std::env::var("VORTOONE_BROWSER_PATH").ok().filter(|p| Path::new(p).is_file())
        .or_else(|| BROWSERS.iter().find(|p| Path::new(p).is_file()).map(|p| p.to_string()));
    let command = |name: &str| find_command(name, home).map(|p| p.display().to_string());
    // macOS 的 /usr/bin/git 在没装命令行工具时只是个提示安装的壳：要看命令行工具在不在
    let git = command("git").filter(|p| p != "/usr/bin/git" || Path::new("/Library/Developer/CommandLineTools/usr/bin/git").is_file()
                                           || Path::new("/Applications/Xcode.app").is_dir());
    vec![
        Tool { id: "browser", name: "Chrome 一类浏览器", path: browser, needed_for: "agent 操作网页",
               install: "装 Google Chrome（或 Chromium、Edge、Brave）" },
        Tool { id: "git", name: "git", path: git, needed_for: "开发 agent 在项目副本里改代码",
               install: "在终端运行 xcode-select --install" },
        Tool { id: "claude", name: "Claude Code", path: command("claude"), needed_for: "agent 用 Claude Code 当执行引擎",
               install: "按 Anthropic 的说明安装，并运行 claude 登录" },
        Tool { id: "opencode", name: "OpenCode", path: command("opencode"), needed_for: "开发 agent 用 OpenCode 当执行引擎",
               install: "按 opencode.ai 的说明安装" },
        Tool { id: "tailscale", name: "Tailscale", path: command("tailscale")
                   .or_else(|| Path::new("/Applications/Tailscale.app").is_dir().then(|| "/Applications/Tailscale.app".into())),
               needed_for: "手机和别的电脑连进这台电脑上的服务", install: "从 tailscale.com 下载安装并登录" },
    ]
}

pub fn tools_report(tools: &[Tool]) -> String {
    tools.iter().map(|t| match &t.path {
        Some(path) => format!("✓ {}：{}", t.name, path),
        None => format!("✗ {}：没有找到（{}要用；{}）", t.name, t.needed_for, t.install),
    }).collect::<Vec<_>>().join("\n")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("vortoone-places-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn only_https_or_this_computer_in_the_clear() {
        assert_eq!(origin("https://One.Example.com/x?y").as_deref(), Some("https://one.example.com"));
        assert_eq!(origin("http://127.0.0.1:4321/").as_deref(), Some("http://127.0.0.1:4321"));
        assert_eq!(origin("http://localhost:8080").as_deref(), Some("http://localhost:8080"));
        for bad in ["http://192.168.1.2:8080", "http://example.com", "ftp://x", "https://u:p@x.com", "javascript:x", ""] {
            assert_eq!(origin(bad), None, "{bad}");
        }
    }

    #[test]
    fn choosing_adds_once_and_removing_clears_current() {
        let mut list = ServerList::default();
        list.choose("https://a.example/").unwrap();
        list.choose("https://a.example").unwrap();
        list.choose("http://127.0.0.1:4000").unwrap();
        assert_eq!(list.servers, ["https://a.example", "http://127.0.0.1:4000"]);
        assert_eq!(list.current.as_deref(), Some("http://127.0.0.1:4000"));
        assert!(list.choose("http://10.0.0.2:80").is_err());
        list.remove("http://127.0.0.1:4000/");
        assert_eq!(list.current, None);
        assert_eq!(list.servers, ["https://a.example"]);
    }

    #[test]
    fn a_new_local_port_replaces_the_old_entry() {
        let mut list = ServerList { current: Some("http://127.0.0.1:4000".into()),
                                    servers: vec!["https://a.example".into(), "http://127.0.0.1:4000".into()] };
        list.moved(Some("http://127.0.0.1:4000"), "http://127.0.0.1:4011");
        assert_eq!(list.servers, ["https://a.example", "http://127.0.0.1:4011"]);
        assert_eq!(list.current.as_deref(), Some("http://127.0.0.1:4011"));
    }

    #[test]
    fn files_survive_a_round_trip_and_bad_entries_are_dropped() {
        let dir = temp("servers");
        fs::write(dir.join(SERVERS_FILE), r#"{"current":"http://evil.example","servers":["https://a.example","http://evil.example","https://a.example/"]}"#).unwrap();
        let list = read_servers(&dir);
        assert_eq!(list, ServerList { current: None, servers: vec!["https://a.example".into()] });
        write_servers(&dir, &list).unwrap();
        assert_eq!(read_servers(&dir), list);
    }

    #[test]
    fn an_existing_dev_install_keeps_starting_its_local_server() {
        let (config, data) = (temp("cfg"), temp("data"));
        assert!(!read_local(&config, &data).installed);
        fs::create_dir_all(data.join("home")).unwrap();
        let state = read_local(&config, &data);
        assert!(state.installed && state.run);
        assert_eq!(opening(&ServerList::default(), &state, 0), Opening::Local);
    }

    #[test]
    fn a_bundled_install_seeds_only_the_new_home_and_leaves_older_progress_intact() {
        let fresh = temp("fresh-setup");
        prepare_local_home(&fresh, true, Some("新安装的所有者")).unwrap();
        let home = fresh.join("home");
        let marker = home.join(DESKTOP_SETUP);
        let setup: serde_json::Value = serde_json::from_slice(&fs::read(&marker).unwrap()).unwrap();
        assert_eq!(setup, serde_json::json!({"step": "purpose", "ownerDisplayName": "新安装的所有者"}));
        assert!(!desktop_setup_at_model(&home));
        fs::write(&marker, r#"{"step":"model","mode":"team","name":"团队"}"#).unwrap();
        prepare_local_home(&fresh, true, Some("不能覆盖")).unwrap();
        assert_eq!(fs::read_to_string(&marker).unwrap(), r#"{"step":"model","mode":"team","name":"团队"}"#);
        assert!(desktop_setup_at_model(&home));
        fs::write(&marker, r#"{"step":"done","mode":"team"}"#).unwrap();
        assert!(!desktop_setup_at_model(&home));
        fs::write(&marker, r#"{"step":"about","mode":"team","name":"团队"}"#).unwrap();
        assert!(desktop_setup_at_model(&home));
        fs::write(&marker, r#"{"step":"invite","mode":"team","name":"团队"}"#).unwrap();
        assert!(desktop_setup_at_model(&home));
        fs::write(&marker, r#"{"step":"team","mode":"personal"}"#).unwrap();
        assert!(desktop_setup_at_model(&home));
        fs::write(&marker, r#"{"step":"presets","mode":"personal"}"#).unwrap();
        assert!(desktop_setup_at_model(&home));
        fs::write(&marker, r#"{"step":"model","mode":"team"}"#).unwrap();
        assert!(!desktop_setup_at_model(&home));

        let old = temp("existing-setup");
        prepare_local_home(&old, false, Some("不能给现有工作区补录")).unwrap();
        prepare_local_home(&old, true, Some("不能给现有工作区补录")).unwrap();
        let old_home = old.join("home");
        assert!(!old_home.join(DESKTOP_SETUP).exists());
        fs::write(old_home.join(LEGACY_DESKTOP_SETUP), r#"{"step":"model","mode":"personal"}"#).unwrap();
        assert!(desktop_setup_at_model(&old_home));
        let _ = fs::remove_dir_all(fresh);
        let _ = fs::remove_dir_all(old);
    }

    #[test]
    fn stable_directories_start_a_fresh_home_alongside_legacy_state() {
        use tauri::utils::config::{AppDirectoriesOverride, Config};

        let dev: Config = serde_json::from_str(include_str!("../tauri.conf.json")).unwrap();
        let stable: Config = serde_json::from_str(include_str!("../tauri.stable.conf.json")).unwrap();
        assert!(dev.app.app_directories_override.is_none());
        let Some(AppDirectoriesOverride::Directories(directories)) = stable.app.app_directories_override else {
            panic!("stable configuration must override its own directories");
        };

        let base = temp("stable-profile");
        let legacy = base.join("com.vorto.vortoone");
        fs::create_dir(&legacy).unwrap();
        let old_model = legacy.join("llm-profile.json");
        let old_marker = legacy.join(LOCAL_FILE);
        fs::write(&old_model, b"fixture-model-profile").unwrap();
        fs::write(&old_marker, br#"{"installed":true,"run":true}"#).unwrap();

        // Tauri substitutes these platform bases. Both may point to the same
        // directory on macOS, as in this fixture.
        let scoped_path = |path: Option<PathBuf>, variable| {
            base.join(path.unwrap().strip_prefix(variable).unwrap())
        };
        let config = scoped_path(directories.config, "$CONFIG");
        let data = scoped_path(directories.data, "$DATA");
        let local_data = scoped_path(directories.local_data, "$LOCALDATA");
        for path in [&config, &data, &local_data] {
            assert_eq!(path, &legacy.join("desktop-home"));
        }
        assert_eq!(opening(&read_servers(&config), &read_local(&config, &data), 0), Opening::Welcome);
        assert!(crate::model_settings::env_file(&config, &data).is_none());
        prepare_local_home(&data, true, Some("New stable owner")).unwrap();
        assert!(data.join("home").join(DESKTOP_SETUP).is_file());
        assert!(!legacy.join("home").exists());
        assert_eq!(fs::read(old_model).unwrap(), b"fixture-model-profile");
        assert_eq!(fs::read(old_marker).unwrap(), br#"{"installed":true,"run":true}"#);
        fs::remove_dir_all(base).unwrap();
    }

    #[test]
    fn what_opens_first() {
        let installed = LocalState { installed: true, run: true, url: Some("http://127.0.0.1:4000".into()), ..LocalState::default() };
        let none = LocalState::default();
        let remote = ServerList { current: Some("https://a.example".into()), servers: vec!["https://a.example".into()] };
        let here = ServerList { current: Some("http://127.0.0.1:4000".into()), servers: vec!["http://127.0.0.1:4000".into()] };
        assert_eq!(opening(&ServerList::default(), &none, 0), Opening::Welcome);
        assert_eq!(opening(&remote, &none, 0), Opening::Remote("https://a.example".into()));
        assert_eq!(opening(&remote, &installed, 0), Opening::Remote("https://a.example".into()));
        assert_eq!(opening(&here, &installed, 0), Opening::Local);
        let held = LocalState { held: true, held_until: Some(1000), ..installed.clone() };
        assert_eq!(opening(&here, &held, 999), Opening::Welcome);
        assert_eq!(opening(&here, &held, 1000), Opening::Local);
        let stopped = LocalState { held: true, held_until: None, ..installed.clone() };
        assert_eq!(opening(&here, &stopped, u64::MAX), Opening::Welcome);
        // 选的是这里经 Tailscale 对外的地址：还是这里，照本机的办法打开
        let shared = LocalState { public_url: Some("https://box.tail1.ts.net".into()), ..installed };
        let public = ServerList { current: Some("https://box.tail1.ts.net/".into()), servers: vec![] };
        assert!(shared.is_public("https://box.tail1.ts.net") && !shared.is_public("https://other.tail1.ts.net"));
        assert_eq!(opening(&public, &shared, 0), Opening::Local);
        assert!(!LocalState { installed: false, ..shared }.is_public("https://box.tail1.ts.net"));
    }

    #[test]
    fn the_tools_report_says_what_is_missing_and_how_to_get_it() {
        let report = tools_report(&[
            Tool { id: "git", name: "git", path: Some("/usr/bin/git".into()), needed_for: "x", install: "y" },
            Tool { id: "claude", name: "Claude Code", path: None, needed_for: "用它当引擎", install: "去装" },
        ]);
        assert_eq!(report, "✓ git：/usr/bin/git\n✗ Claude Code：没有找到（用它当引擎要用；去装）");
    }
}
