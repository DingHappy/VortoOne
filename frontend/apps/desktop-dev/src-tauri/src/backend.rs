//! 开发版连接真实后端：由 App 启动本机的 VortoOne 后端，换一次登录，再让主窗口打开它。
//!
//! 开发版不自带 Python 运行时。它读 App 配置目录里的 `backend.json`（指向一份源码和一个 Python），
//! 没有这个文件就保持原来的界面预览。数据放在开发版自己的目录里，不读正式版的账号、项目或运行状态。
//!
//! 主人令牌每次启动现生成、只在本进程和它启动的后端里，绝不交给页面：窗口拿到的是一次性的交接码
//! （`POST /api/auth/handover` → `GET /api/auth/handover/<码>`），后端核对后种下登录 Cookie。

use std::fs;
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

pub const CONFIG_FILE: &str = "backend.json";
const START_WITHIN: Duration = Duration::from_secs(90);
const IO_WAIT: Duration = Duration::from_secs(5);
/// 模型凭据等只从这些前缀里取，免得把配置文件当成往后端塞任意环境变量的口子。
const ENV_PREFIXES: [&str; 3] = ["OPENAI_", "VORTOONE_MODEL", "VORTOONE_LLM_"];
const MODEL_NAMES: [&str; 4] = ["DEFAULT_MODEL", "LLM_MODEL_CHEAP", "LLM_MODEL_BALANCED", "LLM_MODEL_POWERFUL"];

#[derive(Debug, PartialEq)]
pub struct Config {
    pub python: PathBuf,
    pub source: PathBuf,
    pub env_file: Option<PathBuf>,
}

#[derive(Default)]
pub struct Backend(pub Mutex<Option<Child>>);

impl Backend {
    pub fn stop(&self) {
        if let Ok(mut slot) = self.0.lock() {
            if let Some(mut child) = slot.take() {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    }
}

/// `"key": "value"` 里的 value。配置和后端的应答都只有几项平铺的字符串，不为它引入 JSON 库。
pub fn json_string(text: &str, key: &str) -> Option<String> {
    let start = text.find(&format!("\"{key}\""))? + key.len() + 2;
    let rest = text[start..].trim_start().strip_prefix(':')?.trim_start().strip_prefix('"')?;
    let mut out = String::new();
    let mut chars = rest.chars();
    while let Some(c) = chars.next() {
        match c {
            '"' => return Some(out),
            '\\' => match chars.next()? {
                'n' => out.push('\n'),
                't' => out.push('\t'),
                other => out.push(other),
            },
            other => out.push(other),
        }
    }
    None
}

pub fn parse_config(text: &str) -> Result<Config, String> {
    let absolute = |key: &str| -> Result<PathBuf, String> {
        let path = PathBuf::from(json_string(text, key).ok_or(format!("backend.json 缺少 {key}"))?);
        if path.is_absolute() {
            Ok(path)
        } else {
            Err(format!("backend.json 的 {key} 必须是绝对路径"))
        }
    };
    let env_file = match json_string(text, "env_file") {
        Some(_) => Some(absolute("env_file")?),
        None => None,
    };
    Ok(Config { python: absolute("python")?, source: absolute("source")?, env_file })
}

/// 环境文件里允许带给后端的几项（`KEY=VALUE`，可带引号；其余行忽略）。
pub fn allowed_env(text: &str) -> Vec<(String, String)> {
    dotenvy::from_read_iter(text.as_bytes())
        .filter_map(Result::ok)
        .filter(|(key, _)| ENV_PREFIXES.iter().any(|p| key.starts_with(p)) || MODEL_NAMES.contains(&key.as_str()))
        .collect()
}

fn free_port() -> Result<u16, String> {
    let listener = TcpListener::bind(("127.0.0.1", 0)).map_err(|e| e.to_string())?;
    Ok(listener.local_addr().map_err(|e| e.to_string())?.port())
}

fn new_token() -> Result<String, String> {
    let mut bytes = [0u8; 32];
    fs::File::open("/dev/urandom").and_then(|mut f| f.read_exact(&mut bytes)).map_err(|e| e.to_string())?;
    Ok(bytes.iter().map(|b| format!("{b:02x}")).collect())
}

/// 对本机后端的一次请求；返回 (状态码, 正文)。
fn request(port: u16, method: &str, path: &str, bearer: Option<&str>) -> Result<(u16, String), String> {
    let address = SocketAddr::from(([127, 0, 0, 1], port));
    let mut stream = TcpStream::connect_timeout(&address, IO_WAIT).map_err(|e| e.to_string())?;
    stream.set_read_timeout(Some(IO_WAIT)).map_err(|e| e.to_string())?;
    stream.set_write_timeout(Some(IO_WAIT)).map_err(|e| e.to_string())?;
    let auth = bearer.map(|t| format!("Authorization: Bearer {t}\r\n")).unwrap_or_default();
    let head = format!("{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n{auth}Content-Length: 0\r\nConnection: close\r\n\r\n");
    stream.write_all(head.as_bytes()).map_err(|e| e.to_string())?;
    let mut raw = String::new();
    stream.read_to_string(&mut raw).map_err(|e| e.to_string())?;
    parse_response(&raw).ok_or_else(|| "后端应答无法解析".to_string())
}

pub fn parse_response(raw: &str) -> Option<(u16, String)> {
    let status = raw.split_whitespace().nth(1)?.parse().ok()?;
    let body = raw.split_once("\r\n\r\n").map(|(_, body)| body).unwrap_or("");
    Some((status, body.to_string()))
}

pub fn command(config: &Config, home: &Path, ui: &Path, port: u16, token: &str) -> Command {
    let mut command = Command::new(&config.python);
    command.args(["-m", "src.runtime_entry", "server", "--host", "127.0.0.1", "--port"]).arg(port.to_string());
    command.current_dir(home);
    command.env("PYTHONPATH", &config.source);
    command.env("VORTOONE_WORKSPACE_SCOPE", "general");
    command.env("VORTOONE_APP_UI_DIR", ui);
    command.env("VORTOONE_API_TOKEN", token);
    // 监护进程 pid：App 被强杀时后端据此自己退出，不留占着端口的孤儿进程
    command.env("VORTOONE_SUPERVISOR_PID", std::process::id().to_string());
    for name in ["VORTOONE_IM", "VORTOONE_CRON", "VORTOONE_HEARTBEAT"] {
        command.env(name, if name == "VORTOONE_IM" { "" } else { "0" });
    }
    if let Some(text) = config.env_file.as_ref().and_then(|path| fs::read_to_string(path).ok()) {
        command.envs(allowed_env(&text));
    }
    command.stdin(Stdio::null());
    command
}

/// 启动后端并等它应答；返回 (子进程, 端口, 令牌)。
pub fn start(config: &Config, home: &Path, ui: &Path, log: &Path) -> Result<(Child, u16, String), String> {
    if !ui.join("index.html").is_file() {
        return Err("安装包里没有界面文件".into());
    }
    fs::create_dir_all(home).map_err(|e| e.to_string())?;
    let (port, token) = (free_port()?, new_token()?);
    let output = fs::File::create(log).map_err(|e| e.to_string())?;
    let mut child = command(config, home, ui, port, &token)
        .stdout(output.try_clone().map_err(|e| e.to_string())?)
        .stderr(output)
        .spawn()
        .map_err(|e| format!("后端启动失败：{e}"))?;
    let since = Instant::now();
    loop {
        if let Ok(Some(status)) = child.try_wait() {
            return Err(format!("后端已退出（{status}），详见日志"));
        }
        if matches!(request(port, "GET", "/api/health/quick", None), Ok((200, _))) {
            return Ok((child, port, token));
        }
        if since.elapsed() > START_WITHIN {
            let _ = child.kill();
            let _ = child.wait();
            return Err("后端在 90 秒内没有应答，详见日志".into());
        }
        std::thread::sleep(Duration::from_millis(300));
    }
}

/// 主窗口要打开的一次性登录地址。
pub fn sign_in_url(port: u16, token: &str) -> Result<String, String> {
    let (status, body) = request(port, "POST", "/api/auth/handover", Some(token))?;
    let code = json_string(&body, "code").filter(|c| status == 200 && good_code(c)).ok_or("后端没有给出交接码")?;
    Ok(format!("{}/api/auth/handover/{code}", origin(port)))
}

pub fn origin(port: u16) -> String {
    format!("http://127.0.0.1:{port}")
}

fn good_code(code: &str) -> bool {
    (20..=200).contains(&code.len()) && code.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
}

/// 主窗口只在后端自己的地址里走；别的地址交给系统浏览器。
pub fn own_page(url: &tauri::Url, port: u16) -> bool {
    url.scheme() == "http" && url.host_str() == Some("127.0.0.1") && url.port() == Some(port)
        && url.username().is_empty() && url.password().is_none()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn config_needs_absolute_paths() {
        let ok = parse_config(r#"{"python": "/usr/bin/python3", "source": "/Users/me/Vorto One", "env_file": "/Users/me/.env"}"#).unwrap();
        assert_eq!(ok.python, PathBuf::from("/usr/bin/python3"));
        assert_eq!(ok.source, PathBuf::from("/Users/me/Vorto One"));
        assert_eq!(ok.env_file, Some(PathBuf::from("/Users/me/.env")));
        assert_eq!(parse_config(r#"{"python":"/p","source":"/s"}"#).unwrap().env_file, None);
        assert!(parse_config(r#"{"python": "python3", "source": "/s"}"#).is_err());
        assert!(parse_config(r#"{"python": "/p", "source": "../s"}"#).is_err());
        assert!(parse_config(r#"{"python": "/p"}"#).is_err());
        assert!(parse_config(r#"{"python": "/p", "source": "/s", "env_file": "rel"}"#).is_err());
    }

    #[test]
    fn only_model_settings_pass_from_the_env_file() {
        let text = "# c\nOPENAI_API_KEY=\"k1\"\nexport OPENAI_API_BASE='http://x/v1'\nPATH=/evil\nVORTOONE_API_TOKEN=mine\nVORTOONE_ENABLE_SHELL=1\nVORTOONE_MODEL=m\nDEFAULT_MODEL=chosen\nLLM_MODEL_CHEAP=small\nLLM_MODEL_BALANCED=medium\nLLM_MODEL_POWERFUL=large\nLLM_MODEL_OTHER=no\nnot a line\n";
        assert_eq!(
            allowed_env(text),
            vec![
                ("OPENAI_API_KEY".to_string(), "k1".to_string()),
                ("OPENAI_API_BASE".to_string(), "http://x/v1".to_string()),
                ("VORTOONE_MODEL".to_string(), "m".to_string()),
                ("DEFAULT_MODEL".to_string(), "chosen".to_string()),
                ("LLM_MODEL_CHEAP".to_string(), "small".to_string()),
                ("LLM_MODEL_BALANCED".to_string(), "medium".to_string()),
                ("LLM_MODEL_POWERFUL".to_string(), "large".to_string()),
            ]
        );
    }

    #[test]
    fn dotenv_comments_do_not_become_part_of_the_model_url() {
        assert_eq!(allowed_env("OPENAI_API_BASE=http://example.test/v1   # gateway\nOPENAI_API_KEY=\"key#part\" # note\nDEFAULT_MODEL='chosen model' # note\n"), vec![
            ("OPENAI_API_BASE".into(), "http://example.test/v1".into()),
            ("OPENAI_API_KEY".into(), "key#part".into()),
            ("DEFAULT_MODEL".into(), "chosen model".into()),
        ]);
    }

    #[test]
    fn the_backend_is_started_private_and_supervised() {
        let config = Config { python: "/p".into(), source: "/s".into(), env_file: None };
        let built = command(&config, Path::new("/home"), Path::new("/ui"), 4321, "tok");
        let env: std::collections::HashMap<_, _> = built
            .get_envs()
            .map(|(k, v)| (k.to_string_lossy().to_string(), v.map(|v| v.to_string_lossy().to_string())))
            .collect();
        assert_eq!(env["VORTOONE_API_TOKEN"].as_deref(), Some("tok"));
        assert_eq!(env["VORTOONE_WORKSPACE_SCOPE"].as_deref(), Some("general"));
        assert_eq!(env["VORTOONE_APP_UI_DIR"].as_deref(), Some("/ui"));
        assert_eq!(env["VORTOONE_CRON"].as_deref(), Some("0"));
        assert_eq!(env["VORTOONE_SUPERVISOR_PID"], Some(std::process::id().to_string()));
        let args: Vec<_> = built.get_args().map(|a| a.to_string_lossy().to_string()).collect();
        assert_eq!(args, ["-m", "src.runtime_entry", "server", "--host", "127.0.0.1", "--port", "4321"]);
        assert_eq!(built.get_current_dir(), Some(Path::new("/home")));
    }

    #[test]
    fn replies_and_codes_are_read_strictly() {
        let raw = "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\n\r\n{\"code\":\"Ab0-_Ab0-_Ab0-_Ab0-_Ab0-_\",\"expiresIn\":60}";
        let (status, body) = parse_response(raw).unwrap();
        assert_eq!(status, 200);
        assert_eq!(json_string(&body, "code").as_deref(), Some("Ab0-_Ab0-_Ab0-_Ab0-_Ab0-_"));
        assert!(good_code("Ab0-_Ab0-_Ab0-_Ab0-_Ab0-_"));
        for bad in ["short", "has space has space has space", "x/../../../../../../etc/passwd", "a?b=cccccccccccccccccccc"] {
            assert!(!good_code(bad), "{bad}");
        }
        assert_eq!(json_string("{\"other\": 1}", "code"), None);
        assert_eq!(json_string("{\"code\": \"unterminated", "code"), None);
    }

    /// 真的启动一次后端并走完交接登录。要本机的 Python 和源码，默认不跑：
    /// `VORTO_DEV_PYTHON=… VORTO_DEV_SOURCE=… VORTO_DEV_UI=… cargo test -- --ignored`
    #[test]
    #[ignore]
    fn a_real_backend_starts_and_hands_over_a_sign_in() {
        let var = |name: &str| PathBuf::from(std::env::var(name).unwrap_or_else(|_| panic!("需要 {name}")));
        let config = Config { python: var("VORTO_DEV_PYTHON"), source: var("VORTO_DEV_SOURCE"), env_file: None };
        let scratch = std::env::temp_dir().join(format!("vorto-dev-live-{}", std::process::id()));
        let (mut child, port, token) =
            start(&config, &scratch.join("home"), &var("VORTO_DEV_UI"), &scratch.join("backend.log")).unwrap();
        let outcome = (|| {
            assert_eq!(request(port, "GET", "/api/app/me", None)?.0, 401);
            assert_eq!(request(port, "GET", "/api/app/me", Some(&token))?.0, 200);
            let url = sign_in_url(port, &token)?;
            assert!(own_page(&url.parse().unwrap(), port) && !url.contains(&token), "{url}");
            let path = url.strip_prefix(&origin(port)).unwrap().to_string();
            assert_eq!(request(port, "GET", &path, None)?.0, 303);
            assert_eq!(request(port, "GET", &path, None)?.0, 303); // 用过了：仍是跳转，但跳去登录页
            assert_eq!(request(port, "GET", "/", None)?.0, 200);
            Ok::<(), String>(())
        })();
        let _ = child.kill();
        let _ = child.wait();
        let _ = fs::remove_dir_all(&scratch);
        outcome.unwrap();
    }

    #[test]
    fn the_main_window_stays_on_the_backend() {
        for url in ["http://127.0.0.1:4321/", "http://127.0.0.1:4321/org/x/home?a=1#b"] {
            assert!(own_page(&url.parse().unwrap(), 4321), "{url}");
        }
        for url in [
            "http://127.0.0.1:4322/",
            "https://127.0.0.1:4321/",
            "http://localhost:4321/",
            "http://127.0.0.1.evil.example:4321/",
            "http://user@127.0.0.1:4321/",
            "https://github.com/",
            "file:///etc/passwd",
        ] {
            assert!(!own_page(&url.parse().unwrap(), 4321), "{url}");
        }
    }
}
