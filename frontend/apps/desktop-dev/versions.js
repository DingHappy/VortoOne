const container = document.getElementById("private-version-controls");
const tauri = window.__TAURI_INTERNALS__;
const checkTimeFormat = new Intl.DateTimeFormat("zh-CN", { dateStyle: "short", timeStyle: "short" });
const checkMessages = new Map([
  ["awaiting_assignment", "此电脑已登记，正在等待管理员分配版本。"],
  ["paused", "分配的版本已暂停，请等待管理员恢复。"],
  ["up_to_date", "此电脑已达到管理员分配的版本。"],
  ["unavailable", "分配的版本暂不可用，请联系管理员。"],
  ["no_update", "此电脑暂时没有可安装的灰度更新。"],
  ["enrollment_invalid", "此电脑的灰度登记已失效。退出灰度后，可用新的登记码重新登记。"],
  ["declined", "已跳过本次更新。"],
  ["installing", "已启动安装，完成后应用会重新打开。"],
]);
let busy = false;
let refreshing = false;

function mountEnrolledControls() {
  container.innerHTML = `
    <section aria-labelledby="gray-title">
      <h2 id="gray-title">灰度更新</h2>
      <p>此电脑已登记。</p>
      <div class="row"><button id="gray-check" type="button">检查更新</button><button id="gray-leave" type="button" class="secondary">退出灰度</button></div>
      <p class="status" role="status" aria-atomic="true"></p>
      <p class="check-time" hidden>最近完成检查 <time></time></p>
    </section>
    <dialog aria-labelledby="gray-leave-title">
      <h2 id="gray-leave-title">退出此电脑的灰度更新？</h2>
      <p>已安装的版本会保留。</p>
      <form method="dialog" class="row"><button type="submit" value="cancel" class="secondary" autofocus>取消</button><button type="submit" value="leave">退出灰度</button></form>
    </dialog>
  `;
  const status = container.querySelector(".status");
  const checkTime = container.querySelector(".check-time");
  const time = checkTime.querySelector("time");
  const leave = container.querySelector("#gray-leave");
  const check = container.querySelector("#gray-check");
  const confirmation = container.querySelector("dialog");

  async function run(pending, failure, work) {
    if (busy) return;
    busy = leave.disabled = check.disabled = true;
    status.classList.remove("error");
    status.textContent = pending;
    try {
      await work();
    } catch {
      status.classList.add("error");
      status.textContent = failure;
    } finally {
      busy = leave.disabled = check.disabled = false;
    }
  }

  check.addEventListener("click", async () => {
    await run("正在检查更新…", "无法检查更新，请稍后再试。", async () => {
      const result = await tauri.invoke("gray_update_check");
      const message = checkMessages.get(result);
      if (!message) throw new Error("Unexpected update check result");
      const checkedAt = new Date();
      status.classList.toggle("error", result === "enrollment_invalid");
      status.textContent = message;
      time.dateTime = checkedAt.toISOString();
      time.textContent = checkTimeFormat.format(checkedAt);
      checkTime.hidden = false;
    });
    if (check.isConnected && document.hasFocus()) check.focus();
  });
  leave.addEventListener("click", () => {
    confirmation.returnValue = "";
    confirmation.showModal();
  });
  confirmation.addEventListener("close", async () => {
    if (confirmation.returnValue !== "leave") return;
    await run("正在退出灰度更新…", "无法退出灰度更新，请稍后再试。", async () => {
      const result = await tauri.invoke("gray_update_leave");
      if (result.enrolled) throw new Error("The device is still enrolled");
      container.replaceChildren();
      document.getElementById("version-title").focus();
    });
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
