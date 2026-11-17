const element = (id) => document.getElementById(id);
const invoke = (command, args) => window.__TAURI_INTERNALS__.invoke(command, args);
const status = element("status");
const save = element("save");
try {
  const current = await invoke("model_settings_get");
  element("base-url").value = current.baseUrl;
  element("model").value = current.model;
  element("key-state").textContent = current.hasKey ? "已保存 API Key；留空会继续使用。" : "尚未保存 API Key。";
  element("clear-key").disabled = !current.hasKey;
  save.disabled = false;
} catch {
  status.textContent = "模型配置暂时无法打开，请关闭窗口后重试。";
}
element("model-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  save.disabled = true;
  status.textContent = "正在保存并连接…";
  try {
    await invoke("model_settings_save", {input: {
      baseUrl: element("base-url").value.trim(), model: element("model").value.trim(),
      apiKey: element("api-key").value, clearKey: element("clear-key").checked,
    }});
  } catch (error) {
    status.textContent = typeof error === "string" ? error : "保存失败，请重试。";
    save.disabled = false;
  }
});
