const tauri = window.__TAURI_INTERNALS__;
const form = document.getElementById("enrollment-form");
const status = document.getElementById("enrollment-status");
const name = document.getElementById("device-name");
const code = document.getElementById("enrollment-code");
const submit = form.querySelector("button");

function setStatus(message, error = false) {
  status.textContent = message;
  status.classList.toggle("error", error);
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (submit.disabled) return;
  const input = { name: name.value.trim(), code: code.value.trim() };
  if (!input.name || !/^[a-z0-9]{20}$/i.test(input.code)) {
    setStatus("请填写设备名称和 20 位登记码。", true);
    return;
  }
  submit.disabled = name.disabled = code.disabled = true;
  try {
    await tauri.invoke("gray_update_enroll", { input });
    form.reset();
    form.hidden = true;
    setStatus("已登记此电脑。收到版本分配后，可在「版本管理」中检查更新。");
    status.focus();
  } catch {
    setStatus("登记失败。请核对登记码，确认尚未过期或使用，再重试。", true);
  } finally {
    submit.disabled = name.disabled = code.disabled = false;
  }
  if (!form.hidden) code.focus();
});

async function loadEnrollment() {
  if (!form.hidden || submit.disabled) return;
  try {
    const state = await tauri.invoke("gray_update_status");
    if (!state.supported) setStatus("此安装形式无法登记更新。", true);
    else if (state.enrolled) setStatus("此电脑已登记。请在「版本管理」中检查更新。");
    else {
      setStatus("");
      form.hidden = false;
      name.focus();
    }
  } catch {
    setStatus("暂时无法读取设备登记状态。请重新打开邀请入口后再试。", true);
  }
}

window.addEventListener("focus", loadEnrollment);
void loadEnrollment();
