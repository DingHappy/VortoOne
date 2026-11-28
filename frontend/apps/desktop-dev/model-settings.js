const element = (id) => document.getElementById(id);
const invoke = (command, args) => window.__TAURI_INTERNALS__.invoke(command, args);
const presets = [
  { id: "openai", label: "OpenAI", base: "https://api.openai.com/v1" },
  { id: "deepseek", label: "DeepSeek", base: "https://api.deepseek.com/v1" },
  { id: "ollama", label: "Ollama", base: "http://127.0.0.1:11434/v1" },
  { id: "lm-studio", label: "LM Studio", base: "http://127.0.0.1:1234/v1" },
  { id: "omlx", label: "oMLX", base: "http://127.0.0.1:8000/v1" },
  { id: "custom", label: "自定义", base: "" },
];
const profileLimit = 20;

const form = element("model-form");
const fields = { baseUrl: element("base-url"), model: element("model"), apiKey: element("api-key") };
const clearKey = element("clear-key");
const keyState = element("key-state");
const providers = element("providers");
const presetOptions = element("presets");
const profileList = element("profiles");
const savedModels = element("saved-models");
const status = element("status");
const save = element("save");
const add = element("add");
const setDefault = element("set-default");
const remove = element("remove");
const profiles = new Map();

let selectedId;
let defaultId;
let hadSavedProfiles = false;
let saving = false;

function keyOrigin(value) {
  const base = value.trim();
  const match = /^https?:\/\/([^/?#]+)(?:\/|$)/i.exec(base);
  if (!match || /[@%]|:$/.test(match[1]) || /[\s\x00-\x1f\x7f\\]|%(?![\da-f]{2})/i.test(base)) return null;
  try {
    return /[?#]/.test(base) ? null : new URL(base).origin;
  } catch {
    return null;
  }
}

function keylessLocal(value) {
  const match = /^https?:\/\/(?:localhost|127\.0\.0\.1)(?::([1-9]\d{0,4}))?(?:\/[^%?#]*)?$/i.exec(value.trim());
  return keyOrigin(value) !== null && !!match && (!match[1] || Number(match[1]) <= 65535);
}

function matchingPreset(value) {
  return presets.find(({ base }) => base && base === value.trim().replace(/\/$/, "")) ?? presets.at(-1);
}

function serviceLabel(value) {
  if (!value.trim()) return "";
  const preset = matchingPreset(value);
  return preset.id === "custom" && keyOrigin(value) ? new URL(value.trim()).host : preset.label;
}

function writeStatus(message = "", error = false) {
  status.textContent = message;
  status.dataset.error = String(error);
}

function newDraft() {
  const id = Array.from(crypto.getRandomValues(new Uint8Array(12)), (byte) => byte.toString(16).padStart(2, "0")).join("");
  const profile = { id, baseUrl: "", model: "", apiKey: "", clearKey: false, preset: "" };
  profiles.set(id, profile);
  defaultId ??= id;
  return profile;
}

function canKeepKey(profile) {
  return !!profile.originalOrigin && profile.originalOrigin === keyOrigin(profile.baseUrl);
}

function updateKeyField(profile) {
  const saved = canKeepKey(profile);
  const local = keylessLocal(profile.baseUrl);
  element("clear-key-label").hidden = !saved || !local;
  clearKey.checked = profile.clearKey;
  clearKey.disabled = saving || !saved || !local;
  fields.apiKey.disabled = saving || profile.clearKey;
  fields.apiKey.setAttribute("aria-required", String(!local && !saved));
  fields.apiKey.placeholder = saved ? "留空保留已保存的密钥" : local ? "无需鉴权时可留空" : "填写此服务的 API Key";
  keyState.textContent = profile.clearKey ? "保存时会清除此模型的密钥。"
    : saved ? "此模型已有密钥。填写新密钥可替换。"
    : profile.originalOrigin ? "服务地址已变更。旧密钥不会发送到新地址。"
    : "";
}

function renderProfiles() {
  savedModels.hidden = !hadSavedProfiles && profiles.size === 1;
  profileList.replaceChildren();
  let index = 0;
  for (const profile of profiles.values()) {
    index += 1;
    const row = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "profile";
    button.disabled = saving;
    if (profile.id === selectedId) button.setAttribute("aria-current", "true");
    const title = document.createElement("span");
    title.className = "profile-name";
    title.textContent = [profile.model.trim() || `模型 ${index}`, serviceLabel(profile.baseUrl)].filter(Boolean).join(" · ");
    title.title = title.textContent;
    button.append(title);
    if (profile.id === defaultId) {
      const badge = document.createElement("span");
      badge.className = "profile-default";
      badge.textContent = "默认";
      button.append(badge);
    }
    button.addEventListener("click", () => {
      selectProfile(profile.id);
      profileList.querySelector('[aria-current="true"]')?.focus();
    });
    row.append(button);
    profileList.append(row);
  }
}

function updateControls(profile) {
  const fresh = !profile.preset;
  element("model-fields").hidden = fresh;
  element("model-fields").disabled = saving;
  providers.disabled = saving;
  element("model-actions").hidden = profile.id === defaultId;
  setDefault.disabled = saving || fresh;
  remove.disabled = saving;
  const atLimit = profiles.size >= profileLimit;
  add.disabled = saving || fresh || atLimit;
  add.textContent = atLimit ? `最多可配置 ${profileLimit} 个模型` : "添加另一个模型";
  save.disabled = saving || fresh;
  updateKeyField(profile);
}

function selectProfile(id, focusPreset = false) {
  selectedId = id;
  const profile = profiles.get(id);
  for (const input of presetOptions.querySelectorAll("input")) input.checked = input.value === profile.preset;
  for (const [key, input] of Object.entries(fields)) {
    input.value = profile[key];
    input.removeAttribute("aria-invalid");
  }
  writeStatus();
  renderProfiles();
  updateControls(profile);
  if (focusPreset) presetOptions.querySelector("input").focus();
}

function changeAddress(profile, next) {
  const currentOrigin = keyOrigin(profile.baseUrl);
  const changedOrigin = !currentOrigin || currentOrigin !== keyOrigin(next);
  if (changedOrigin) {
    profile.apiKey = "";
    fields.apiKey.value = "";
    profile.clearKey = false;
  }
  profile.baseUrl = next;
  return changedOrigin;
}

function issue(profile) {
  if (!profile.preset) return [null, "请选择服务。"];
  if (!keyOrigin(profile.baseUrl)) return [fields.baseUrl, "请填写完整的 HTTP 或 HTTPS 地址，地址不能包含账号、密码、查询参数或片段。"];
  if (!profile.model.trim()) return [fields.model, "请填写模型服务提供的模型 ID。"];
  if (!keylessLocal(profile.baseUrl) && !canKeepKey(profile) && !profile.apiKey.trim()) {
    return [fields.apiKey, "请填写此模型的 API Key。"];
  }
  return null;
}

function validate(profile) {
  const problem = issue(profile);
  if (!problem) return true;
  if (profile.id !== selectedId) selectProfile(profile.id);
  const [field, message] = problem;
  writeStatus(message, true);
  if (field) {
    field.setAttribute("aria-invalid", "true");
    field.focus();
  } else {
    presetOptions.querySelector("input").focus();
  }
  return false;
}

for (const preset of presets) {
  const label = document.createElement("label");
  const input = document.createElement("input");
  const text = document.createElement("span");
  label.className = "preset";
  input.type = "radio";
  input.name = "preset";
  input.value = preset.id;
  text.textContent = preset.label;
  input.addEventListener("change", () => {
    const profile = profiles.get(selectedId);
    const next = preset.id === "custom" ? "" : preset.base;
    if (changeAddress(profile, next)) {
      profile.model = "";
      fields.model.value = "";
    }
    profile.preset = preset.id;
    fields.baseUrl.value = next;
    for (const input of Object.values(fields)) input.removeAttribute("aria-invalid");
    writeStatus();
    renderProfiles();
    updateControls(profile);
  });
  label.append(input, text);
  presetOptions.append(label);
}

fields.baseUrl.addEventListener("input", () => {
  const profile = profiles.get(selectedId);
  const hadTemporaryKey = !!profile.apiKey;
  const changedOrigin = changeAddress(profile, fields.baseUrl.value);
  fields.baseUrl.removeAttribute("aria-invalid");
  writeStatus(hadTemporaryKey && changedOrigin ? "服务地址已变更，请重新填写 API Key。" : "");
  renderProfiles();
  updateKeyField(profile);
});
fields.model.addEventListener("input", () => {
  profiles.get(selectedId).model = fields.model.value;
  fields.model.removeAttribute("aria-invalid");
  writeStatus();
  renderProfiles();
});
fields.apiKey.addEventListener("input", () => {
  profiles.get(selectedId).apiKey = fields.apiKey.value;
  fields.apiKey.removeAttribute("aria-invalid");
  writeStatus();
});
clearKey.addEventListener("change", () => {
  const profile = profiles.get(selectedId);
  profile.clearKey = clearKey.checked;
  profile.apiKey = "";
  fields.apiKey.value = "";
  updateKeyField(profile);
});

add.addEventListener("click", () => {
  if (validate(profiles.get(selectedId))) selectProfile(newDraft().id, true);
});
setDefault.addEventListener("click", () => {
  const selected = profiles.get(selectedId);
  if (!validate(selected)) return;
  defaultId = selected.id;
  renderProfiles();
  updateControls(selected);
  profileList.querySelector('[aria-current="true"]')?.focus();
  writeStatus("已选择默认模型，保存后生效。");
});
remove.addEventListener("click", () => {
  if (selectedId === defaultId) return;
  profiles.delete(selectedId);
  selectProfile(defaultId);
  if (savedModels.hidden) presetOptions.querySelector("input:checked")?.focus();
  else profileList.querySelector('[aria-current="true"]')?.focus();
  writeStatus("已移除这条配置，保存后生效。");
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (saving || ![...profiles.values()].every(validate)) return;
  saving = true;
  renderProfiles();
  updateControls(profiles.get(selectedId));
  writeStatus("正在保存模型配置…");
  try {
    await invoke("model_settings_save", { input: {
      profiles: [...profiles.values()].map(({ id, baseUrl, model, apiKey, clearKey }) => ({
        id, baseUrl: baseUrl.trim(), model: model.trim(), apiKey, clearKey,
      })),
      defaultId,
    } });
  } catch (error) {
    saving = false;
    renderProfiles();
    updateControls(profiles.get(selectedId));
    writeStatus(typeof error === "string" ? error : "保存失败，请重试。", true);
    save.focus();
  }
});

async function load() {
  const loading = element("loading");
  const retry = element("retry");
  retry.hidden = true;
  loading.textContent = "正在读取模型配置…";
  try {
    const current = await invoke("model_settings_get");
    hadSavedProfiles = current.profiles.length > 0;
    defaultId = current.defaultId;
    for (const { id, baseUrl, model, hasKey } of current.profiles) {
      profiles.set(id, {
        id, baseUrl, model, apiKey: "", clearKey: false,
        preset: matchingPreset(baseUrl).id, originalOrigin: hasKey ? keyOrigin(baseUrl) : null,
      });
    }
    const first = hadSavedProfiles ? profiles.get(defaultId) : newDraft();
    selectProfile(first.id);
    loading.hidden = true;
    form.hidden = false;
  } catch {
    loading.textContent = "暂时无法读取模型配置。";
    retry.hidden = false;
  }
}

element("retry").addEventListener("click", load);
await load();
