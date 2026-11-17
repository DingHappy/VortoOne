//! Tailscale：让手机和别的电脑经 Tailscale 连进这台电脑上的服务（`tailscale serve`，只在这个人的
//! tailnet 里、走 HTTPS），以及在 tailnet 里找服务（HOME_AND_MEMBERS_DESIGN 第 4 节、H6）。
//!
//! - 服务本身照旧只听 127.0.0.1；对外的是 tailscaled 代理，tailnet 以外够不着。
//! - 找服务只看 tailnet 里按约定起名的设备（`vortoone`、`vortoone-…`），不扫局域网。
//! - 只给地址，不替人判断：每个地址是不是 VortoOne、能不能用，由窗口自己去问它的 `/api/instance`。

use serde_json::Value;
use std::io::{BufRead, BufReader, Read};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::mpsc;
use std::time::{Duration, Instant};

/// 约定的名字：tailnet 里叫这个（或以 `vortoone-` 开头）的设备，可能跑着 VortoOne。
const NAME: &str = "vortoone";
const MAX_PEERS: usize = 5;
const STATUS_WAIT: Duration = Duration::from_secs(3);
/// 等人去 Tailscale 的管理页打开 HTTPS，最多等这么久。
const APPROVE_WAIT: Duration = Duration::from_secs(10 * 60);
const SERVE_WAIT: Duration = Duration::from_secs(30);

/// 打不开的原因，照窗口认的 `ServeProblem`。
#[derive(Debug, PartialEq)]
pub enum Problem {
    /// tailnet 里没开 HTTPS（或 Serve）：管理员到 `url` 打开。
    Https(Option<String>),
    /// Tailscale 拒绝或出错。
    Serve,
    /// 装了 Tailscale，但这台电脑没连上。
    NotConnected { app: bool },
    /// 人不等了。
    Stopped,
}

impl Problem {
    pub fn to_json(&self) -> Value {
        match self {
            Problem::Https(url) => serde_json::json!({"reason": "https", "url": url}),
            Problem::Serve => serde_json::json!({"reason": "serve"}),
            Problem::NotConnected { app } => serde_json::json!({"reason": "notConnected", "app": app}),
            Problem::Stopped => serde_json::json!({"reason": "stopped"}),
        }
    }
}

pub fn has_app() -> bool {
    Path::new("/Applications/Tailscale.app").is_dir()
}

/// Tailscale 的命令行：PATH 里的，或 Mac App 里自带的那一个。
pub fn cli() -> Option<PathBuf> {
    crate::places::find_command("tailscale", std::env::var_os("HOME").as_deref().map(Path::new)).or_else(|| {
        let bundled = PathBuf::from("/Applications/Tailscale.app/Contents/MacOS/Tailscale");
        bundled.is_file().then_some(bundled)
    })
}

fn command(cli: &Path) -> Command {
    let mut command = Command::new(cli);
    // Mac App 里的那个程序没有终端时会当成要开窗口：明说要命令行
    command.env("TAILSCALE_BE_CLI", "1").stdin(Stdio::null());
    command
}

/// 跑一次，限时；返回（成没成，输出）。输出在另一个线程里读，免得输出多了把管道塞满、程序退不出。
fn run(args: &[&str], limit: Duration) -> Option<(bool, String)> {
    let mut child = command(&cli()?).args(args).stdout(Stdio::piped()).stderr(Stdio::piped()).spawn().ok()?;
    let (mut out, mut err) = (child.stdout.take()?, child.stderr.take()?);
    let reader = std::thread::spawn(move || {
        let mut text = String::new();
        let _ = out.read_to_string(&mut text);
        let mut more = String::new();
        let _ = err.read_to_string(&mut more);
        text + &more
    });
    let started = Instant::now();
    let ok = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status.success(),
            Ok(None) if started.elapsed() < limit => std::thread::sleep(Duration::from_millis(50)),
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                return None;
            }
        }
    };
    Some((ok, reader.join().unwrap_or_default()))
}

pub fn status_json() -> Option<Value> {
    run(&["status", "--json"], STATUS_WAIT).and_then(|(_, out)| serde_json::from_str(&out).ok())
}

/// 这台电脑在 tailnet 里的名字（`box.tail1234.ts.net`）；没连上是 None。
pub fn device(status: &Value) -> Option<String> {
    if status["BackendState"] != "Running" {
        return None;
    }
    let name = status["Self"]["DNSName"].as_str()?.trim_end_matches('.').to_ascii_lowercase();
    host_name(&name).then_some(name)
}

/// 只认像样的主机名：字母、数字、连字符、点，不能空，不超长。tailnet 里别人的设备名不可信。
fn host_name(name: &str) -> bool {
    !name.is_empty() && name.len() <= 253 && name.split('.').all(|label| {
        !label.is_empty() && label.len() <= 63 && !label.starts_with('-')
            && label.chars().all(|c| c.is_ascii_alphanumeric() || c == '-')
    })
}

/// 给窗口的 Tailscale 状态（`Tailnet`）。
pub fn tailnet() -> Value {
    let app = has_app();
    if cli().is_none() {
        return if app { serde_json::json!({"state": "off", "app": true}) } else { serde_json::json!({"state": "missing"}) };
    }
    match status_json().as_ref().and_then(device) {
        Some(device) => serde_json::json!({"state": "on", "device": device}),
        None => serde_json::json!({"state": "off", "app": app}),
    }
}

/// 用哪个 HTTPS 端口：443，除非这台设备在 443 上已经在分享别的东西（那就 8443）；两个都被占了返回 None。
pub fn https_port(serve: Option<&Value>, device: &str, target: &str) -> Option<u16> {
    let taken = |port: u16| -> bool {
        let Some(web) = serve.and_then(|s| s["Web"].get(format!("{device}:{port}"))) else {
            return serve.is_some_and(|s| s["TCP"].get(port.to_string()).is_some());
        };
        // 已经是我们这个服务：接着用
        web["Handlers"].as_object().is_none_or(|handlers| {
            handlers.values().any(|h| h["Proxy"].as_str().map(|p| p.trim_end_matches('/')) != Some(target))
        })
    };
    [443, 8443].into_iter().find(|port| !taken(*port))
}

/// `serve` 让人去打开 HTTPS 的那个管理页地址。
pub fn admin_url(out: &str) -> Option<String> {
    out.split_whitespace()
        .find(|word| word.starts_with("https://login.tailscale.com/"))
        .map(|word| word.trim_end_matches(['.', ',', ')']).to_string())
}

pub fn public_url(device: &str, port: u16) -> String {
    if port == 443 { format!("https://{device}") } else { format!("https://{device}:{port}") }
}

/// 把 `http://127.0.0.1:<port>` 经 HTTPS 分享到 tailnet，返回对外地址。tailnet 里没开 HTTPS 时，
/// Tailscale 会给一个管理页地址并一直等：交给 `asks`，接着等到打开为止、人不等了（`stopped`）或超时。
pub fn serve(port: u16, asks: &mut dyn FnMut(&str), stopped: &dyn Fn() -> bool) -> Result<String, Problem> {
    let Some(cli) = cli() else { return Err(Problem::NotConnected { app: has_app() }) };
    let Some(device) = status_json().as_ref().and_then(device) else { return Err(Problem::NotConnected { app: has_app() }) };
    let target = format!("http://127.0.0.1:{port}");
    let current = run(&["serve", "status", "--json"], STATUS_WAIT).and_then(|(_, out)| serde_json::from_str(&out).ok());
    let https = https_port(current.as_ref(), &device, &target).ok_or(Problem::Serve)?;
    let flag = format!("--https={https}");
    let mut child = command(&cli)
        .args(["serve", "--bg", flag.as_str(), target.as_str()])
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|_| Problem::Serve)?;
    let (sender, lines) = mpsc::channel::<String>();
    for pipe in [child.stdout.take().map(|p| Box::new(p) as Box<dyn Read + Send>),
                 child.stderr.take().map(|p| Box::new(p) as Box<dyn Read + Send>)].into_iter().flatten() {
        let sender = sender.clone();
        std::thread::spawn(move || {
            for line in BufReader::new(pipe).lines().map_while(Result::ok) {
                if sender.send(line).is_err() { break; }
            }
        });
    }
    drop(sender);
    let started = Instant::now();
    let (mut asked, mut said) = (None::<String>, String::new());
    loop {
        while let Ok(line) = lines.try_recv() {
            if asked.is_none() {
                if let Some(url) = admin_url(&line) {
                    asks(&url);
                    asked = Some(url);
                }
            }
            if said.len() < 4096 { said.push_str(&line); said.push('\n'); }
        }
        match child.try_wait() {
            Ok(Some(status)) if status.success() => return Ok(public_url(&device, https)),
            Ok(Some(_)) => {
                let lower = said.to_ascii_lowercase();
                let https_off = lower.contains("https") && (lower.contains("not enabled") || lower.contains("certificate"));
                return Err(if asked.is_some() || https_off { Problem::Https(asked.or_else(|| admin_url(&said))) } else { Problem::Serve });
            }
            Ok(None) => {}
            Err(_) => return Err(Problem::Serve),
        }
        let limit = if asked.is_some() { APPROVE_WAIT } else { SERVE_WAIT };
        if stopped() || started.elapsed() > limit {
            let _ = child.kill();
            let _ = child.wait();
            return Err(if stopped() { Problem::Stopped } else { Problem::Https(asked) });
        }
        std::thread::sleep(Duration::from_millis(200));
    }
}

/// 不再分享 `public_url`（只关我们开的那个端口）。
pub fn unserve(public_url: &str) {
    let https = public_url.rsplit_once(':').and_then(|(_, p)| p.parse::<u16>().ok()).unwrap_or(443);
    let _ = run(&["serve", &format!("--https={https}"), "off"], SERVE_WAIT);
}

/// tailnet 里可能跑着 VortoOne 的地址：`vortoone.<tailnet>`，再是在线、名字是 `vortoone` 或以 `vortoone-`
/// 开头的设备（最多 5 台）。这台电脑自己不算，它由「这台电脑」一栏给。
pub fn candidates(status: &Value) -> Vec<String> {
    let mut out = Vec::new();
    let me = status["Self"]["DNSName"].as_str().unwrap_or_default().trim_end_matches('.').to_ascii_lowercase();
    if let Some(suffix) = status["CurrentTailnet"]["MagicDNSSuffix"].as_str().map(|s| s.trim_end_matches('.').to_ascii_lowercase()) {
        let name = format!("{NAME}.{suffix}");
        if host_name(&name) && name != me { out.push(format!("https://{name}")); }
    }
    let mut peers: Vec<String> = status["Peer"].as_object().into_iter().flat_map(|peers| peers.values())
        .filter(|peer| peer["Online"].as_bool().unwrap_or(false))
        .filter(|peer| {
            let host = peer["HostName"].as_str().unwrap_or_default().to_ascii_lowercase();
            host == NAME || host.starts_with(&format!("{NAME}-"))
        })
        .filter_map(|peer| peer["DNSName"].as_str().map(|d| d.trim_end_matches('.').to_ascii_lowercase()))
        .filter(|name| host_name(name) && *name != me)
        .collect();
    peers.sort();
    for name in peers.into_iter().take(MAX_PEERS) {
        let url = format!("https://{name}");
        if !out.contains(&url) { out.push(url); }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn this_device_only_when_connected_and_named_plainly() {
        assert_eq!(device(&json!({"BackendState": "Running", "Self": {"DNSName": "Box.tail1.ts.net."}})).as_deref(),
                   Some("box.tail1.ts.net"));
        assert_eq!(device(&json!({"BackendState": "Stopped", "Self": {"DNSName": "box.tail1.ts.net."}})), None);
        assert_eq!(device(&json!({"BackendState": "Running", "Self": {"DNSName": "evil name.ts.net."}})), None);
    }

    #[test]
    fn https_on_443_unless_something_else_is_there() {
        let target = "http://127.0.0.1:4321";
        assert_eq!(https_port(None, "box.ts.net", target), Some(443));
        let ours = json!({"Web": {"box.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:4321"}}}}});
        assert_eq!(https_port(Some(&ours), "box.ts.net", target), Some(443));
        let other = json!({"Web": {"box.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:3000"}}}}});
        assert_eq!(https_port(Some(&other), "box.ts.net", target), Some(8443));
        let both = json!({"TCP": {"8443": {}}, "Web": {"box.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:3000"}}}}});
        assert_eq!(https_port(Some(&both), "box.ts.net", target), None);
        assert_eq!(public_url("box.ts.net", 443), "https://box.ts.net");
        assert_eq!(public_url("box.ts.net", 8443), "https://box.ts.net:8443");
    }

    #[test]
    fn the_admin_page_tailscale_names() {
        let out = "Serve is not enabled on your tailnet.\nTo enable, visit:\n\n         https://login.tailscale.com/f/serve?node=abc\n";
        assert_eq!(admin_url(out).as_deref(), Some("https://login.tailscale.com/f/serve?node=abc"));
        assert_eq!(admin_url("see https://evil.example/login.tailscale.com/"), None);
    }

    #[test]
    fn the_tailnet_by_its_names_and_not_this_computer() {
        let status = json!({
            "Self": {"DNSName": "vortoone-me.tail1.ts.net."},
            "CurrentTailnet": {"MagicDNSSuffix": "tail1.ts.net"},
            "Peer": {
                "a": {"HostName": "vortoone-mini", "DNSName": "vortoone-mini.tail1.ts.net.", "Online": true},
                "b": {"HostName": "vortoone-old", "DNSName": "vortoone-old.tail1.ts.net.", "Online": false},
                "c": {"HostName": "ana-phone", "DNSName": "ana-phone.tail1.ts.net.", "Online": true},
                "d": {"HostName": "VortoOne", "DNSName": "evil name.tail1.ts.net.", "Online": true},
                "e": {"HostName": "vortoonex", "DNSName": "vortoonex.tail1.ts.net.", "Online": true},
                "f": {"HostName": "vortoone-me", "DNSName": "vortoone-me.tail1.ts.net.", "Online": true}
            }
        });
        assert_eq!(candidates(&status), vec!["https://vortoone.tail1.ts.net", "https://vortoone-mini.tail1.ts.net"]);
        assert!(candidates(&json!({})).is_empty());
    }
}
