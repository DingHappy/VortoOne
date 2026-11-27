const element = (id) => document.getElementById(id);
const panel = element("gray-updates");
const status = element("gray-status");
const form = element("gray-enroll");
const code = element("gray-code");
const installed = element("gray-installed");
const actions = element("gray-actions");
const leaveConfirmation = element("gray-leave-confirm");
const invoke = (command, args) => window.__TAURI_INTERNALS__.invoke(command, args);
const controls = Array.from(panel.querySelectorAll("button, input"));
let busy = false;

function render(state) {
  panel.hidden = !state.supported;
  form.hidden = state.enrolled;
  actions.hidden = !state.enrolled;
  installed.textContent = state.version;
  element("gray-enrollment-state").textContent = state.enrolled ? "此电脑已登记" : "此电脑尚未登记";
}

async function run(work, failure) {
  if (busy) return;
  busy = true;
  for (const control of controls) control.disabled = true;
  status.textContent = "";
  status.classList.remove("error");
  try {
    await work();
  } catch {
    status.textContent = failure;
    status.classList.add("error");
  } finally {
    for (const control of controls) control.disabled = false;
    busy = false;
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const name = element("gray-name").value.trim();
  const enrollmentCode = code.value.trim();
  if (!name || !enrollmentCode) {
    status.textContent = "请输入设备名称和一次性登记码。";
    status.classList.add("error");
    return;
  }
  await run(async () => {
    const next = await invoke("gray_update_enroll", { input: { code: enrollmentCode, name } });
    render(next);
    form.reset();
    status.textContent = "设备已登记。管理员可以为这台电脑分配灰度版本。";
  }, "登记失败。请检查网络，确认登记码未过期且未使用，然后重试。");
  if (!actions.hidden) element("gray-check").focus();
});

element("gray-check").addEventListener("click", async () => {
  await run(async () => {
    await invoke("gray_update_check");
    render(await invoke("gray_update_status"));
  }, "无法检查灰度更新，请稍后再试。");
  if (!actions.hidden) element("gray-check").focus();
});

element("gray-leave").addEventListener("click", () => {
  if (busy) return;
  leaveConfirmation.returnValue = "";
  leaveConfirmation.showModal();
});

leaveConfirmation.addEventListener("close", async () => {
  if (leaveConfirmation.returnValue !== "leave") return;
  await run(async () => {
    const next = await invoke("gray_update_leave");
    render(next);
    status.textContent = "已退出灰度更新。";
  }, "无法退出灰度更新，请稍后再试。");
  (form.hidden ? element("gray-leave") : element("gray-name")).focus();
});

element("gray-admin").addEventListener("click", async () => {
  await run(() => invoke("gray_update_open_admin"), "无法打开灰度管理页，请稍后再试。");
  element("gray-admin").focus();
});

try {
  if (window.__TAURI_INTERNALS__) render(await invoke("gray_update_status"));
} catch {
  status.textContent = "暂时无法读取灰度登记状态。请重新打开版本管理后再试。";
  status.classList.add("error");
}
