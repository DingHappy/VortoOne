const element = (id) => document.getElementById(id);
const invoke = (command, args) => window.__TAURI_INTERNALS__.invoke(command, args);
const status = element("status");
const save = element("save");
const address = element("base-url");
const clearKey = element("clear-key");
const keyState = element("key-state");
function keyOrigin(value) {
  const base = value.trim();
  const match = /^https?:\/\/([^/?#]+)(?:\/|$)/i.exec(base);
  if (!match || /[@%]|:$/.test(match[1]) || /[\s\x00-\x1f\x7f\\]|%(?![\da-f]{2})/i.test(base)) return null;
  try {
    const url = new URL(base);
    return /[?#]/.test(base) ? null : url.origin;
  } catch {
    return null;
  }
}
try {
  const current = await invoke("model_settings_get");
  address.value = current.baseUrl;
  element("model").value = current.model;
  const originalOrigin = keyOrigin(current.baseUrl);
  const updateKeyState = () => {
    const canReuse = current.hasKey && originalOrigin !== null && originalOrigin === keyOrigin(address.value);
    clearKey.disabled = !canReuse;
    if (!canReuse) clearKey.checked = false;
    keyState.textContent = canReuse ? "已保存此服务的 API Key；留空会继续使用。" : current.hasKey
      ? "当前地址不会沿用原来的 API Key。若此服务需要密钥，请填写。" : "尚未保存 API Key。";
  };
  address.addEventListener("input", updateKeyState);
  updateKeyState();
  save.disabled = false;
} catch {
  status.textContent = "模型配置暂时无法打开，请关闭窗口后重试。";
}
element("model-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  save.disabled = true;
  status.textContent = "正在保存模型配置…";
  try {
    await invoke("model_settings_save", {input: {
      baseUrl: address.value.trim(), model: element("model").value.trim(),
      apiKey: element("api-key").value, clearKey: clearKey.checked,
    }});
  } catch (error) {
    status.textContent = typeof error === "string" ? error : "保存失败，请重试。";
    save.disabled = false;
  }
});
