const element = (id) => document.getElementById(id);
const invoke = (command, args) => window.__TAURI_INTERNALS__.invoke(command, args);
const status = element("status");
let preview = null;
let busy = false;
let reading = false;
let folderTarget = null;
let pairedTaskTarget = null;
// 新界面「连接电脑」打开的配对链接带来的码：核对完家的身份再填进去，不替人点确认
let linkedCode = "";
const taskControl = element("paired-task-allow-control");
const taskLogin = element("paired-task-allow-login");
function resetTaskConsent() {
  taskControl.checked = taskLogin.checked = false;
  taskLogin.disabled = true;
}
taskControl.addEventListener("change", () => {
  taskLogin.disabled = !taskControl.checked;
  if (taskLogin.disabled) taskLogin.checked = false;
});
const states = { connecting: "正在连接", online: "已连接 · 全能力未就绪", disconnected: "当前未连接", revoked: "已停用", replaced: "连接已由另一进程接替", error: "连接不可用，请检查版本或重新配对" };
const shareStates = {
  idle: "打开并分享专用浏览器",
  starting: "取消打开浏览器",
  sharing: "停止分享",
  stopping: "正在停止分享…",
  unavailable: "重试分享专用浏览器",
};
const desktopShareStates = {
  idle: "选择并分享显示器",
  starting: "取消选择显示器",
  starting_with_control: "取消选择显示器",
  sharing: "停止桌面分享",
  sharing_with_control: "停止桌面分享",
  controlling: "停止桌面接管",
  stopping: "正在停止桌面分享…",
  unavailable: "重试桌面分享",
};
function message(error) { status.textContent = typeof error === "string" ? error : "操作未完成，请重试。"; }
function node(tag, text, className) {
  const item = document.createElement(tag);
  item.textContent = text;
  if (className) item.className = className;
  return item;
}
function shareButton(record, state, label, command, allowControl = false) {
  const action = node("button", label, "secondary");
  action.type = "button";
  action.disabled = state === "stopping";
  action.addEventListener("click", async () => {
    if (busy || action.disabled) return;
    busy = true;
    action.disabled = true;
    status.textContent = "";
    const share = state === "idle" || state === "unavailable";
    try { await invoke(command, { registrationId: record.registrationId, share, ...(allowControl && { allowControl }) }); }
    catch (error) { message(error); }
    finally { busy = false; await refresh(); }
  });
  return action;
}
const taskBrowser = element("task-browser");
const taskBrowserToggle = element("task-browser-toggle");
const taskBrowserStatus = element("task-browser-status");
let taskBrowserEnabled = null;

async function readTaskBrowser() {
  try {
    taskBrowserEnabled = await invoke("task_browser_enabled");
    taskBrowser.hidden = typeof taskBrowserEnabled !== "boolean";
    taskBrowserToggle.textContent = taskBrowserEnabled ? "关闭任务浏览器" : "启用任务浏览器";
    taskBrowserToggle.disabled = taskBrowser.hidden;
  } catch {
    taskBrowser.hidden = false;
    taskBrowserToggle.disabled = true;
    taskBrowserStatus.textContent = "任务浏览器设置暂时不可用。";
  }
}
taskBrowserToggle.addEventListener("click", async () => {
  if (taskBrowserToggle.disabled || typeof taskBrowserEnabled !== "boolean") return;
  taskBrowserToggle.disabled = true;
  taskBrowserStatus.textContent = "";
  try {
    await invoke("task_browser_set", { enabled: !taskBrowserEnabled });
  } catch (error) {
    taskBrowserStatus.textContent = typeof error === "string" ? error : "无法更改任务浏览器设置。";
  }
  await readTaskBrowser();
});
void readTaskBrowser();

async function takePairLink() {
  let link = null;
  try { link = await invoke("pair_link_take"); } catch { return; }
  if (!link || typeof link.server !== "string" || typeof link.code !== "string") return;
  reset();
  element("origin").value = link.server;
  linkedCode = link.code;
  status.textContent = "已从「连接电脑」的链接填好家的地址。填上电脑名称，核对家的身份；配对码会在核对后填好，确认仍要你来点。";
}

async function refresh() {
  if (reading || busy) return;
  await takePairLink();
  reading = true;
  try {
    const answer = await invoke("computers_get");
    const records = Array.isArray(answer) ? answer : answer.computers;
    element("auto-status").textContent = answer.autoPairing?.message || "";
    const list = element("connections");
    list.replaceChildren();
    if (!records.length) list.append(node("p", "尚未向任何家登记这台电脑。", "muted"));
    for (const record of records) {
      const card = node("article", "", "connection");
      card.append(node("h3", record.name), node("span", states[record.state] || "状态未知", "badge"), node("p", record.origin), node("p", `家身份：${record.homeInstanceId}`, "identity"));
      card.append(node("p", record.enabled === false ? "本机配对已停用；移除这条记录可重试清理残留凭据。" : record.secretStorage === "private_file" ? "降级存储：本机私有文件（0700 目录 / 0600 文件）" : "凭据保存在此开发版独立的系统钥匙串项中。", "muted"));
      if (record.enabled === true) {
        const configure = node("button", "本机文件夹", "secondary");
        configure.type = "button";
        configure.addEventListener("click", async () => {
          if (busy) return;
          folderTarget = record.registrationId;
          element("folder-home").textContent = record.origin;
          element("folder-panel").hidden = false;
          element("folder-list").replaceChildren();
          element("folder-read").checked = true;
          element("folder-write").checked = false;
          await folderSettings("list");
          element("folder-choose").focus();
        });
        card.append(configure);
        if (record.state === "online" && Object.hasOwn(shareStates, record.browserShare)) {
          const share = record.browserShare === "idle" || record.browserShare === "unavailable";
          card.append(shareButton(record, record.browserShare, shareStates[record.browserShare], "computer_browser_share"));
          if (share) {
            const task = node("button", "打开任务浏览器", "secondary");
            task.type = "button";
            task.addEventListener("click", () => {
              if (busy) return;
              pairedTaskTarget = record.registrationId;
              element("paired-task-home").textContent = record.origin;
              element("paired-task-panel").hidden = false;
              element("paired-task-code").value = "";
              resetTaskConsent();
              element("paired-task-status").textContent = "";
              element("paired-task-code").focus();
            });
            card.append(task);
          }
          if (record.browserShare === "sharing") {
            card.append(node("p", "专用浏览器正在分享 · 最长 5 分钟", "muted"));
          }
          if (record.browserShare === "unavailable") {
            card.append(node("p", "分享未能启动或已中断。请检查本机 Chromium 系浏览器、电脑连接和家的版本。", "muted"));
          }
        }
        if (record.state === "online" && Object.hasOwn(desktopShareStates, record.desktopShare)) {
          const state = record.desktopShare;
          card.append(shareButton(record, state, desktopShareStates[state], "computer_desktop_share"));
          if (state === "idle" || state === "unavailable") {
            card.append(shareButton(record, state, "分享并允许接管", "computer_desktop_share", true));
          }
          if (state === "controlling") {
            card.append(node("p", "配对人正在接管所选显示器", "muted"));
          } else if (state === "sharing_with_control") {
            card.append(node("p", "本次显示器分享已允许配对人接管 · 最长 5 分钟", "muted"));
          } else if (state === "sharing") {
            card.append(node("p", "所选显示器正在只读分享 · 最长 5 分钟", "muted"));
          } else if (state === "unavailable") {
            card.append(node("p", "桌面分享暂不可用。请检查 macOS 版本、屏幕录制权限及电脑连接。", "muted"));
            card.append(node("p", "桌面接管需要 macOS 15.2 或更新版本及辅助功能权限。", "muted"));
          }
        }
      }
      const remove = node("button", "移除本机连接", "secondary");
      remove.type = "button";
      remove.addEventListener("click", async () => {
        if (busy) return;
        busy = true;
        remove.disabled = true;
        try { await invoke("computer_remove", { registrationId: record.registrationId }); status.textContent = "已停止本机连接并删除凭据。家里的登记可由所有者另行移除。"; }
        catch (error) { message(error); }
        finally { busy = false; await refresh(); }
      });
      card.append(remove);
      list.append(card);
    }
  } catch (error) { message(error); }
  finally { reading = false; }
}
element("paired-task-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busy || !pairedTaskTarget) return;
  const input = element("paired-task-code");
  const taskCode = input.value.replace(/\s+/g, "").toLowerCase();
  const result = element("paired-task-status");
  if (!/^[0-9a-f]{32}$/.test(taskCode)) {
    result.textContent = "请输入接管卡生成的一次性任务码。";
    return;
  }
  const button = element("paired-task-open");
  const allowControl = taskControl.checked === true;
  const allowLogin = allowControl && taskLogin.checked === true;
  input.value = "";
  resetTaskConsent();
  button.disabled = true;
  busy = true;
  result.textContent = "";
  try {
    await invoke("computer_browser_share", { registrationId: pairedTaskTarget, share: true, taskCode,
      ...(allowControl ? { allowControl: true } : {}), ...(allowLogin ? { allowLogin: true } : {}) });
    result.textContent = "正在打开专用窗口。请在窗口输入接管卡上的站点地址。";
  } catch (error) {
    result.textContent = typeof error === "string" ? error : "无法打开任务浏览器。";
  } finally {
    busy = false;
    button.disabled = false;
    await refresh();
  }
});
element("paired-task-cancel").addEventListener("click", () => {
  if (busy) return;
  pairedTaskTarget = null;
  element("paired-task-code").value = "";
  resetTaskConsent();
  element("paired-task-panel").hidden = true;
});
function reset() {
  preview = null;
  element("code").value = "";
  element("preview-form").hidden = false;
  element("pair-form").hidden = true;
}
async function folderSettings(operation, extra = {}) {
  if (!folderTarget || busy) return;
  busy = true;
  element("folder-choose").disabled = true;
  element("folder-status").textContent = "";
  try {
    const answer = await invoke("computer_folder_settings", { registrationId: folderTarget, operation, ...extra });
    const list = element("folder-list");
    list.replaceChildren();
    for (const folder of answer.folders) {
      const row = node("div", "", "folder-grant");
      const permissions = [folder.read ? "读取" : "", folder.write ? "新建" : ""].filter(Boolean).join("、");
      const remove = node("button", "撤销授权", "secondary");
      remove.type = "button";
      remove.addEventListener("click", () => folderSettings("revoke", { folderId: folder.folder_id }));
      const details = node("div", "");
      details.append(node("span", `${folder.alias} · ${permissions}`), node("p", folder.directory, "muted"));
      row.append(details, remove);
      list.append(row);
    }
    if (!answer.folders.length) list.append(node("p", "还没有开放的文件夹。", "muted"));
    if (operation === "grant") element("folder-status").textContent = "已保存本机文件夹授权。";
    if (operation === "revoke") element("folder-status").textContent = "已撤销本机文件夹授权。";
  } catch (error) {
    element("folder-status").textContent = typeof error === "string" ? error : "本机文件夹设置暂时不可用。";
  } finally {
    busy = false;
    element("folder-choose").disabled = false;
  }
}
element("folder-choose").addEventListener("click", () => folderSettings("grant", {
  read: element("folder-read").checked, write: element("folder-write").checked,
}));
element("folder-close").addEventListener("click", () => {
  if (busy) return;
  folderTarget = null;
  element("folder-panel").hidden = true;
  element("folder-list").replaceChildren();
});
element("refresh").addEventListener("click", refresh);
element("cancel").addEventListener("click", () => { if (!busy) { reset(); status.textContent = ""; } });
element("preview-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busy) return;
  busy = true;
  element("preview").disabled = true;
  status.textContent = "正在通过受验证的连接核对家的身份…";
  try {
    const name = element("name").value.trim();
    if (!name) throw "请填写电脑名称。";
    preview = { ...(await invoke("computer_pair_preview", { origin: element("origin").value.trim() })), name };
    element("verified-origin").textContent = preview.origin;
    element("verified-home").textContent = preview.homeInstanceId;
    element("verified-name").textContent = name;
    element("preview-form").hidden = true;
    element("pair-form").hidden = false;
    element("code").focus();
    status.textContent = "核对以上家的地址和身份，再输入在这个家生成的电脑配对码。";
    if (linkedCode) {
      element("code").value = linkedCode;
      linkedCode = "";
      status.textContent = "核对以上家的地址和身份；配对码已从链接填好，确认无误再点「确认配对」。";
    }
  } catch (error) { message(error); }
  finally { busy = false; element("preview").disabled = false; }
});
element("pair-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busy || !preview) return;
  busy = true;
  element("confirm").disabled = true;
  const code = element("code").value;
  element("code").value = "";
  status.textContent = "请在本机确认窗口核对配对。全部执行能力仍关闭。";
  try {
    await invoke("computer_pair_confirm", { previewId: preview.previewId, code, name: preview.name });
    status.textContent = "已登记电脑并开始连接。可为这个家设置本机文件夹授权。";
  } catch (error) { message(error); }
  finally { reset(); busy = false; element("confirm").disabled = false; await refresh(); }
});
await refresh();
setInterval(refresh, 1500);
