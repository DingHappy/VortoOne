//! C1: identities and an outbound control connection only. No tool or runner protocol is exposed.
use crate::connector_secrets::{self as secrets, SecretRef, SecretStore, Storage, SystemStore};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    fs,
    io::{BufRead, BufReader, Write},
    path::{Path, PathBuf},
    process::{Child, ChildStdin, Command, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering},
        mpsc, Mutex,
    },
    time::{Duration, Instant},
};
use tauri::Manager;

const WINDOW: &str = "computers";
const PAGE: &str = "/computers.html";
const MAX_FRAME: usize = 8192;
const MAX_RECORDS: usize = 16;

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Registration {
    origin: String,
    home_instance_id: String,
    connector_id: String,
    name: String,
    paired_principal_id: String,
    credential_epoch: String,
    trust_epoch: String,
    storage: Storage,
    enabled: bool,
}
impl Registration {
    fn key(&self) -> SecretRef {
        SecretRef {
            home: self.home_instance_id.clone(),
            connector: self.connector_id.clone(),
        }
    }
    fn request(&self, token: String, allow_loopback_http: bool) -> Value {
        json!({"operation":"connect", "origin":self.origin, "home_instance_id":self.home_instance_id,
            "connector_id":self.connector_id, "paired_principal_id":self.paired_principal_id,
            "credential_epoch":self.credential_epoch, "trust_epoch":self.trust_epoch,
            "allow_loopback_http":allow_loopback_http, "token":token})
    }
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Configuration {
    installation_id: String,
    registrations: Vec<Registration>,
    #[serde(default)]
    auto_pair_blocked: BTreeMap<String, String>,
}

struct Preview {
    origin: String,
    home: String,
    deadline: Instant,
    loopback: bool,
}
struct Slot {
    state: String,
    generation: u64,
    process: Option<WorkerProcess>,
}
#[derive(Default)]
struct Inner {
    config: Option<Configuration>,
    previews: BTreeMap<String, Preview>,
    slots: BTreeMap<String, Slot>,
    generation: u64,
    auto_pairing: Option<AutoPairingView>,
}

#[derive(Default)]
pub struct ConnectorManager {
    gate: Mutex<()>,
    inner: Mutex<Inner>,
    closing: AtomicBool,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ComputerView {
    registration_id: String,
    origin: String,
    home_instance_id: String,
    connector_id: String,
    name: String,
    state: String,
    secret_storage: Storage,
    enabled: bool,
    capabilities_ready: bool,
}
#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AutoPairingView {
    state: String,
    message: String,
}
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ComputersView {
    computers: Vec<ComputerView>,
    auto_pairing: Option<AutoPairingView>,
}
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PairPreview {
    preview_id: String,
    origin: String,
    home_instance_id: String,
    expires_in: u64,
}

fn trusted(label: &str, url: &tauri::Url) -> bool {
    label == WINDOW
        && crate::packaged_url(url)
        && url.path() == PAGE
        && url.query().is_none()
        && url.fragment().is_none()
}
fn check(window: &tauri::WebviewWindow) -> Result<(), String> {
    if trusted(
        window.label(),
        &window.url().map_err(|_| "无法核对本机设置窗口")?,
    ) {
        Ok(())
    } else {
        Err("只有本机电脑设置窗口可以使用此操作".into())
    }
}

fn directory(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    if app.config().identifier != secrets::CHANNEL {
        return Err("应用通道与本机秘密存储不一致，未启动电脑连接".into());
    }
    Ok(app
        .path()
        .app_data_dir()
        .map_err(|_| "无法找到本机配置")?
        .join("computer-connections"))
}
fn store(directory: &Path) -> SystemStore {
    SystemStore {
        directory: directory.join("secrets"),
    }
}

fn clean_origin(raw: &str, trusted_local_origin: Option<&str>) -> Result<(String, bool), String> {
    if raw.len() > 2048 {
        return Err("家的地址过长".into());
    }
    let url: tauri::Url = raw
        .trim()
        .parse()
        .map_err(|_| "请填写完整的家的 HTTPS 地址")?;
    if !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
        || !matches!(url.path(), "" | "/")
    {
        return Err("家的地址不能包含账号、密码、路径或查询参数".into());
    }
    let origin = crate::places::origin(url.as_str()).ok_or("请使用有效的 HTTPS 地址")?;
    if url.scheme() == "https" {
        return Ok((origin, false));
    }
    if matches!(url.host_str(), Some("127.0.0.1" | "[::1]"))
        && trusted_local_origin == Some(origin.as_str())
    {
        Ok((origin, true))
    } else {
        Err("明文连接仅限本 App 当前启动并核对的本机服务；其它家请使用 HTTPS".into())
    }
}

fn local_proof(app: &tauri::AppHandle) -> Option<String> {
    let actual = app.state::<crate::backend::Backend>().local_origin()?;
    let config = app.path().app_config_dir().ok()?;
    let data = app.path().app_data_dir().ok()?;
    crate::places::read_local(&config, &data)
        .url
        .and_then(|origin| crate::places::origin(&origin))
        .filter(|origin| origin == &actual)
}

#[derive(Clone)]
enum WorkerSource {
    Bundled(PathBuf),
    Source(PathBuf, PathBuf),
}
impl WorkerSource {
    fn for_app(app: &tauri::AppHandle) -> Result<Self, String> {
        let resources = app.path().resource_dir().map_err(|_| "无法找到连接器")?;
        let runtime = resources.join("runtime").join(if cfg!(windows) {
            "vortocode-runtime.exe"
        } else {
            "vortocode-runtime"
        });
        if runtime.is_file() {
            return Ok(Self::Bundled(runtime));
        }
        let config = app
            .path()
            .app_config_dir()
            .map_err(|_| "无法找到连接器配置")?;
        let text = fs::read_to_string(config.join(crate::backend::CONFIG_FILE))
            .map_err(|_| "安装包尚未包含电脑连接器，请更新 App")?;
        let config = crate::backend::parse_config(&text).map_err(|_| "本机开发连接器配置无效")?;
        Ok(Self::Source(config.python, config.source))
    }
    fn base_command(&self, directory: &Path) -> Command {
        let mut command = match self {
            Self::Bundled(runtime) => Command::new(runtime),
            Self::Source(python, _) => {
                let mut command = Command::new(python);
                command.args(["-m", "src.runtime_entry"]);
                command
            }
        };
        command
            .env_clear()
            .env("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
            .env("PYTHONNOUSERSITE", "1");
        if let Self::Source(_, source) = self {
            command.env("PYTHONPATH", source);
        }
        command
            .current_dir(directory)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null());
        command
    }
    fn command(&self, directory: &Path) -> Command {
        let mut command = self.base_command(directory);
        command.arg("computer-connector");
        command
    }
}

/// The trusted runtime owns validation, trust invalidation and publication. The
/// Rust shell must never unpack an old home directly over the active state.
pub fn restore_home(app: &tauri::AppHandle, data: &Path, archive: &Path) -> Result<(), String> {
    let mut command = WorkerSource::for_app(app)?.base_command(data);
    command
        .arg("desktop-home-restore")
        .arg("--data-dir")
        .arg(data)
        .arg("--archive")
        .arg(archive);
    run_restore_helper(command)
}
fn run_restore_helper(mut command: Command) -> Result<(), String> {
    let child = command
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|_| "无法启动随包恢复程序；原来的家没有被本机界面修改")?;
    let mut process = WorkerProcess { child, input: None };
    let output = process
        .child
        .stdout
        .take()
        .ok_or("恢复程序私有通道不可用")?;
    let answer = read_frame(&mut BufReader::new(output))
        .map_err(|_| "恢复程序没有给出有效结果；请检查当前服务状态后重试")?;
    let status = process
        .child
        .wait()
        .map_err(|_| "无法核对恢复程序是否完成")?;
    if status.success() && answer == json!({"ok":true}) {
        Ok(())
    } else {
        Err("恢复未完成，请检查备份和本机服务状态后重试；本机界面不会删除原来的家".into())
    }
}

struct WorkerProcess {
    child: Child,
    input: Option<ChildStdin>,
}
type Frames = mpsc::Receiver<Result<Value, ()>>;

fn wait_frame(
    frames: &Frames,
    poll: Duration,
    mut alive: impl FnMut() -> bool,
) -> Result<Value, ()> {
    loop {
        match frames.recv_timeout(poll) {
            Ok(frame) => return frame,
            Err(mpsc::RecvTimeoutError::Disconnected) => return Err(()),
            // A healthy worker reports state changes, not every network heartbeat.
            // Silence is only a polling opportunity; it is not an expiry of trust.
            Err(mpsc::RecvTimeoutError::Timeout) if alive() => continue,
            Err(mpsc::RecvTimeoutError::Timeout) => return Err(()),
        }
    }
}
impl WorkerProcess {
    fn spawn(mut command: Command, request: &Value) -> Result<(Self, Frames), String> {
        let encoded = serde_json::to_vec(request).map_err(|_| "连接器配置无效")?;
        if encoded.len() > MAX_FRAME {
            return Err("连接器配置过长".into());
        }
        let child = command
            .spawn()
            .map_err(|_| "无法启动电脑连接器，请更新 App")?;
        let mut process = Self { child, input: None };
        let mut input = process.child.stdin.take().ok_or("连接器私有通道不可用")?;
        let output = process.child.stdout.take().ok_or("连接器私有通道不可用")?;
        input
            .write_all(&encoded)
            .and_then(|_| input.write_all(b"\n"))
            .and_then(|_| input.flush())
            .map_err(|_| "连接器没有接收配置")?;
        process.input = Some(input);
        let (sender, receiver) = mpsc::sync_channel(4);
        std::thread::spawn(move || {
            let mut output = BufReader::new(output);
            loop {
                let frame = read_frame(&mut output);
                let done = frame.is_err();
                if sender.send(frame).is_err() || done {
                    return;
                }
            }
        });
        Ok((process, receiver))
    }
    fn stop(&mut self) -> Result<(), String> {
        self.input.take(); // EOF is the normal parent-death/stop signal.
        let until = Instant::now() + Duration::from_millis(750);
        loop {
            if matches!(self.child.try_wait(), Ok(Some(_))) {
                return Ok(());
            }
            if Instant::now() >= until {
                break;
            }
            std::thread::sleep(Duration::from_millis(15));
        }
        self.child.kill().map_err(|_| "未能停止电脑连接器")?;
        self.child.wait().map_err(|_| "未能确认电脑连接器已停止")?;
        Ok(())
    }
    fn confirm_revocation(&mut self) -> Result<(), String> {
        // EOF must not race the worker's network ACK after durable local revocation.
        self.input
            .as_mut()
            .ok_or("连接器私有通道已关闭")?
            .write_all(b"{\"type\":\"revocation_persisted\"}\n")
            .map_err(|_| "无法确认本机已撤销")?;
        let until = Instant::now() + Duration::from_secs(2);
        while Instant::now() < until {
            if matches!(self.child.try_wait(), Ok(Some(_))) {
                return Ok(());
            }
            std::thread::sleep(Duration::from_millis(15));
        }
        self.stop()
    }
}
impl Drop for WorkerProcess {
    fn drop(&mut self) {
        let _ = self.stop();
    }
}

fn read_frame(reader: &mut impl BufRead) -> Result<Value, ()> {
    let mut line = Vec::new();
    loop {
        let available = reader.fill_buf().map_err(|_| ())?;
        if available.is_empty() {
            return Err(());
        }
        let count = available
            .iter()
            .position(|c| *c == b'\n')
            .map(|n| n + 1)
            .unwrap_or(available.len());
        if line.len() + count > MAX_FRAME {
            return Err(());
        }
        line.extend_from_slice(&available[..count]);
        reader.consume(count);
        if line.last() == Some(&b'\n') {
            return serde_json::from_slice(&line).map_err(|_| ());
        }
    }
}

fn exchange(source: &WorkerSource, directory: &Path, request: &Value) -> Result<Value, String> {
    let (mut process, frames) = WorkerProcess::spawn(source.command(directory), request)?;
    let result = frames
        .recv_timeout(Duration::from_secs(30))
        .map_err(|_| "电脑连接器没有及时应答")?
        .map_err(|_| "电脑连接器应答无效")?;
    process.stop()?;
    Ok(result)
}

fn load(directory: &Path) -> Result<Configuration, String> {
    secrets::private_dir(directory)?;
    let path = directory.join("registrations.json");
    let config = match fs::read(&path) {
        Ok(bytes) if bytes.len() <= 65536 => serde_json::from_slice::<Configuration>(&bytes)
            .map_err(|_| "电脑配置损坏，未启动连接")?,
        Ok(_) => return Err("电脑配置过长，未启动连接".into()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            let config = Configuration {
                installation_id: secrets::random_id()?,
                registrations: Vec::new(),
                auto_pair_blocked: BTreeMap::new(),
            };
            persist(directory, &config)?;
            config
        }
        Err(_) => return Err("无法读取本机电脑配置".into()),
    };
    let mut homes = std::collections::HashSet::new();
    if !secrets::identifier(&config.installation_id)
        || config.registrations.len() > MAX_RECORDS
        || config.auto_pair_blocked.len() > 256
        || config.auto_pair_blocked.iter().any(|(home, reason)| {
            !secrets::identifier(home)
                || !matches!(
                    reason.as_str(),
                    "removed" | "secret_storage" | "unknown_registration"
                )
        })
        || config
            .registrations
            .iter()
            .any(|record| !valid_record(record) || !homes.insert(record.home_instance_id.clone()))
    {
        return Err("电脑配置身份无效，未启动连接".into());
    }
    Ok(config)
}
fn persist(directory: &Path, config: &Configuration) -> Result<(), String> {
    secrets::atomic_private_write(
        &directory.join("registrations.json"),
        &serde_json::to_vec(config).map_err(|_| "无法保存电脑配置")?,
    )
}
fn accept_registration(
    directory: &Path,
    config: &mut Configuration,
    record: Registration,
) -> Result<(), String> {
    if config.registrations.len() >= MAX_RECORDS
        || config
            .registrations
            .iter()
            .any(|previous| previous.home_instance_id == record.home_instance_id)
    {
        return Err("此家已有登记，或本机登记数量已达上限".into());
    }
    let mut candidate = config.clone();
    candidate.auto_pair_blocked.remove(&record.home_instance_id);
    candidate.registrations.push(record);
    persist(directory, &candidate)?;
    *config = candidate;
    Ok(())
}
fn disable_pair(
    directory: &Path,
    config: &mut Configuration,
    home: &str,
) -> Result<Registration, String> {
    let mut candidate = config.clone();
    let record = candidate
        .registrations
        .iter_mut()
        .find(|record| record.home_instance_id == home)
        .ok_or("没有这项本机配对")?;
    record.enabled = false;
    let record = record.clone();
    persist(directory, &candidate)?;
    *config = candidate;
    Ok(record)
}
fn remove_local(
    directory: &Path,
    config: &mut Configuration,
    home: &str,
    secrets: &impl SecretStore,
    stop: impl FnOnce() -> Result<(), String>,
) -> Result<(), String> {
    if config.auto_pair_blocked.len() >= 256 && !config.auto_pair_blocked.contains_key(home) {
        return Err("本机停用记录已满，请先手工整理电脑配置".into());
    }
    config
        .auto_pair_blocked
        .insert(home.into(), "removed".into());
    let record = disable_pair(directory, config, home)?;
    stop()?;
    secrets.delete(&record.key(), record.storage)?;
    let mut candidate = config.clone();
    candidate
        .registrations
        .retain(|record| record.home_instance_id != home);
    persist(directory, &candidate)?;
    *config = candidate;
    Ok(())
}
fn valid_name(name: &str) -> bool {
    !name.trim().is_empty() && name.chars().count() <= 64 && !name.chars().any(char::is_control)
}
fn valid_record(record: &Registration) -> bool {
    secrets::identifier(&record.home_instance_id)
        && secrets::identifier(&record.connector_id)
        && valid_name(&record.name)
        && secrets::identifier(&record.paired_principal_id)
        && secrets::identifier(&record.credential_epoch)
        && secrets::identifier(&record.trust_epoch)
        && crate::places::origin(&record.origin).as_deref() == Some(record.origin.as_str())
}

impl ConnectorManager {
    fn ensure(&self, directory: &Path) -> Result<(), String> {
        let mut inner = self.inner.lock().map_err(|_| "电脑配置忙，请重试")?;
        if inner.config.is_none() {
            inner.config = Some(load(directory)?);
        }
        Ok(())
    }
    fn views(&self) -> Result<ComputersView, String> {
        let inner = self.inner.lock().map_err(|_| "无法读取电脑状态")?;
        let computers = inner
            .config
            .as_ref()
            .map(|config| {
                config
                    .registrations
                    .iter()
                    .map(|record| ComputerView {
                        registration_id: record.home_instance_id.clone(),
                        origin: record.origin.clone(),
                        home_instance_id: record.home_instance_id.clone(),
                        connector_id: record.connector_id.clone(),
                        name: record.name.clone(),
                        state: inner
                            .slots
                            .get(&record.home_instance_id)
                            .map(|slot| slot.state.clone())
                            .unwrap_or_else(|| {
                                if record.enabled {
                                    "disconnected"
                                } else {
                                    "revoked"
                                }
                                .into()
                            }),
                        secret_storage: record.storage,
                        enabled: record.enabled,
                        capabilities_ready: false,
                    })
                    .collect()
            })
            .unwrap_or_default();
        Ok(ComputersView {
            computers,
            auto_pairing: inner.auto_pairing.clone(),
        })
    }
    pub fn stop(&self) {
        self.closing.store(true, Ordering::Release);
        // Do not wait for a pairing dialog holding gate: App exit must close active pipes.
        if let Ok(mut inner) = self.inner.lock() {
            for slot in inner.slots.values_mut() {
                slot.generation = 0;
                if let Some(mut process) = slot.process.take() {
                    let _ = process.stop();
                }
                slot.state = "disconnected".into();
            }
        }
    }
}

fn connect_registration(
    app: &tauri::AppHandle,
    record: &Registration,
    source: &WorkerSource,
    directory: &Path,
) -> Result<(), String> {
    let manager = app.state::<ConnectorManager>();
    if manager.closing.load(Ordering::Acquire) || !record.enabled {
        return Ok(());
    }
    {
        let inner = manager.inner.lock().map_err(|_| "电脑状态忙，请重试")?;
        if inner
            .slots
            .get(&record.home_instance_id)
            .is_some_and(|slot| slot.process.is_some() || slot.state == "replaced")
        {
            return Ok(());
        }
    }
    let (_, loopback) = clean_origin(&record.origin, local_proof(app).as_deref())?;
    let token = store(directory).get(&record.key(), record.storage)?;
    let (process, frames) =
        WorkerProcess::spawn(source.command(directory), &record.request(token, loopback))?;
    let generation = {
        let mut inner = manager.inner.lock().map_err(|_| "电脑状态忙，请重试")?;
        if manager.closing.load(Ordering::Acquire) {
            return Ok(());
        }
        inner.generation += 1;
        let generation = inner.generation;
        inner.slots.insert(
            record.home_instance_id.clone(),
            Slot {
                state: "connecting".into(),
                generation,
                process: Some(process),
            },
        );
        generation
    };
    let handle = app.clone();
    let record = record.clone();
    let directory = directory.to_path_buf();
    std::thread::spawn(move || {
        loop {
            let frame = wait_frame(&frames, Duration::from_secs(1), || {
                let manager = handle.state::<ConnectorManager>();
                let Ok(mut inner) = manager.inner.lock() else {
                    return false;
                };
                inner
                    .slots
                    .get_mut(&record.home_instance_id)
                    .filter(|slot| slot.generation == generation)
                    .and_then(|slot| slot.process.as_mut())
                    .is_some_and(|process| matches!(process.child.try_wait(), Ok(None)))
            });
            let ended = frame.is_err();
            let state = match &frame {
                Ok(frame) => match accepted_status(frame, &record) {
                    Some(state) => state,
                    None => "error",
                },
                _ => "disconnected",
            };
            let manager = handle.state::<ConnectorManager>();
            let Ok(mut inner) = manager.inner.lock() else {
                return;
            };
            if !inner
                .slots
                .get(&record.home_instance_id)
                .is_some_and(|slot| slot.generation == generation)
            {
                return;
            }
            if state == "revoked" {
                // Persist the local tombstone before this credential could be used on restart.
                if disable_pair(
                    &directory,
                    inner.config.as_mut().unwrap(),
                    &record.home_instance_id,
                )
                .is_err()
                {
                    let slot = inner.slots.get_mut(&record.home_instance_id).unwrap();
                    slot.state = "error".into();
                    if let Some(mut process) = slot.process.take() {
                        let _ = process.stop();
                    }
                    return;
                }
                let deleted = store(&directory)
                    .delete(&record.key(), record.storage)
                    .is_ok();
                let slot = inner.slots.get_mut(&record.home_instance_id).unwrap();
                slot.state = "revoked".into();
                if let Some(mut process) = slot.process.take() {
                    if deleted {
                        let _ = process.confirm_revocation();
                    } else {
                        let _ = process.stop();
                    }
                }
                return;
            }
            let slot = inner.slots.get_mut(&record.home_instance_id).unwrap();
            slot.state = state.into();
            if ended || matches!(state, "replaced" | "error") {
                if let Some(mut process) = slot.process.take() {
                    let _ = process.stop();
                }
                return;
            }
        }
    });
    Ok(())
}

fn accepted_status<'a>(frame: &'a Value, record: &Registration) -> Option<&'a str> {
    if frame["type"] != "status"
        || frame["home_instance_id"] != record.home_instance_id
        || frame["connector_id"] != record.connector_id
    {
        return None;
    }
    if frame.as_object()?.len() != 4 {
        return None;
    }
    match frame["state"].as_str()? {
        state @ ("connecting" | "online" | "disconnected" | "revoked" | "replaced" | "error") => {
            Some(state)
        }
        _ => None,
    }
}

fn block_auto(
    directory: &Path,
    config: &mut Configuration,
    home: &str,
    reason: &str,
) -> Result<(), String> {
    if !secrets::identifier(home)
        || !matches!(
            reason,
            "removed" | "secret_storage" | "unknown_registration"
        )
        || (config.auto_pair_blocked.len() >= 256 && !config.auto_pair_blocked.contains_key(home))
    {
        return Err("本机自动配对停用记录不可用".into());
    }
    let mut candidate = config.clone();
    candidate
        .auto_pair_blocked
        .insert(home.into(), reason.into());
    persist(directory, &candidate)?;
    *config = candidate;
    Ok(())
}

fn auto_note(
    manager: &ConnectorManager,
    directory: &Path,
    block: Option<(&str, &str)>,
    state: &str,
    message: &str,
) {
    if let Ok(mut inner) = manager.inner.lock() {
        let saved = block
            .map(|(home, reason)| {
                block_auto(directory, inner.config.as_mut().unwrap(), home, reason)
            })
            .transpose();
        inner.auto_pairing = Some(AutoPairingView {
            state: state.into(),
            message: if saved.is_err() {
                "本机配对未启用，且停用记录未能保存。请打开本机设置，并在家中检查和移除可能留下的登记。".into()
            } else {
                message.into()
            },
        });
    }
}

fn native_secret(
    secrets: &impl SecretStore,
    record: &Registration,
    token: &str,
) -> Result<(), String> {
    // Automatic pairing must never invoke the manual private-file fallback.
    secrets.put(&record.key(), Storage::Keychain, token).map_err(|_| "系统秘密存储不可用，本机自动配对未启用；请打开本机设置，并在家中移除本次登记后手工配对。".into())
}

fn native_with_intent<T>(
    manager: &ConnectorManager,
    directory: &Path,
    home: &str,
    send: impl FnOnce() -> T,
) -> Result<T, String> {
    {
        let mut inner = manager.inner.lock().map_err(|_| "本机电脑状态不可用")?;
        let config = inner.config.as_mut().ok_or("本机电脑配置不可用")?;
        if manager.closing.load(Ordering::Acquire)
            || config.auto_pair_blocked.contains_key(home)
            || config
                .registrations
                .iter()
                .any(|record| record.home_instance_id == home)
        {
            return Err("本机自动登记已停用或已有登记，请手工核对".into());
        }
        // This must be durable before the home can create a grant. If the App
        // crashes or later keychain/config writes fail, a fresh launch cannot
        // silently create another orphan registration.
        block_auto(directory, config, home, "unknown_registration")?;
    }
    // Network and native secret-store dialogs never hold the state mutex.
    Ok(send())
}

fn auto_pair_local(app: &tauri::AppHandle, directory: &Path) {
    let backend = app.state::<crate::backend::Backend>();
    let Some(candidate) = backend.native_candidate() else {
        return;
    };
    let manager = app.state::<ConnectorManager>();
    let origin = crate::backend::origin(candidate.port);
    if local_proof(app).as_deref() != Some(origin.as_str()) {
        backend.discard_native(candidate);
        auto_note(
            &manager,
            directory,
            None,
            "error",
            "无法核对本机服务，自动配对未启用。请重新启动本机服务或手工配对。",
        );
        return;
    }
    let identity = match crate::backend::native_identity(candidate.port) {
        Ok((200, identity))
            if identity["home_instance_id"]
                .as_str()
                .is_some_and(secrets::identifier)
                && identity["protocol_min"] == 1
                && identity["protocol_max"] == 1
                && identity["features"] == json!(["control-only"]) =>
        {
            identity
        }
        _ => {
            backend.discard_native(candidate);
            auto_note(
                &manager,
                directory,
                None,
                "error",
                "本机家的身份或协议无法核对，请更新服务后重新启动，或手工配对。",
            );
            return;
        }
    };
    let home = identity["home_instance_id"].as_str().unwrap();
    {
        let Ok(inner) = manager.inner.lock() else {
            return;
        };
        let config = inner.config.as_ref().unwrap();
        if config
            .registrations
            .iter()
            .any(|record| record.home_instance_id == home)
        {
            backend.discard_native(candidate);
            return; // An existing disabled/revoked registration is never silently replaced.
        }
        if config.auto_pair_blocked.contains_key(home) {
            let reason = config.auto_pair_blocked[home].clone();
            drop(inner);
            backend.discard_native(candidate);
            auto_note(
                &manager,
                directory,
                None,
                "disabled",
                if reason == "removed" {
                    "这台电脑已在本机移除此家的连接。需要重新连接时，请在本机设置中手工配对。"
                } else {
                    "此前本机自动登记未能安全完成。请在家中检查并移除可能留下的登记，再在本机设置中手工配对。"
                },
            );
            return;
        }
        if config.registrations.len() >= MAX_RECORDS {
            drop(inner);
            backend.discard_native(candidate);
            auto_note(
                &manager,
                directory,
                None,
                "error",
                "本机配对数量已达上限，未创建新的自动登记。",
            );
            return;
        }
    }
    let ticket = match backend.consume_native(candidate) {
        Ok(ticket) => ticket,
        Err(message) => {
            auto_note(&manager, directory, None, "expired", &message);
            return;
        }
    };
    let connector = match secrets::random_id() {
        Ok(id) => id,
        Err(_) => {
            auto_note(
                &manager,
                directory,
                None,
                "error",
                "无法生成电脑身份，自动配对未启用。",
            );
            return;
        }
    };
    let response = match native_with_intent(&manager, directory, home, || {
        crate::backend::native_pair(candidate.port, &ticket, &connector, "这台电脑")
    }) {
        Ok(response) => response,
        Err(_) => {
            auto_note(&manager, directory, None, "error", "本机状态无法安全保存或已有停用记录，未发送新的自动配对请求。请打开本机设置手工核对。");
            return;
        }
    };
    let mut grant = match response {
        Ok((200, grant)) => grant,
        Ok((409, _)) => {
            auto_note(&manager, directory, None, "owner_required", "请先完成所有者认领或明确迁移旧家，再在本机设置中手工配对。本次自动登记已停用，不会在重启后自动补授权。");
            return;
        }
        Ok((403, _)) => {
            auto_note(
                &manager,
                directory,
                None,
                "denied",
                "本机启动票据已失效，自动配对未启用。请在本机设置中手工配对；重启不会自动补授权。",
            );
            return;
        }
        _ => {
            auto_note(&manager, directory, Some((home, "unknown_registration")), "error", "本机自动登记的结果未知。请在家中检查并移除可能留下的登记，再在本机设置中手工配对。");
            return;
        }
    };
    if !backend.matches_native(candidate)
        || local_proof(app).as_deref() != Some(origin.as_str())
        || !grant.as_object().is_some_and(|grant| grant.len() == 6)
    {
        auto_note(
            &manager,
            directory,
            Some((home, "unknown_registration")),
            "error",
            "本机服务或登记应答已变化，自动配对未启用。请在家中检查本次登记后手工配对。",
        );
        return;
    }
    grant
        .as_object_mut()
        .unwrap()
        .insert("type".into(), json!("paired"));
    let preview = Preview {
        origin,
        home: home.into(),
        deadline: Instant::now(),
        loopback: true,
    };
    let (record, token) = match grant_record(&grant, &preview, &connector, "这台电脑".into()) {
        Ok(pair) => pair,
        Err(_) => {
            auto_note(
                &manager,
                directory,
                Some((home, "unknown_registration")),
                "error",
                "本机配对授权无法核对，未保存凭据。请在家中移除本次登记后手工配对。",
            );
            return;
        }
    };
    let secrets = store(directory);
    if let Err(message) = native_secret(&secrets, &record, &token) {
        auto_note(
            &manager,
            directory,
            Some((home, "secret_storage")),
            "secret_storage",
            &message,
        );
        return;
    }
    if manager.closing.load(Ordering::Acquire) || !backend.matches_native(candidate) {
        let _ = secrets.delete(&record.key(), record.storage);
        auto_note(
            &manager,
            directory,
            Some((home, "unknown_registration")),
            "error",
            "本机服务已停止，自动配对未启用；请在家中检查本次登记。",
        );
        return;
    }
    let saved = (|| {
        let mut inner = manager.inner.lock().map_err(|_| "本机电脑状态不可用")?;
        accept_registration(directory, inner.config.as_mut().unwrap(), record)?;
        inner.auto_pairing = Some(AutoPairingView {
            state: "paired".into(),
            message: "已通过本机启动通道登记这台电脑；全部执行能力仍未就绪。".into(),
        });
        Ok::<_, String>(())
    })();
    if saved.is_err() {
        let _ = secrets.delete(
            &SecretRef {
                home: home.into(),
                connector,
            },
            Storage::Keychain,
        );
        auto_note(
            &manager,
            directory,
            Some((home, "secret_storage")),
            "error",
            "本机配对配置未能保存，自动连接未启用。请在家中移除本次登记后手工配对。",
        );
    }
}

/// Called at App startup independently of whether the live web view opens a local or remote home.
/// Also retried after the App's local server starts, when its loopback proof becomes available.
pub fn resume(app: tauri::AppHandle) {
    std::thread::spawn(move || {
        let manager = app.state::<ConnectorManager>();
        let Ok(_gate) = manager.gate.lock() else {
            return;
        };
        if manager.closing.load(Ordering::Acquire) {
            return;
        }
        let Ok(directory) = directory(&app) else {
            return;
        };
        if manager.ensure(&directory).is_err() {
            return;
        }
        auto_pair_local(&app, &directory);
        let source = WorkerSource::for_app(&app);
        let records = manager
            .inner
            .lock()
            .ok()
            .and_then(|inner| {
                inner
                    .config
                    .as_ref()
                    .map(|config| config.registrations.clone())
            })
            .unwrap_or_default();
        for record in records {
            if !record.enabled {
                continue;
            }
            if source
                .as_ref()
                .map_err(|_| ())
                .and_then(|source| {
                    connect_registration(&app, &record, source, &directory).map_err(|_| ())
                })
                .is_err()
            {
                if let Ok(mut inner) = manager.inner.lock() {
                    inner
                        .slots
                        .entry(record.home_instance_id)
                        .and_modify(|slot| {
                            if slot.process.is_none() && slot.state != "replaced" {
                                slot.state = "error".into();
                            }
                        })
                        .or_insert(Slot {
                            state: "error".into(),
                            generation: 0,
                            process: None,
                        });
                }
            }
        }
    });
}

#[tauri::command]
pub async fn computers_get(
    window: tauri::WebviewWindow,
    app: tauri::AppHandle,
) -> Result<ComputersView, String> {
    check(&window)?;
    tauri::async_runtime::spawn_blocking(move || {
        let manager = app.state::<ConnectorManager>();
        manager.ensure(&directory(&app)?)?;
        manager.views()
    })
    .await
    .map_err(|_| "无法读取电脑状态")?
}

#[tauri::command]
pub async fn computer_pair_preview(
    window: tauri::WebviewWindow,
    app: tauri::AppHandle,
    origin: String,
) -> Result<PairPreview, String> {
    check(&window)?;
    tauri::async_runtime::spawn_blocking(move || {
        let manager = app.state::<ConnectorManager>();
        let _gate = manager.gate.lock().map_err(|_| "电脑配置忙，请重试")?;
        let directory = directory(&app)?;
        manager.ensure(&directory)?;
        let (origin, loopback) = clean_origin(&origin, local_proof(&app).as_deref())?;
        let identity = exchange(
            &WorkerSource::for_app(&app)?,
            &directory,
            &json!({"operation":"identity", "origin":origin, "allow_loopback_http":loopback}),
        )?;
        let home = identity["home_instance_id"]
            .as_str()
            .filter(|home| secrets::identifier(home))
            .ok_or("家没有提供有效的持久身份")?
            .to_string();
        if identity["type"] != "identity"
            || identity["protocol_min"].as_u64().is_none_or(|min| min > 1)
            || identity["protocol_max"].as_u64().is_none_or(|max| max < 1)
            || !identity["features"]
                .as_array()
                .is_some_and(|features| features.iter().any(|feature| feature == "control-only"))
        {
            return Err("家的电脑协议不兼容，请更新 App 或服务".into());
        }
        let id = secrets::random_id()?;
        let mut inner = manager.inner.lock().map_err(|_| "电脑配置忙，请重试")?;
        inner
            .previews
            .retain(|_, preview| preview.deadline > Instant::now());
        if inner.previews.len() >= 4 {
            return Err("待确认的配对过多，请稍后重试".into());
        }
        inner.previews.insert(
            id.clone(),
            Preview {
                origin: origin.clone(),
                home: home.clone(),
                deadline: Instant::now() + Duration::from_secs(300),
                loopback,
            },
        );
        Ok(PairPreview {
            preview_id: id,
            origin,
            home_instance_id: home,
            expires_in: 300,
        })
    })
    .await
    .map_err(|_| "无法核对家的身份")?
}

fn ask(app: &tauri::AppHandle, title: &str, text: String, accept: &str) -> bool {
    use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
    app.dialog()
        .message(text)
        .title(title)
        .kind(MessageDialogKind::Warning)
        .buttons(MessageDialogButtons::OkCancelCustom(
            accept.into(),
            "取消".into(),
        ))
        .blocking_show()
}

fn pair_request(preview: &Preview, code: &str, name: &str) -> Result<Value, String> {
    // A revoked registration ID is permanently retired by the home. The stable
    // installation identity is metadata; each explicit pairing receives a new ID.
    Ok(
        json!({"operation":"pair", "origin":preview.origin, "home_instance_id":preview.home,
        "connector_id":secrets::random_id()?, "name":name, "code":code, "allow_loopback_http":preview.loopback}),
    )
}

#[tauri::command]
pub async fn computer_pair_confirm(
    window: tauri::WebviewWindow,
    app: tauri::AppHandle,
    preview_id: String,
    code: String,
    name: String,
) -> Result<(), String> {
    check(&window)?;
    tauri::async_runtime::spawn_blocking(move || {
        let manager = app.state::<ConnectorManager>();
        let _gate = manager.gate.lock().map_err(|_| "电脑配置忙，请重试")?;
        if manager.closing.load(Ordering::Acquire) { return Err("App 正在退出".into()); }
        let directory = directory(&app)?;
        manager.ensure(&directory)?;
        let preview = manager.inner.lock().map_err(|_| "电脑配置忙，请重试")?.previews.remove(&preview_id).ok_or("请先重新核对家的身份")?;
        if preview.deadline <= Instant::now() { return Err("身份预览已过期，请重新核对".into()); }
        let name = name.trim().to_string();
        let code: String = code.chars().filter(|c| !matches!(c, ' ' | '-')).collect::<String>().to_ascii_uppercase();
        if !valid_name(&name) || code.len() != 12 || !code.bytes().all(|c| matches!(c, b'A'..=b'Z' | b'2'..=b'7')) { return Err("请填写电脑名称和 12 位配对码".into()); }
        let (_, loopback) = clean_origin(&preview.origin, local_proof(&app).as_deref())?;
        if loopback != preview.loopback { return Err("本机服务已变化，请重新核对".into()); }
        let config = manager.inner.lock().map_err(|_| "电脑配置忙，请重试")?.config.as_ref().unwrap().clone();
        if config.registrations.iter().any(|record| record.home_instance_id == preview.home) { return Err("此家已有本机配对，请先移除旧配对".into()); }
        if config.registrations.len() >= MAX_RECORDS { return Err("本机配对数量已达上限，请先移除旧配对".into()); }
        if !ask(&app, "确认连接这台电脑", format!("家：{}\n家身份：{}\n电脑名称：{}\n\n仅登记身份并保持主动连接。文件、命令、浏览器和 Codex 等全部能力尚未就绪，配对不会开放它们。", preview.origin, preview.home, name), "确认配对") {
            return Err("已取消配对，请重新核对后再试".into());
        }
        let source = WorkerSource::for_app(&app)?;
        let request = pair_request(&preview, &code, &name)?;
        let grant = exchange(&source, &directory, &request)?;
        let connector_id = request["connector_id"].as_str().ok_or("电脑身份无效")?;
        let (mut record, token) = grant_record(&grant, &preview, connector_id, name)?;
        let secrets = store(&directory);
        if secrets.put(&record.key(), Storage::Keychain, &token).is_err() {
            if !ask(&app, "系统秘密存储不可用", "电脑凭据尚未保存到系统钥匙串。是否明确改为本机私有文件？\n\n这是降级存储：目录权限 0700、文件权限 0600；凭据不进入家的备份。取消后不会启动连接，家中刚创建的登记可在家里移除。".into(), "使用私有文件") {
                return Err("凭据未保存，连接未启动；请在家中移除本次登记后重试".into());
            }
            record.storage = Storage::PrivateFile;
            secrets.put(&record.key(), record.storage, &token)?;
        }
        // A revocation watcher may have disabled another registration while the native
        // keychain dialog was open. Merge into the latest state, never the old snapshot.
        let mut inner = manager.inner.lock().map_err(|_| "电脑配置忙，请重试")?;
        if let Err(error) = accept_registration(&directory, inner.config.as_mut().unwrap(), record.clone()) { let _ = secrets.delete(&record.key(), record.storage); return Err(error); }
        drop(inner);
        if connect_registration(&app, &record, &source, &directory).is_err() {
            if let Ok(mut inner) = manager.inner.lock() {
                inner.slots.entry(record.home_instance_id).or_insert(Slot {
                    state: "error".into(), generation: 0, process: None,
                });
            }
            return Err("配对已保存在本机，但连接尚未就绪；请检查版本或重新打开 App".into());
        }
        Ok(())
    }).await.map_err(|_| "配对未完成")?
}

fn grant_record(
    grant: &Value,
    preview: &Preview,
    connector: &str,
    name: String,
) -> Result<(Registration, String), String> {
    if grant["type"] != "paired"
        || grant["home_instance_id"] != preview.home
        || grant["connector_id"] != connector
    {
        return Err("配对身份与本机确认不一致，未保存凭据".into());
    }
    let token = grant["token"]
        .as_str()
        .filter(|text| {
            (32..=512).contains(&text.len()) && text.bytes().all(|c| c.is_ascii_graphic())
        })
        .ok_or("配对凭据无效")?
        .to_string();
    let record = Registration {
        origin: preview.origin.clone(),
        home_instance_id: preview.home.clone(),
        connector_id: connector.into(),
        name,
        paired_principal_id: grant["paired_principal_id"]
            .as_str()
            .ok_or("配对所有者身份无效")?
            .into(),
        credential_epoch: grant["credential_epoch"]
            .as_str()
            .ok_or("配对凭据代次无效")?
            .into(),
        trust_epoch: grant["trust_epoch"]
            .as_str()
            .ok_or("配对授权代次无效")?
            .into(),
        storage: Storage::Keychain,
        enabled: true,
    };
    if !valid_record(&record) {
        return Err("配对授权无效".into());
    }
    Ok((record, token))
}

#[tauri::command]
pub async fn computer_remove(
    window: tauri::WebviewWindow,
    app: tauri::AppHandle,
    registration_id: String,
) -> Result<(), String> {
    check(&window)?;
    tauri::async_runtime::spawn_blocking(move || {
        let manager = app.state::<ConnectorManager>();
        let _gate = manager.gate.lock().map_err(|_| "电脑配置忙，请重试")?;
        let directory = directory(&app)?;
        manager.ensure(&directory)?;
        let record = manager.inner.lock().map_err(|_| "电脑配置忙，请重试")?.config.as_ref().unwrap().registrations.iter()
            .find(|record| record.home_instance_id == registration_id).cloned();
        let Some(record) = record else { return Ok(()); };
        if !ask(&app, "移除这台电脑的连接", format!("家：{}\n电脑：{}\n\n立即停止这台电脑向这个家的连接，并删除本机保存的电脑凭据。家里的登记可由所有者另行移除。", record.origin, record.name), "停止并移除") { return Err("已取消移除".into()); }
        let mut inner = manager.inner.lock().map_err(|_| "电脑配置忙，请重试")?;
        let mut candidate = inner.config.take().unwrap();
        let result = remove_local(&directory, &mut candidate, &registration_id, &store(&directory), || {
            if let Some(slot) = inner.slots.get_mut(&registration_id) {
                slot.generation = 0;
                slot.state = "revoked".into();
                if let Some(mut process) = slot.process.take() { process.stop()?; }
            }
            Ok(())
        });
        inner.config = Some(candidate);
        if result.is_ok() { inner.slots.remove(&registration_id); }
        result
    }).await.map_err(|_| "未能移除电脑配对")?
}

#[tauri::command]
pub fn computers_open(window: tauri::WebviewWindow, app: tauri::AppHandle) -> Result<(), String> {
    // Existing packaged prefs may open this window; a remote live page cannot.
    if window.label() != "prefs"
        || !crate::packaged_url(&window.url().map_err(|_| "无法核对本机设置窗口")?)
        || window.url().map_err(|_| "无法核对本机设置窗口")?.path() != "/shell/index.html"
    {
        return Err("只有本机偏好设置可以打开电脑设置".into());
    }
    open(&app)
}
pub fn open(app: &tauri::AppHandle) -> Result<(), String> {
    crate::show(app, WINDOW, "computers.html", "连接的电脑", 760.0, 760.0)
        .map_err(|_| "无法打开电脑设置".into())
}

#[cfg(test)]
mod tests {
    use super::*;
    const HOME: &str = "11111111111141118111111111111111";
    const CONNECTOR: &str = "22222222222242228222222222222222";
    fn preview() -> Preview {
        Preview {
            origin: "https://home.example".into(),
            home: HOME.into(),
            deadline: Instant::now() + Duration::from_secs(300),
            loopback: false,
        }
    }
    fn grant() -> Value {
        json!({"type":"paired", "home_instance_id":HOME, "connector_id":CONNECTOR, "paired_principal_id":"33333333333343338333333333333333", "credential_epoch":"44444444444444448444444444444444", "trust_epoch":"55555555555545558555555555555555", "token":"only-pipe-secret-0123456789abcdef0123456789"})
    }
    #[test]
    fn only_exact_packaged_computer_settings_can_pair() {
        assert!(trusted(
            "computers",
            &"tauri://localhost/computers.html".parse().unwrap()
        ));
        for (label, url) in [
            ("live", "tauri://localhost/computers.html"),
            ("computers", "https://home.example/computers.html"),
            ("computers", "http://127.0.0.1:8080/computers.html"),
            ("prefs", "tauri://localhost/computers.html"),
            ("computers", "tauri://localhost/computers.html?redirect=x"),
        ] {
            assert!(!trusted(label, &url.parse().unwrap()));
        }
    }
    #[test]
    fn loopback_requires_actual_host_proof_and_remote_urls_are_not_rewritten() {
        for origin in [
            "http://127.0.0.1:8080",
            "http://[::1]:8080",
            "http://localhost:8080",
        ] {
            assert!(clean_origin(origin, None).is_err());
        }
        assert_eq!(
            clean_origin("http://127.0.0.1:8080", Some("http://127.0.0.1:8080")).unwrap(),
            ("http://127.0.0.1:8080".into(), true)
        );
        assert!(clean_origin("http://127.0.0.1:8081", Some("http://127.0.0.1:8080")).is_err());
        for origin in [
            "https://user:pass@home.example",
            "https://home.example/path",
            "https://home.example/?token=x",
            "https://home.example/#token",
        ] {
            assert!(clean_origin(origin, None).is_err());
        }
    }
    #[test]
    fn grant_is_bound_to_preview_and_secret_never_enters_configuration_or_status() {
        let (record, token) =
            grant_record(&grant(), &preview(), CONNECTOR, "My Mac".into()).unwrap();
        assert!(record.enabled);
        let config = Configuration {
            installation_id: CONNECTOR.into(),
            registrations: vec![record.clone()],
            auto_pair_blocked: BTreeMap::new(),
        };
        assert!(!serde_json::to_string(&config).unwrap().contains(&token));
        let manager = ConnectorManager::default();
        manager.inner.lock().unwrap().config = Some(config);
        let view = serde_json::to_string(&manager.views().unwrap()).unwrap();
        assert!(!view.contains(&token) && view.contains("\"capabilitiesReady\":false"));
        let mut other = grant();
        other["home_instance_id"] = json!(CONNECTOR);
        assert!(grant_record(&other, &preview(), CONNECTOR, "Mac".into()).is_err());
        other = grant();
        other["trust_epoch"] = json!(0);
        assert!(grant_record(&other, &preview(), CONNECTOR, "Mac".into()).is_err());
        assert_eq!(
            accepted_status(
                &json!({"type":"status", "state":"online", "home_instance_id":HOME, "connector_id":CONNECTOR}),
                &record
            ),
            Some("online")
        );
        for frame in [
            json!({"type":"execute", "command":"touch /tmp/no"}),
            json!({"type":"status", "state":"online", "home_instance_id":CONNECTOR, "connector_id":CONNECTOR}),
        ] {
            assert!(accepted_status(&frame, &record).is_none());
        }
    }
    #[test]
    fn framed_protocol_rejects_unbounded_or_malformed_output() {
        assert!(read_frame(&mut std::io::Cursor::new(vec![b'x'; MAX_FRAME + 1])).is_err());
        assert!(read_frame(&mut std::io::Cursor::new(b"not-json\n")).is_err());
        assert!(read_frame(&mut std::io::Cursor::new(b"{\"type\":\"status\"}")).is_err());
        assert_eq!(
            read_frame(&mut std::io::Cursor::new(b"{\"type\":\"status\"}\n")).unwrap()["type"],
            "status"
        );
    }
    #[test]
    fn installation_identity_survives_restart_and_corruption_does_not_reset_it() {
        let root = std::env::temp_dir().join(format!(
            "vortoone-connector-config-{}",
            secrets::random_id().unwrap()
        ));
        let config = load(&root).unwrap();
        assert_eq!(load(&root).unwrap().installation_id, config.installation_id);
        fs::write(root.join("registrations.json"), b"invalid").unwrap();
        assert!(load(&root).is_err());
        fs::remove_dir_all(root).unwrap();
    }
    struct FakeStore {
        directory: PathBuf,
        fail: bool,
        calls: Mutex<Vec<String>>,
    }
    impl SecretStore for FakeStore {
        fn put(&self, _: &SecretRef, storage: Storage, _: &str) -> Result<(), String> {
            self.calls.lock().unwrap().push(
                if storage == Storage::Keychain {
                    "put_keychain"
                } else {
                    "put_file"
                }
                .into(),
            );
            if self.fail {
                Err("fake keychain unavailable".into())
            } else {
                Ok(())
            }
        }
        fn get(&self, _: &SecretRef, _: Storage) -> Result<String, String> {
            Ok("test-only-secret-0123456789abcdef01234567".into())
        }
        fn delete(&self, key: &SecretRef, _: Storage) -> Result<(), String> {
            let config = load(&self.directory).unwrap();
            assert!(
                !config
                    .registrations
                    .iter()
                    .find(|record| record.home_instance_id == key.home)
                    .unwrap()
                    .enabled
            );
            self.calls.lock().unwrap().push("delete".into());
            if self.fail {
                Err("fake deletion failure".into())
            } else {
                Ok(())
            }
        }
    }
    fn configured() -> (PathBuf, Configuration) {
        let root = std::env::temp_dir().join(format!(
            "vortoone-connector-order-{}",
            secrets::random_id().unwrap()
        ));
        let (record, _) = grant_record(&grant(), &preview(), CONNECTOR, "Mac".into()).unwrap();
        let config = Configuration {
            installation_id: CONNECTOR.into(),
            registrations: vec![record],
            auto_pair_blocked: BTreeMap::new(),
        };
        persist(&root, &config).unwrap();
        (root, config)
    }
    #[test]
    fn local_remove_durably_disables_before_stopping_or_deleting() {
        for delete_fails in [false, true] {
            let (root, mut config) = configured();
            let secrets = FakeStore {
                directory: root.clone(),
                fail: delete_fails,
                calls: Mutex::new(vec![]),
            };
            let result = remove_local(&root, &mut config, HOME, &secrets, || {
                assert!(!load(&root).unwrap().registrations[0].enabled);
                secrets.calls.lock().unwrap().push("stop".into());
                Ok(())
            });
            assert_eq!(*secrets.calls.lock().unwrap(), vec!["stop", "delete"]);
            assert_eq!(result.is_err(), delete_fails);
            let read_back = load(&root).unwrap();
            if delete_fails {
                assert!(!read_back.registrations[0].enabled && !config.registrations[0].enabled);
            } else {
                assert!(read_back.registrations.is_empty() && config.registrations.is_empty());
            }
            fs::remove_dir_all(root).unwrap();
        }
    }
    #[test]
    fn explicit_repair_uses_a_new_registration_identity_after_local_remove() {
        let (root, mut config) = configured();
        let installation = config.installation_id.clone();
        let previous = config.registrations[0].connector_id.clone();
        let secrets = FakeStore {
            directory: root.clone(),
            fail: false,
            calls: Mutex::new(vec![]),
        };
        remove_local(&root, &mut config, HOME, &secrets, || Ok(())).unwrap();
        let request = pair_request(&preview(), "ABCDEFGH2345", "Mac").unwrap();
        let connector = request["connector_id"].as_str().unwrap();
        assert!(secrets::identifier(connector));
        assert_ne!(connector, previous);
        assert_ne!(connector, installation);
        let mut fresh_grant = grant();
        fresh_grant["connector_id"] = json!(connector);
        let (record, _) = grant_record(&fresh_grant, &preview(), connector, "Mac".into()).unwrap();
        accept_registration(&root, &mut config, record).unwrap();
        let restored = load(&root).unwrap();
        assert_eq!(restored.installation_id, installation);
        assert_eq!(restored.registrations[0].connector_id, connector);
        assert!(restored.registrations[0].enabled);
        assert!(!restored.auto_pair_blocked.contains_key(HOME));
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn automatic_secret_failure_never_falls_back_or_enables_and_has_public_safe_status() {
        let root = std::env::temp_dir().join(format!(
            "vortoone-native-secret-failure-{}",
            secrets::random_id().unwrap()
        ));
        let config = load(&root).unwrap();
        let manager = ConnectorManager::default();
        manager.inner.lock().unwrap().config = Some(config);
        let (record, token) =
            grant_record(&grant(), &preview(), CONNECTOR, "这台电脑".into()).unwrap();
        let fake = FakeStore {
            directory: root.clone(),
            fail: true,
            calls: Mutex::new(vec![]),
        };
        let message = native_secret(&fake, &record, &token).unwrap_err();
        auto_note(
            &manager,
            &root,
            Some((HOME, "secret_storage")),
            "secret_storage",
            &message,
        );
        let loaded = load(&root).unwrap();
        assert!(loaded.registrations.is_empty());
        assert_eq!(loaded.auto_pair_blocked[HOME], "secret_storage");
        assert_eq!(*fake.calls.lock().unwrap(), vec!["put_keychain"]);
        let output = serde_json::to_string(&manager.views().unwrap()).unwrap();
        assert!(!output.contains(&token) && !output.contains("vcn_"));
        assert!(output.contains("打开本机设置") && output.contains("secret_storage"));
        assert!(!fs::read_to_string(root.join("registrations.json"))
            .unwrap()
            .contains(&token));
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn native_intent_write_failure_prevents_the_remote_request() {
        let root = std::env::temp_dir().join(format!(
            "vortoone-native-intent-failure-{}",
            secrets::random_id().unwrap()
        ));
        let config = load(&root).unwrap();
        let manager = ConnectorManager::default();
        manager.inner.lock().unwrap().config = Some(config);
        fs::remove_file(root.join("registrations.json")).unwrap();
        fs::create_dir(root.join("registrations.json")).unwrap();
        let calls = std::sync::atomic::AtomicUsize::new(0);
        let result = native_with_intent(&manager, &root, HOME, || {
            calls.fetch_add(1, Ordering::Relaxed);
        });
        assert!(result.is_err());
        assert_eq!(calls.load(Ordering::Relaxed), 0);
        assert!(manager
            .inner
            .lock()
            .unwrap()
            .config
            .as_ref()
            .unwrap()
            .registrations
            .is_empty());
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn native_dispatch_crash_or_secret_failure_keeps_durable_intent_and_success_is_unique() {
        for outcome in ["crash", "secret_failure", "success"] {
            let root = std::env::temp_dir().join(format!(
                "vortoone-native-intent-order-{}",
                secrets::random_id().unwrap()
            ));
            let manager = ConnectorManager::default();
            manager.inner.lock().unwrap().config = Some(load(&root).unwrap());
            let calls = std::sync::atomic::AtomicUsize::new(0);
            let response = native_with_intent(&manager, &root, HOME, || {
                calls.fetch_add(1, Ordering::Relaxed);
                assert_eq!(
                    load(&root).unwrap().auto_pair_blocked[HOME],
                    "unknown_registration"
                );
                if outcome == "crash" {
                    Err("simulated process loss after dispatch")
                } else {
                    Ok(grant())
                }
            })
            .unwrap();
            if let Ok(grant) = response {
                let (record, token) =
                    grant_record(&grant, &preview(), CONNECTOR, "这台电脑".into()).unwrap();
                let fake = FakeStore {
                    directory: root.clone(),
                    fail: outcome == "secret_failure",
                    calls: Mutex::new(vec![]),
                };
                if native_secret(&fake, &record, &token).is_ok() {
                    accept_registration(
                        &root,
                        manager.inner.lock().unwrap().config.as_mut().unwrap(),
                        record,
                    )
                    .unwrap();
                }
            }
            // Simulate a new App using only committed disk state, without any
            // successful follow-up write after the remote dispatch or failure.
            let restarted = ConnectorManager::default();
            let committed = load(&root).unwrap();
            if outcome == "success" {
                assert_eq!(committed.registrations.len(), 1);
                assert!(!committed.auto_pair_blocked.contains_key(HOME));
            } else {
                assert!(committed.registrations.is_empty());
                assert_eq!(committed.auto_pair_blocked[HOME], "unknown_registration");
            }
            restarted.inner.lock().unwrap().config = Some(committed);
            assert!(native_with_intent(&restarted, &root, HOME, || {
                calls.fetch_add(1, Ordering::Relaxed);
            })
            .is_err());
            assert_eq!(calls.load(Ordering::Relaxed), 1);
            fs::remove_dir_all(root).unwrap();
        }
    }
    #[test]
    #[cfg(unix)]
    fn restore_delegates_to_a_private_helper_and_never_unpacks_or_deletes_the_existing_home() {
        for (answer, exit, succeeds) in [
            ("{\"ok\":true}", 0, true),
            (
                "{\"ok\":false,\"error\":\"must-not-be-returned\"}",
                2,
                false,
            ),
            ("{\"ok\":true}", 2, false),
        ] {
            let root = std::env::temp_dir().join(format!(
                "vortoone-desktop-restore-{}",
                secrets::random_id().unwrap()
            ));
            let source = root.join("source");
            let data = root.join("app data");
            fs::create_dir_all(source.join("src")).unwrap();
            fs::create_dir_all(data.join("home")).unwrap();
            fs::write(data.join("home/original.txt"), "original home must remain").unwrap();
            let archive = data.join("archive.tar.gz");
            fs::write(&archive, "fake archive passed only to trusted helper").unwrap();
            fs::write(source.join("src/runtime_entry.py"), format!("import os, pathlib, sys\nassert sys.argv[1]=='desktop-home-restore'\nassert sys.argv[2]=='--data-dir' and sys.argv[4]=='--archive'\nassert pathlib.Path(sys.argv[3],'home/original.txt').read_text()=='original home must remain'\nassert pathlib.Path(sys.argv[5]).is_file()\nassert sys.stdin.read()==''\nassert not any(k.startswith(('OPENAI_','VORTOONE_')) for k in os.environ)\nprint({answer:?},flush=True)\nsys.exit({exit})\n")).unwrap();
            let mut command =
                WorkerSource::Source("/usr/bin/python3".into(), source).base_command(&data);
            command
                .arg("desktop-home-restore")
                .arg("--data-dir")
                .arg(&data)
                .arg("--archive")
                .arg(&archive);
            let result = run_restore_helper(command);
            assert_eq!(result.is_ok(), succeeds);
            if let Err(error) = result {
                assert!(!error.contains("must-not-be-returned"));
            }
            assert_eq!(
                fs::read_to_string(data.join("home/original.txt")).unwrap(),
                "original home must remain"
            );
            fs::remove_dir_all(root).unwrap();
        }
    }
    #[test]
    #[cfg(unix)]
    fn healthy_quiet_worker_survives_multiple_parent_poll_timeouts() {
        let root = std::env::temp_dir().join(format!(
            "vortoone-connector-quiet-{}",
            secrets::random_id().unwrap()
        ));
        secrets::private_dir(&root).unwrap();
        fs::create_dir(root.join("src")).unwrap();
        fs::write(root.join("src/runtime_entry.py"), "import json, sys, time\nx=json.loads(sys.stdin.readline())\ntime.sleep(0.15)\nprint(json.dumps({'type':'status','state':'online','home_instance_id':x['home_instance_id'],'connector_id':x['connector_id']}),flush=True)\nassert sys.stdin.read()==''\n").unwrap();
        let command = WorkerSource::Source("/usr/bin/python3".into(), root.clone()).command(&root);
        let (record, token) = grant_record(&grant(), &preview(), CONNECTOR, "Mac".into()).unwrap();
        let (mut process, frames) =
            WorkerProcess::spawn(command, &record.request(token, false)).unwrap();
        let mut polls = 0;
        let frame = wait_frame(&frames, Duration::from_millis(10), || {
            polls += 1;
            matches!(process.child.try_wait(), Ok(None))
        })
        .unwrap();
        assert!(polls >= 3, "test must cross several parent timeouts");
        assert_eq!(accepted_status(&frame, &record), Some("online"));
        assert!(process.child.try_wait().unwrap().is_none());
        process.stop().unwrap();
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn local_remove_preserves_secret_evidence_when_stop_or_persistence_fails() {
        let (root, mut config) = configured();
        let secrets = FakeStore {
            directory: root.clone(),
            fail: false,
            calls: Mutex::new(vec![]),
        };
        assert!(remove_local(&root, &mut config, HOME, &secrets, || Err(
            "fake stop failure".into()
        ))
        .is_err());
        assert!(!load(&root).unwrap().registrations[0].enabled);
        assert!(secrets.calls.lock().unwrap().is_empty());
        fs::remove_file(root.join("registrations.json")).unwrap();
        fs::create_dir(root.join("registrations.json")).unwrap();
        assert!(remove_local(&root, &mut config, HOME, &secrets, || panic!(
            "must not stop before durable disable"
        ))
        .is_err());
        assert!(secrets.calls.lock().unwrap().is_empty());
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    #[cfg(unix)]
    fn revocation_ack_reaches_worker_only_after_durable_tombstone_and_secret_deletion() {
        let (root, mut config) = configured();
        fs::create_dir(root.join("src")).unwrap();
        fs::write(root.join("src/runtime_entry.py"), "import json, pathlib, sys\nx=json.loads(sys.stdin.readline())\nprint(json.dumps({'type':'status','state':'revoked','home_instance_id':x['home_instance_id'],'connector_id':x['connector_id']}),flush=True)\nassert json.loads(sys.stdin.readline())=={'type':'revocation_persisted'}\nassert json.loads(pathlib.Path('registrations.json').read_text())['registrations'][0]['enabled'] is False\npathlib.Path('ack-observed').write_text('yes')\n").unwrap();
        let command = WorkerSource::Source("/usr/bin/python3".into(), root.clone()).command(&root);
        let (mut process, frames) = WorkerProcess::spawn(
            command,
            &config.registrations[0]
                .request("only-pipe-secret-0123456789abcdef0123456789".into(), false),
        )
        .unwrap();
        assert_eq!(
            frames
                .recv_timeout(Duration::from_secs(5))
                .unwrap()
                .unwrap()["state"],
            "revoked"
        );
        let record = disable_pair(&root, &mut config, HOME).unwrap();
        let secrets = FakeStore {
            directory: root.clone(),
            fail: false,
            calls: Mutex::new(vec![]),
        };
        secrets.delete(&record.key(), record.storage).unwrap();
        process.confirm_revocation().unwrap();
        assert!(root.join("ack-observed").is_file());
        assert_eq!(*secrets.calls.lock().unwrap(), vec!["delete"]);
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    #[cfg(unix)]
    fn real_child_receives_secret_only_on_pipe_and_stops_on_parent_eof() {
        let root = std::env::temp_dir().join(format!(
            "vortoone-connector-worker-{}",
            secrets::random_id().unwrap()
        ));
        secrets::private_dir(&root).unwrap();
        fs::create_dir(root.join("src")).unwrap();
        let script = root.join("src/runtime_entry.py");
        fs::write(&script, "import json, os, sys\nx=json.loads(sys.stdin.readline())\nassert 'token' not in str(sys.argv)\nassert not any(k.startswith(('OPENAI_', 'VORTOONE_')) for k in os.environ)\nprint(json.dumps({'type':'status','state':'online','home_instance_id':x['home_instance_id'],'connector_id':x['connector_id']}),flush=True)\nassert sys.stdin.read()==''\n").unwrap();
        let (record, token) = grant_record(&grant(), &preview(), CONNECTOR, "Mac".into()).unwrap();
        let command = WorkerSource::Source("/usr/bin/python3".into(), root.clone()).command(&root);
        let (mut process, frames) =
            WorkerProcess::spawn(command, &record.request(token, false)).unwrap();
        let frame = frames
            .recv_timeout(Duration::from_secs(5))
            .unwrap()
            .unwrap();
        assert_eq!(accepted_status(&frame, &record), Some("online"));
        process.stop().unwrap();
        assert!(process.child.try_wait().unwrap().is_some());
        fs::remove_dir_all(root).unwrap();
    }
}
