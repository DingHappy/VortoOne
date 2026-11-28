const EVENT = "vortoone-update-progress";
const phases = ["downloading", "verifying", "installing", "restarting"];
const stageCopy = {
  downloading: ["正在下载更新", "正在连接更新服务…"],
  verifying: ["正在验证更新", "正在检查安装包和版本签名…"],
  installing: ["正在安装更新", "安装完成后会自动重启 VortoOne。"],
  restarting: ["正在重启 VortoOne", "正在打开新版本…"],
};
const amount = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 1 });

function formatBytes(bytes) {
  if (bytes < 1000) return `${bytes} B`;
  if (bytes < 1_000_000) return `${amount.format(bytes / 1000)} KB`;
  if (bytes < 1_000_000_000) return `${amount.format(bytes / 1_000_000)} MB`;
  return `${amount.format(bytes / 1_000_000_000)} GB`;
}

export function progressView(snapshot) {
  const step = phases.indexOf(snapshot?.phase);
  if (step === -1 ||
      typeof snapshot.version !== "string" || !snapshot.version ||
      !Number.isSafeInteger(snapshot.received) || snapshot.received < 0) return null;
  const received = snapshot.received;
  const total = Number.isSafeInteger(snapshot.total) && snapshot.total > 0 && snapshot.total >= received
    ? snapshot.total : null;
  const [title, message] = stageCopy[snapshot.phase];
  const view = {
    version: snapshot.version, phase: snapshot.phase, step, received, total, title,
    detail: message, valueText: message, percent: null,
  };
  if (snapshot.phase !== "downloading") return view;
  if (total !== null) {
    const receivedLabel = formatBytes(received);
    const totalLabel = formatBytes(total);
    view.percent = Math.floor(received / total * 100);
    view.detail = `${receivedLabel} / ${totalLabel}`;
    view.valueText = `已下载 ${receivedLabel}，共 ${totalLabel}`;
  } else if (received > 0) {
    view.detail = view.valueText = `已下载 ${formatBytes(received)}`;
  }
  return view;
}

function advances(current, next) {
  if (!current) return true;
  if (next.version !== current.version || next.step < current.step || next.received < current.received) return false;
  if (next.step > current.step || next.received > current.received) return true;
  return current.total === null || next.total !== null;
}

export function mountProgress(document, source) {
  const version = document.getElementById("update-version");
  const phase = document.getElementById("update-phase");
  const meter = document.getElementById("update-meter");
  const fill = document.getElementById("update-fill");
  const detail = document.getElementById("update-detail");
  const percent = document.getElementById("update-percent");
  const steps = [...document.querySelectorAll("[data-phase]")];
  let current;

  function render(snapshot) {
    const next = progressView(snapshot);
    if (!next || !advances(current, next)) return false;

    if (!current) version.textContent = `VortoOne ${next.version}`;
    if (!current || next.step !== current.step) {
      phase.textContent = next.title;
      for (const [index, step] of steps.entries()) {
        if (index === next.step) step.setAttribute("aria-current", "step");
        else step.removeAttribute("aria-current");
        step.classList.toggle("complete", index < next.step);
      }
    }
    current = next;

    detail.textContent = next.detail;
    meter.setAttribute("aria-valuetext", next.valueText);
    const determinate = next.percent !== null;
    meter.classList.toggle("indeterminate", !determinate);
    percent.hidden = !determinate;
    if (determinate) {
      meter.removeAttribute("aria-hidden");
      meter.setAttribute("aria-valuemin", "0");
      meter.setAttribute("aria-valuemax", "100");
      meter.setAttribute("aria-valuenow", String(next.percent));
      percent.textContent = `${next.percent}%`;
      fill.style.width = `${next.received / next.total * 100}%`;
    } else {
      // WebKit otherwise exposes a numeric default for an indeterminate ARIA meter.
      meter.setAttribute("aria-hidden", "true");
      for (const property of ["aria-valuemin", "aria-valuemax", "aria-valuenow"]) meter.removeAttribute(property);
      fill.style.removeProperty("width");
    }
    return true;
  }

  const snapshot = () => render(source.__vortooneUpdateProgress);
  source.addEventListener(EVENT, snapshot);
  snapshot();
  return render;
}

if (typeof window !== "undefined") mountProgress(document, window);
