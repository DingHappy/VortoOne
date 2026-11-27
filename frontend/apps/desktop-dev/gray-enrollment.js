const tauri = window.__TAURI_INTERNALS__;
const form = document.getElementById("enrollment-form");
const status = document.getElementById("enrollment-status");
const name = document.getElementById("device-name");
const code = document.getElementById("enrollment-code");
const submit = form.querySelector("button");
const openVersions = document.getElementById("open-versions");
const enrollmentErrors = new Map([
  ["invalid_input", "请填写有效的设备名称和 20 位登记码。"],
  ["code_rejected", "登记码无效、已过期或已使用。请联系管理员获取新码。"],
  ["network", "无法连接登记服务。请检查网络后重试。"],
  ["local_credential", "无法读取或保存本机登记信息。请检查本机权限，必要时向管理员获取新码。"],
  ["busy", "暂时无法登记。请稍后重试。"],
]);
let loading = false;

function setStatus(message, error = false) {
  status.textContent = message;
  status.classList.toggle("error", error);
}

function showEnrolled(message) {
  form.reset();
  form.hidden = true;
  setStatus(message);
  if (openVersions.hidden) {
    openVersions.hidden = false;
    openVersions.focus();
  }
}

openVersions.addEventListener("click", async () => {
  if (openVersions.disabled || openVersions.hidden) return;
  openVersions.disabled = true;
  try {
    await tauri.invoke("gray_update_open_versions");
  } catch {
    setStatus("无法打开版本管理。可从「工作区」菜单重试。", true);
  } finally {
    openVersions.disabled = false;
    if (document.hasFocus()) openVersions.focus();
  }
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (submit.disabled) return;
  const input = { name: name.value.trim(), code: code.value.trim() };
  if (!input.name || !/^[a-z0-9]{20}$/i.test(input.code)) {
    setStatus("请填写设备名称和 20 位登记码。", true);
    return;
  }
  submit.disabled = name.disabled = code.disabled = true;
  setStatus("正在登记此电脑…");
  try {
    await tauri.invoke("gray_update_enroll", { input });
    showEnrolled("已登记此电脑。收到版本分配后即可检查更新。");
  } catch (error) {
    if (error === "already_enrolled") showEnrolled("此电脑已登记。");
    else setStatus(enrollmentErrors.get(error) ?? "暂时无法完成登记，请稍后重试。", true);
  } finally {
    submit.disabled = name.disabled = code.disabled = false;
  }
  if (!form.hidden) code.focus();
});

async function loadEnrollment() {
  if (loading || !form.hidden || submit.disabled || openVersions.disabled) return;
  loading = true;
  try {
    const state = await tauri.invoke("gray_update_status");
    if (state.supported && state.enrolled) {
      showEnrolled("此电脑已登记。");
    } else if (state.supported) {
      setStatus("");
      openVersions.hidden = true;
      form.hidden = false;
      name.focus();
    } else {
      openVersions.hidden = true;
      setStatus("此安装形式无法登记更新。", true);
    }
  } catch {
    openVersions.hidden = true;
    setStatus("暂时无法读取设备登记状态。请重新打开邀请入口后再试。", true);
  } finally {
    loading = false;
  }
}

window.addEventListener("focus", loadEnrollment);
void loadEnrollment();
