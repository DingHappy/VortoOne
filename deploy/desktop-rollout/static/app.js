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
const machineForm = element("machine-grant");
const machineInput = element("machine-code");
const machineTarget = element("machine-version");
const authorizeMachine = element("authorize-machine");
const beginMachine = element("begin-machine");
const selectedDevices = new Set();
const selectionLimit = 50;
const cancelVersion = "clear";
const dateFormat = new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" });
const platformNames = {
  "darwin-aarch64": "macOS · Apple 芯片",
  "windows-x86_64-nsis": "Windows · x64",
  "linux-x86_64-deb": "Ubuntu · amd64",
};
const machinePlatforms = new Map([
  ["mac", "darwin-aarch64"],
  ["win", "windows-x86_64-nsis"],
  ["linux", "linux-x86_64-deb"],
]);
const stateNames = {
  enrolled: "已登记",
  awaiting_machine: "等待设备首次检查",
  assigned: "等待设备检查",
  update_available: "已检测到更新",
  installing: "安装中",
  installed: "已安装",
  failed: "更新失败",
  declined: "已跳过",
  up_to_date: "已达到目标版本",
  disabled: "已撤销",
};
let busy = false;
let expiryTimer;
let devices = [];
let releases = [];
let assignmentDeviceId = null;

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
  element("invite-instructions").value = "";
  element("invite-instructions").hidden = true;
  element("expiry").textContent = "";
  element("copy-code").disabled = false;
  element("enrollment-details").hidden = true;
}

function leaveDashboard(destination) {
  clearCode();
  showAssignment(false);
  showMachineForm(false);
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

function isNewerVersion(version, installed) {
  const candidate = version.split(".").map(Number);
  const current = installed.split(".").map(Number);
  const firstDifference = candidate.findIndex((part, i) => part !== current[i]);
  return firstDifference !== -1 && candidate[firstDifference] > current[firstDifference];
}

function releaseBlock(device, release) {
  if (release.paused) return "已暂停";
  if (!release.platforms.includes(device.platform)) return "没有适用的平台安装包";
  if (!isNewerVersion(release.version, device.current_version)) return "不高于当前版本";
  return device.target_version === release.version ? "已分配此版本" : "";
}

function waitingForReport(device) {
  return Boolean(device.machine_code) && device.last_seen_at === "1970-01-01T00:00:00Z";
}

function assignmentProgress(version) {
  const assigned = devices.filter((device) =>
    device.active && device.target_version && (!version || device.target_version === version));
  const unreported = assigned.filter(waitingForReport).length;
  const ready = assigned.filter((device) => !waitingForReport(device) &&
    !isNewerVersion(device.target_version, device.current_version)).length;
  return { total: assigned.length, unreported, pending: assigned.length - unreported - ready, ready };
}

function machineCode() {
  return machineInput.value.trim().toLowerCase();
}

function renderMachineChoices() {
  const code = machineCode();
  const match = /^(mac|win|linux)-[0-9a-f]{64}$/.exec(code);
  const platform = match && machinePlatforms.get(match[1]);
  const device = devices.find((row) => row.machine_code === code);
  const previous = machineTarget.value;
  const options = [new Option("请选择目标版本", "")];
  if (platform) {
    for (const release of releases) {
      if (release.paused || !release.platforms.includes(platform)) continue;
      if (device && releaseBlock(device, release)) continue;
      options.push(new Option(release.version, release.version));
    }
  }
  machineTarget.replaceChildren(...options);
  machineTarget.value = options.some((option) => option.value === previous) ? previous : "";
  machineTarget.disabled = options.length === 1;
  authorizeMachine.disabled = !machineTarget.value;
  element("machine-status").textContent = platform
    ? options.length > 1 ? platformNames[platform] : platformNames[platform] + " 暂无可分配的版本。"
    : code ? "请粘贴从目标电脑复制的完整机器码。" : "";
}

function showMachineForm(show) {
  machineForm.hidden = !show;
  beginMachine.setAttribute("aria-expanded", String(show));
  if (!show) machineForm.reset();
  renderMachineChoices();
}

function assignmentDevices() {
  return devices.filter((device) => device.active && (!assignmentDeviceId || device.id === assignmentDeviceId));
}

function renderAssignmentChoices() {
  const scopedDevice = devices.find((device) => device.id === assignmentDeviceId);
  const candidates = assignmentDevices();
  const previousTarget = targetVersion.value;
  const options = [new Option("请选择版本", "")];
  if (candidates.some((device) => device.target_version !== null)) {
    options.push(new Option("取消现有分配", cancelVersion));
  }
  for (const release of releases) {
    let reason = "";
    if (scopedDevice) reason = releaseBlock(scopedDevice, release);
    else if (release.paused) reason = "已暂停";
    else if (!candidates.some((device) => !releaseBlock(device, release))) reason = "没有可分配的设备";
    const option = new Option(reason ? release.version + " · " + reason : release.version, release.version);
    option.disabled = Boolean(reason) || !candidates.length;
    options.push(option);
  }
  element("assignment-label").textContent = scopedDevice
    ? "为「" + scopedDevice.name + "」选择版本" : "为多台设备选择版本";
  targetVersion.replaceChildren(...options);
  targetVersion.value = options.some((option) => option.value === previousTarget && !option.disabled) ? previousTarget : "";
}

function syncSelection() {
  const version = targetVersion.value === cancelVersion ? null : targetVersion.value;
  const release = releases.find((candidate) => candidate.version === version);
  const deviceBlocks = new Map(assignmentDevices().map((device) => [
    device.id,
    version === null ? (device.target_version === null ? "当前没有分配" : "")
      : release ? releaseBlock(device, release) : "请选择版本",
  ]));
  const eligible = new Set([...deviceBlocks].filter(([, reason]) => !reason).map(([id]) => id));
  for (const id of selectedDevices) {
    if (!eligible.has(id)) selectedDevices.delete(id);
  }
  if (assignmentDeviceId && eligible.has(assignmentDeviceId)) selectedDevices.add(assignmentDeviceId);
  const bulkMode = !assignment.hidden && !assignmentDeviceId;
  for (const input of element("devices").querySelectorAll("input[type='checkbox']")) {
    const selected = selectedDevices.has(input.value);
    const reason = deviceBlocks.get(input.value) || "";
    const description = element("assignment-reason-" + input.value);
    input.closest("label").hidden = !bulkMode;
    input.checked = selected;
    input.disabled = !eligible.has(input.value) || (!selected && selectedDevices.size >= selectionLimit);
    description.textContent = reason;
    description.hidden = !(bulkMode && version !== "" && reason);
  }
  beginAssignment.setAttribute("aria-expanded", String(bulkMode));
  for (const button of element("devices").querySelectorAll("[data-assignment-device]")) {
    button.setAttribute("aria-expanded", String(!assignment.hidden && assignmentDeviceId === button.dataset.assignmentDevice));
  }
  applyAssignment.disabled = !selectedDevices.size;
  applyAssignment.textContent = version === null ? "取消分配" : "应用分配";
  let message = "";
  if (assignmentDeviceId) {
    if (!deviceBlocks.has(assignmentDeviceId)) message = "这台设备已不可用，请刷新后重试。";
    else if (!targetVersion.value) {
      const hasChoice = Array.from(targetVersion.options).some((option) => option.value && !option.disabled);
      message = hasChoice ? "选择后确认分配。" : "这台设备暂无可分配的版本。";
    }
  } else if (!targetVersion.value) message = "选择版本后勾选设备。";
  else if (!eligible.size) message = "没有可应用此分配的设备。";
  else if (selectedDevices.size === selectionLimit) message = "已选 50 台设备，已达单次上限。";
  else if (selectedDevices.size) message = "已选择 " + selectedDevices.size + " 台设备。";
  else message = "勾选要应用此分配的设备。";
  const description = element("selection-status");
  description.textContent = message;
  description.hidden = !message;
}

function showAssignment(show, deviceId = null) {
  if (show && !machineForm.hidden) showMachineForm(false);
  assignment.hidden = !show;
  assignmentDeviceId = show ? deviceId : null;
  targetVersion.value = "";
  selectedDevices.clear();
  renderAssignmentChoices();
  syncSelection();
}

function restoreAssignmentFocus(deviceId) {
  const trigger = deviceId ? element("assign-device-" + deviceId) : beginAssignment;
  (trigger && !trigger.disabled ? trigger : element("refresh")).focus();
}

function makeDevice(device) {
  const row = node("li");
  const head = node("div", "", "device-header");
  const status = device.active ? device.status : "disabled";
  const waitingForMachine = waitingForReport(device);
  const name = node("div", "", "device-name");
  if (device.active) {
    const check = document.createElement("input");
    check.type = "checkbox";
    check.value = device.id;
    check.setAttribute("aria-label", "选择设备 " + device.name);
    check.setAttribute("aria-describedby", "assignment-reason-" + device.id);
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
    fact("当前版本", waitingForMachine ? "待设备上报" : device.current_version || "—", waitingForMachine ? "" : "version"),
    fact("分配版本", device.target_version ?? "未分配灰度版本", device.target_version ? "version" : ""),
    fact("最近检查", waitingForMachine ? "尚未检查" : formattedDate(device.last_seen_at)),
  );
  if (device.machine_code) {
    const code = fact("机器码末尾", device.machine_code.slice(-10), "version");
    code.querySelector("dd").title = device.machine_code;
    facts.append(code);
  }
  row.append(head, facts);
  if (device.active) {
    const reason = node("p", "", "muted");
    reason.id = "assignment-reason-" + device.id;
    reason.hidden = true;
    row.append(reason);
    const assign = action(device.target_version ? "更改分配" : "分配版本", "secondary", () => {
      if (busy) return;
      showAssignment(assignment.hidden || assignmentDeviceId !== device.id, device.id);
      if (!assignment.hidden) targetVersion.focus();
    });
    assign.id = "assign-device-" + device.id;
    assign.dataset.assignmentDevice = device.id;
    assign.setAttribute("aria-label", assign.textContent + " " + device.name);
    assign.setAttribute("aria-controls", "assignment");
    const remove = action("撤销设备", "danger", () => {
      if (busy || !window.confirm("撤销「" + device.name + "」的灰度资格？它将停止接收灰度更新，已安装的版本会保留。")) return;
      void run(remove, async () => {
        await request("/devices/" + encodeURIComponent(device.id), "DELETE");
        await refreshAfterChange("设备的灰度资格已撤销。", null, element("refresh"));
      }, "无法撤销设备，请稍后再试。");
    });
    remove.setAttribute("aria-label", "撤销设备 " + device.name);
    controls.append(assign, remove);
  }
  return row;
}

function makeRelease(release) {
  const row = node("li", "", "release-row");
  const heading = node("div");
  heading.append(node("h3", release.version, "version"),
    node("p", release.platforms.map((platform) => platformNames[platform] ?? platform).join("、") +
      " · " + formattedDate(release.created_at), "muted"));
  const progress = assignmentProgress(release.version);
  if (progress.total) {
    heading.append(node("p", [
      "已分配 " + progress.total + " 台",
      "尚未上报 " + progress.unreported + " 台",
      "待升级 " + progress.pending + " 台",
      "已达到目标 " + progress.ready + " 台",
    ].join(" · "), "muted"));
  }
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
  renderMachineChoices();
  renderAssignmentChoices();
  beginAssignment.disabled = !devices.some((device) => device.active);
  const orderedDevices = [...devices].sort((a, b) => Number(b.active) - Number(a.active) || a.name.localeCompare(b.name, "zh-CN"));
  element("devices").replaceChildren(...orderedDevices.map(makeDevice));
  element("releases").replaceChildren(...releases.map(makeRelease));
  const progress = assignmentProgress();
  const overview = element("device-progress");
  overview.hidden = !progress.total;
  overview.replaceChildren(
    fact("已分配", progress.total + " 台"),
    fact("尚未上报", progress.unreported + " 台"),
    fact("待升级", progress.pending + " 台"),
    fact("已达到目标版本", progress.ready + " 台"),
  );
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

beginMachine.addEventListener("click", () => {
  if (busy) return;
  const show = machineForm.hidden;
  if (show) showAssignment(false);
  showMachineForm(show);
  if (show) machineInput.focus();
});

element("cancel-machine").addEventListener("click", () => {
  if (busy) return;
  showMachineForm(false);
  beginMachine.focus();
});

machineInput.addEventListener("input", renderMachineChoices);
machineTarget.addEventListener("change", () => {
  authorizeMachine.disabled = !machineTarget.value;
});

machineForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (machineForm.hidden || authorizeMachine.disabled || !machineTarget.value) return;
  const machine_code = machineCode();
  const version = machineTarget.value;
  await run(authorizeMachine, async () => {
    await request("/machines", "PUT", { machine_code, version });
    showMachineForm(false);
    await refreshAfterChange("已授权机器码末尾 " + machine_code.slice(-10) + " 接收 " + version + "。",
      beginMachine.id, beginMachine);
    beginMachine.focus();
  }, "无法授权这台电脑，请检查机器码和目标版本后重试。");
  renderMachineChoices();
});

beginAssignment.addEventListener("click", () => {
  if (busy) return;
  showAssignment(assignment.hidden || Boolean(assignmentDeviceId));
  if (!assignment.hidden) targetVersion.focus();
});

element("cancel-assignment").addEventListener("click", () => {
  if (busy) return;
  const deviceId = assignmentDeviceId;
  showAssignment(false);
  restoreAssignmentFocus(deviceId);
});

targetVersion.addEventListener("change", syncSelection);

assignment.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!targetVersion.value || !selectedDevices.size) return;
  const version = targetVersion.value === cancelVersion ? null : targetVersion.value;
  const device_ids = Array.from(selectedDevices);
  const deviceId = assignmentDeviceId;
  const device = devices.find((candidate) => candidate.id === deviceId);
  const recipient = device ? "「" + device.name + "」" : device_ids.length + " 台设备";
  await run(applyAssignment, async () => {
    await request("/assignments", "PUT", { device_ids, version });
    showAssignment(false);
    await refreshAfterChange(version === null ? "已取消 " + recipient + " 的版本分配。" : "已为 " + recipient + " 分配 " + version + "。",
      deviceId ? "assign-device-" + deviceId : beginAssignment.id, element("refresh"));
    restoreAssignmentFocus(deviceId);
  }, "无法保存版本分配，请稍后再试。");
  syncSelection();
});

element("enrollment").addEventListener("click", (event) => {
  void run(event.currentTarget, async () => {
    const { code, expires_at } = await request("/enrollments", "POST", {});
    clearCode();
    element("enrollment-details").hidden = false;
    codeInput.value = code;
    element("expiry").textContent = "可用一次，有效期至 " + formattedDate(expires_at);
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

element("copy-invite-instructions").addEventListener("click", async () => {
  const link = element("open-mac-enrollment").getAttribute("href");
  const text = [
    "请在要加入灰度的设备上登记 VortoOne。",
    "Mac 0.3.6 或更新版本可以打开 " + link,
    "如果链接无法点击，请将下面一行复制到 Mac 的「终端」运行。",
    "open '" + link + "'",
    "如果你用的是 0.3.5，请打开「版本管理」登记。支持私密登记的 Windows 或 Ubuntu 版，在「版本管理」里按 Ctrl+Alt+Shift+U。",
    "输入设备名称和我另发的一次性登记码。登记后，请告诉我你填写的设备名称。",
  ].join("\n");
  const fallback = element("invite-instructions");
  try {
    await navigator.clipboard.writeText(text);
    fallback.hidden = true;
    showNotice("已复制登记说明。");
  } catch {
    fallback.value = text;
    fallback.hidden = false;
    fallback.focus();
    fallback.select();
    showNotice("请复制已选中的登记说明。");
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
