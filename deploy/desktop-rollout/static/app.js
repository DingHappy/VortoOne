const apiRoot = "/api/desktop-rollout/admin";
const signIn = "/admin/releases/sign-in";
const element = (id) => document.getElementById(id);
const notice = element("notice");
const dashboard = element("dashboard");
const logout = element("logout");
const codeInput = element("enrollment-code");
const assignment = element("assignment");
const targetVersion = element("target-version");
const applyAssignment = element("apply-assignment");
const beginAssignment = element("begin-assignment");
const selectedDevices = new Set();
const selectionLimit = 50;
const cancelVersion = "clear";
const dateFormat = new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" });
const platformNames = {
  "darwin-aarch64": "macOS · Apple 芯片",
  "windows-x86_64-nsis": "Windows · x64",
  "linux-x86_64-deb": "Ubuntu · amd64",
};
const stateNames = {
  enrolled: "已登记",
  assigned: "等待设备检查",
  update_available: "已检测到更新",
  installing: "安装中",
  installed: "已安装",
  failed: "更新失败",
  declined: "已跳过",
  up_to_date: "已是目标版本",
  disabled: "已撤销",
};
let busy = false;
let expiryTimer;
let devices = [];
let releases = [];

function node(tag, text = "", className = "") {
  const item = document.createElement(tag);
  item.textContent = text;
  item.className = className;
  return item;
}

function action(label, className, handler) {
  const button = node("button", label, className);
  button.type = "button";
  button.addEventListener("click", handler);
  return button;
}

function showNotice(message, error = false) {
  notice.hidden = !message;
  notice.textContent = message;
  notice.classList.toggle("error", error);
}

function formattedDate(iso) {
  const timestamp = Date.parse(iso);
  return Number.isFinite(timestamp) ? dateFormat.format(timestamp) : "—";
}

function clearCode() {
  clearTimeout(expiryTimer);
  codeInput.value = "";
  element("expiry").textContent = "";
  element("copy-code").disabled = false;
  element("enrollment-details").hidden = true;
}

function leaveDashboard(destination) {
  clearCode();
  showAssignment(false);
  devices = [];
  releases = [];
  element("devices").replaceChildren();
  element("releases").replaceChildren();
  document.querySelector("main").hidden = true;
  window.location.replace(destination);
}

async function request(path, method = "GET", body) {
  const response = await fetch(apiRoot + path, {
    method,
    credentials: "same-origin",
    cache: "no-store",
    redirect: "error",
    ...(body === undefined ? {} : {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  });
  if (!response.ok) throw Object.assign(new Error("Rollout request was rejected"), { status: response.status });
  if (response.status === 204) return;
  return response.json();
}

function failureMessage(error, fallback) {
  if (error.status === 429) return "操作过于频繁，请稍后再试。";
  if (error.status === 403) return "服务器拒绝了当前页面的请求，请重新打开管理页。";
  if (error.status === 404 || error.status === 409) return "设备或版本状态已变化，请刷新后重试。";
  return fallback;
}

async function run(button, work, fallback) {
  if (busy) return;
  busy = true;
  if (button) button.disabled = true;
  showNotice("");
  try {
    await work();
  } catch (error) {
    if (error.status === 401) {
      leaveDashboard(signIn + "?expired=1");
    } else {
      showNotice(failureMessage(error, fallback), true);
    }
  } finally {
    if (button) button.disabled = false;
    busy = false;
  }
}

function fact(label, value, className) {
  const entry = node("div");
  entry.append(node("dt", label), node("dd", value, className));
  return entry;
}

function syncSelection() {
  const version = targetVersion.value === cancelVersion ? null : targetVersion.value;
  const platforms = releases.find((release) => release.version === version && !release.paused)?.platforms ?? [];
  const eligible = new Set(devices.filter((device) =>
    device.active && device.target_version !== version && (version === null || platforms.includes(device.platform)),
  ).map((device) => device.id));
  for (const id of selectedDevices) {
    if (!eligible.has(id)) selectedDevices.delete(id);
  }
  for (const input of element("devices").querySelectorAll("input[type='checkbox']")) {
    const selected = selectedDevices.has(input.value);
    input.closest("label").hidden = assignment.hidden;
    input.checked = selected;
    input.disabled = !eligible.has(input.value) || (!selected && selectedDevices.size >= selectionLimit);
  }
  applyAssignment.disabled = !selectedDevices.size;
  const description = element("selection-status");
  if (!targetVersion.value) description.textContent = "选择版本后勾选设备。";
  else if (!eligible.size) description.textContent = "没有可应用此分配的设备。";
  else if (selectedDevices.size === selectionLimit) description.textContent = "已选 50 台设备，已达单次上限。";
  else if (selectedDevices.size) description.textContent = "已选择 " + selectedDevices.size + " 台设备。";
  else description.textContent = "勾选要应用此分配的设备。";
}

function showAssignment(show) {
  assignment.hidden = !show;
  beginAssignment.setAttribute("aria-expanded", String(show));
  if (!show) {
    targetVersion.value = "";
    selectedDevices.clear();
  }
  syncSelection();
}

function makeDevice(device) {
  const row = node("li");
  const head = node("div", "", "device-header");
  const status = device.active ? device.status : "disabled";
  const name = node("div", "", "device-name");
  if (device.active) {
    const check = document.createElement("input");
    check.type = "checkbox";
    check.value = device.id;
    check.setAttribute("aria-label", "选择设备 " + device.name);
    check.addEventListener("change", () => {
      if (check.checked) selectedDevices.add(device.id);
      else selectedDevices.delete(device.id);
      syncSelection();
    });
    const label = node("label", "", "device-check");
    label.append(check);
    name.append(label);
  }
  name.append(node("h3", device.name));
  const controls = node("div", "", "actions");
  controls.append(node("span", stateNames[status] ?? "等待设备检查",
    status === "up_to_date" || status === "installed" ? "badge ready" : "badge"));
  head.append(name, controls);
  const facts = node("dl", "", "facts");
  facts.append(
    fact("平台", platformNames[device.platform] ?? device.platform),
    fact("当前版本", device.current_version || "—", "version"),
    fact("分配版本", device.target_version ?? "未分配灰度版本", device.target_version ? "version" : ""),
    fact("最近检查", formattedDate(device.last_seen_at)),
  );
  row.append(head, facts);
  if (device.active) {
    const remove = action("撤销设备", "danger", () => {
      if (busy || !window.confirm("撤销「" + device.name + "」的灰度资格？它将停止接收灰度更新，已安装的版本会保留。")) return;
      void run(remove, async () => {
        await request("/devices/" + encodeURIComponent(device.id), "DELETE");
        await refreshAfterChange("设备的灰度资格已撤销。", null, element("refresh"));
      }, "无法撤销设备，请稍后再试。");
    });
    remove.setAttribute("aria-label", "撤销设备 " + device.name);
    controls.append(remove);
  }
  return row;
}

function makeRelease(release) {
  const row = node("li", "", "release-row");
  const heading = node("div");
  heading.append(node("h3", release.version, "version"),
    node("p", release.platforms.map((platform) => platformNames[platform] ?? platform).join("、") +
      " · " + formattedDate(release.created_at), "muted"));
  const controls = node("div", "", "actions");
  const toggle = action(release.paused ? "恢复" : "暂停", "secondary", () => {
    void run(toggle, async () => {
      await request("/releases/" + encodeURIComponent(release.version), "PATCH", { paused: !release.paused });
      await refreshAfterChange(release.paused ? "已恢复灰度版本。" : "已暂停灰度版本。", toggle.id, element("refresh"));
    }, "无法更新发布状态，请稍后再试。");
  });
  toggle.id = "release-" + release.version;
  toggle.setAttribute("aria-label", toggle.textContent + "版本 " + release.version);
  controls.append(node("span", release.paused ? "已暂停" : "可分配", release.paused ? "badge" : "badge ready"), toggle);
  row.append(heading, controls);
  return row;
}

async function loadState() {
  const next = await request("/state");
  devices = next.devices;
  releases = next.releases;
  const previousTarget = targetVersion.value;
  targetVersion.replaceChildren(new Option("请选择版本", ""), new Option("取消分配", cancelVersion),
    ...releases.filter((release) => !release.paused).map((release) => new Option(release.version, release.version)));
  targetVersion.value = Array.from(targetVersion.options).some((option) => option.value === previousTarget)
    ? previousTarget : "";
  beginAssignment.disabled = !devices.some((device) => device.active);
  const orderedDevices = [...devices].sort((a, b) => Number(b.active) - Number(a.active) || a.name.localeCompare(b.name, "zh-CN"));
  element("devices").replaceChildren(...orderedDevices.map(makeDevice));
  element("releases").replaceChildren(...releases.map(makeRelease));
  element("no-devices").hidden = devices.length > 0;
  element("no-releases").hidden = releases.length > 0;
  dashboard.hidden = logout.hidden = false;
  syncSelection();
}

async function refreshAfterChange(message, focusId, focusFallback) {
  try {
    await loadState();
    showNotice(message);
    if (document.activeElement === document.body) ((focusId && element(focusId)) || focusFallback).focus();
  } catch (error) {
    if (error.status === 401) throw error;
    showNotice(message + " 列表刷新失败，请手动刷新。", true);
  }
}

logout.addEventListener("click", () => {
  void run(logout, async () => {
    await request("/logout", "POST", {});
    leaveDashboard(signIn);
  }, "无法退出登录，请稍后再试。");
});

element("refresh").addEventListener("click", (event) => {
  void run(event.currentTarget, loadState, "无法刷新设备和版本，请稍后再试。");
});

beginAssignment.addEventListener("click", () => {
  if (busy) return;
  showAssignment(assignment.hidden);
  if (!assignment.hidden) targetVersion.focus();
});

element("cancel-assignment").addEventListener("click", () => {
  if (busy) return;
  showAssignment(false);
  beginAssignment.focus();
});

targetVersion.addEventListener("change", syncSelection);

assignment.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!targetVersion.value || !selectedDevices.size) return;
  const version = targetVersion.value === cancelVersion ? null : targetVersion.value;
  const device_ids = Array.from(selectedDevices);
  await run(applyAssignment, async () => {
    await request("/assignments", "PUT", { device_ids, version });
    showAssignment(false);
    await refreshAfterChange(version === null ? "已取消所选设备的分配。" : "已为所选设备分配版本。",
      beginAssignment.id, element("refresh"));
    beginAssignment.focus();
  }, "无法保存版本分配，请稍后再试。");
  syncSelection();
});

element("enrollment").addEventListener("click", (event) => {
  void run(event.currentTarget, async () => {
    const { code, expires_at } = await request("/enrollments", "POST", {});
    clearCode();
    element("enrollment-details").hidden = false;
    codeInput.value = code;
    element("expiry").textContent = "有效期至 " + formattedDate(expires_at);
    expiryTimer = setTimeout(() => {
      codeInput.value = "";
      element("copy-code").disabled = true;
      element("expiry").textContent = "登记码已过期，请生成新的登记码。";
    }, Math.max(0, Date.parse(expires_at) - Date.now()));
    codeInput.focus();
    codeInput.select();
  }, "无法生成登记码，请稍后再试。");
});

element("copy-code").addEventListener("click", async () => {
  if (!codeInput.value) return;
  try {
    await navigator.clipboard.writeText(codeInput.value);
    showNotice("已复制登记码。");
  } catch {
    codeInput.focus();
    codeInput.select();
    showNotice("请从已选中的输入框复制登记码。");
  }
});

const stateError = "暂时无法读取设备，请刷新后重试。";
await run(null, async () => {
  showNotice("正在读取灰度更新管理状态…");
  await loadState();
  showNotice("");
}, stateError);

window.addEventListener("pageshow", (event) => {
  if (event.persisted) {
    dashboard.hidden = logout.hidden = true;
    void run(null, loadState, stateError);
  }
});
