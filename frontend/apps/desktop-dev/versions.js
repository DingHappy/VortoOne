const container = document.getElementById("private-version-controls");
const tauri = window.__TAURI_INTERNALS__;
let busy = false;
let refreshing = false;

function mountEnrolledControls() {
  container.innerHTML = `
    <section aria-labelledby="gray-title">
      <h2 id="gray-title">灰度更新</h2>
      <p>此电脑已登记。</p>
      <div class="row"><button id="gray-check" type="button">检查更新</button><button id="gray-leave" type="button" class="secondary">退出灰度</button></div>
      <p class="status" role="status" aria-atomic="true"></p>
    </section>
    <dialog aria-labelledby="gray-leave-title">
      <h2 id="gray-leave-title">退出此电脑的灰度更新？</h2>
      <p>已安装的版本会保留。</p>
      <form method="dialog" class="row"><button type="submit" value="cancel" class="secondary" autofocus>取消</button><button type="submit" value="leave">退出灰度</button></form>
    </dialog>
  `;
  const status = container.querySelector(".status");
  const leave = container.querySelector("#gray-leave");
  const check = container.querySelector("#gray-check");
  const confirmation = container.querySelector("dialog");

  async function run(command, pending, failure) {
    if (busy) return;
    busy = leave.disabled = check.disabled = true;
    status.classList.remove("error");
    status.textContent = pending;
    try {
      const result = await tauri.invoke(command);
      status.textContent = "";
      if (command === "gray_update_leave" && !result.enrolled) {
        container.replaceChildren();
        document.getElementById("version-title").focus();
      }
    } catch {
      status.classList.add("error");
      status.textContent = failure;
    } finally {
      busy = leave.disabled = check.disabled = false;
    }
  }

  check.addEventListener("click", async () => {
    await run("gray_update_check", "正在检查更新…", "无法检查更新，请稍后再试。");
    check.focus();
  });
  leave.addEventListener("click", () => {
    confirmation.returnValue = "";
    confirmation.showModal();
  });
  confirmation.addEventListener("close", async () => {
    if (confirmation.returnValue !== "leave") return;
    await run("gray_update_leave", "正在退出灰度更新…", "无法退出灰度更新，请稍后再试。");
    if (leave.isConnected) leave.focus();
  });
}

async function refresh() {
  if (!tauri || busy || refreshing) return;
  refreshing = true;
  let enrolled = false;
  try {
    const state = await tauri.invoke("gray_update_status");
    enrolled = state.supported === true && state.enrolled === true;
  } catch {
    // Public version information remains available if local status cannot be read.
  } finally {
    refreshing = false;
  }
  if (busy) return;
  if (!enrolled) container.replaceChildren();
  else if (!container.hasChildNodes()) mountEnrolledControls();
}

window.addEventListener("focus", refresh);
void refresh();

if (tauri && /^(Win|Linux)/.test(navigator.platform)) {
  document.addEventListener("keydown", (event) => {
    if (event.code !== "KeyU" || !event.ctrlKey || !event.altKey || !event.shiftKey || event.metaKey || event.repeat) return;
    event.preventDefault();
    void tauri.invoke("gray_update_open_enrollment").catch(() => {});
  });
}
