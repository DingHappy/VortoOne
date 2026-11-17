const element = (id) => document.getElementById(id);
const invoke = (command, args) => window.__TAURI_INTERNALS__.invoke(command, args);
const status = element("status");
let preview = null;
let busy = false;
let reading = false;
const states = { connecting: "正在连接", online: "已连接 · 全能力未就绪", disconnected: "当前未连接", revoked: "已停用", replaced: "连接已由另一进程接替", error: "连接不可用，请检查版本或重新配对" };
function message(error) { status.textContent = typeof error === "string" ? error : "操作未完成，请重试。"; }
function node(tag, text, className) {
  const item = document.createElement(tag);
  item.textContent = text;
  if (className) item.className = className;
  return item;
}
async function refresh() {
  if (reading || busy) return;
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
function reset() {
  preview = null;
  element("code").value = "";
  element("preview-form").hidden = false;
  element("pair-form").hidden = true;
}
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
    status.textContent = "已登记电脑并开始连接。全部执行能力尚未就绪。";
  } catch (error) { message(error); }
  finally { reset(); busy = false; element("confirm").disabled = false; await refresh(); }
});
await refresh();
setInterval(refresh, 1500);
