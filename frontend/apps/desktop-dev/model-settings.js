const element = (id) => document.getElementById(id);
const invoke = (command, args) => window.__TAURI_INTERNALS__.invoke(command, args);
const presets = [
  { id: "openai", group: "cloud", label: "OpenAI", base: "https://api.openai.com/v1", aliases: "OpenAI API platform.openai.com GPT" },
  { id: "deepseek", group: "cloud", label: "DeepSeek", base: "https://api.deepseek.com/v1", aliases: "深度求索 platform.deepseek.com" },
  { id: "xiaomi-mimo", group: "cloud", label: "小米 MiMo", base: "https://api.xiaomimimo.com/v1", aliases: "Xiaomi MiMo 米莫 platform.xiaomimimo.com" },
  { id: "ollama", group: "local", label: "Ollama", base: "http://127.0.0.1:11434/v1", aliases: "ollama.com localhost" },
  { id: "lm-studio", group: "local", label: "LM Studio", base: "http://127.0.0.1:1234/v1", aliases: "lmstudio.ai localhost" },
  { id: "omlx", group: "local", label: "oMLX", base: "http://127.0.0.1:8000/v1", aliases: "MLX 苹果芯片 localhost" },
  { id: "custom", group: "custom", label: "自定义服务", base: "" },
];
const profileLimit = 20;

const form = element("model-form");
const formFooter = form.querySelector("footer");
const fields = { baseUrl: element("base-url"), model: element("model"), apiKey: element("api-key") };
const clearKey = element("clear-key");
const keyState = element("key-state");
const providers = element("providers");
const providerSearch = element("provider-search");
const serviceFilters = [...providers.querySelectorAll("[data-service-filter]")];
const providerButtons = new Map();
const serviceFields = element("model-fields");
const serviceTitle = element("selected-service-title");
const fetchModels = element("fetch-models");
const modelSelect = element("model-select");
const modelEntryMode = element("model-entry-mode");
const modelListStatus = element("model-list-status");
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
let browsing = false;
let serviceFilter = "all";

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
  const profile = { id, baseUrl: "", model: "", apiKey: "", clearKey: false, preset: "", manualModel: true };
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

function normalizedSearch(value) {
  return value.normalize("NFKC").toLowerCase().replace(/\s+/g, "");
}

function renderCatalog(profile) {
  const query = normalizedSearch(providerSearch.value);
  const visibleGroups = new Set();
  for (const preset of presets) {
    const isCustom = preset.group === "custom";
    const match = isCustom
      || ((serviceFilter === "all" || preset.group === serviceFilter)
        && [preset.id, preset.label, preset.base, preset.aliases].some((part) => normalizedSearch(part).includes(query)));
    const button = providerButtons.get(preset.id);
    button.hidden = !match;
    if (profile.preset === preset.id) button.setAttribute("aria-current", "true");
    else button.removeAttribute("aria-current");
    if (match && !isCustom) visibleGroups.add(preset.group);
  }
  element("cloud-group").hidden = !visibleGroups.has("cloud");
  element("local-group").hidden = !visibleGroups.has("local");
  element("provider-empty").hidden = visibleGroups.size !== 0;
  for (const button of serviceFilters) {
    button.setAttribute("aria-pressed", String(serviceFilter === button.dataset.serviceFilter));
  }
}

function modelOptions(profile) {
  return profile.lookup?.models ?? [];
}

function resetLookup(profile) {
  profile.lookup = undefined;
  profile.manualModel = true;
}

function renderModelPicker(profile) {
  const models = modelOptions(profile);
  const showSelect = models.length > 0 && !profile.manualModel;
  element("manual-model-field").hidden = showSelect;
  element("model-select-field").hidden = !showSelect;
  modelEntryMode.hidden = models.length === 0;
  modelEntryMode.textContent = showSelect ? "手动填写模型 ID" : "从列表选模型";
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "请选择模型";
  modelSelect.replaceChildren(placeholder);
  if (showSelect) {
    for (const id of models) {
      const option = document.createElement("option");
      option.value = id;
      option.textContent = id;
      modelSelect.append(option);
    }
  }
  modelSelect.value = models.includes(profile.model.trim()) ? profile.model.trim() : "";
  const lookup = profile.lookup;
  const pending = lookup?.state === "loading";
  fetchModels.disabled = saving;
  fetchModels.setAttribute("aria-disabled", String(saving || pending));
  fetchModels.textContent = pending ? "正在获取…" : "获取模型";
  modelListStatus.dataset.error = String(lookup?.state === "error");
  const message = lookup?.message ?? "可以获取服务的模型列表，也可以直接填写模型 ID。";
  if (modelListStatus.textContent !== message) modelListStatus.textContent = message;
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
  providers.hidden = !browsing;
  providers.disabled = saving;
  serviceFields.hidden = browsing;
  serviceFields.disabled = saving;
  serviceTitle.textContent = presets.find((preset) => preset.id === profile.preset)?.label ?? "";
  element("back-to-service").hidden = !profile.preset;
  formFooter.hidden = browsing;
  element("model-actions").hidden = profile.id === defaultId;
  setDefault.hidden = browsing;
  setDefault.disabled = saving || browsing;
  remove.textContent = profile.preset ? "移除模型" : "取消添加";
  remove.disabled = saving;
  const atLimit = profiles.size >= profileLimit;
  add.disabled = saving || browsing || atLimit;
  add.textContent = atLimit ? `最多可配置 ${profileLimit} 个模型` : "添加另一个模型";
  save.disabled = saving || browsing;
  renderCatalog(profile);
  renderModelPicker(profile);
  updateKeyField(profile);
}

function selectProfile(id) {
  const previous = profiles.get(selectedId);
  if (selectedId !== id && previous?.lookup?.state === "loading") resetLookup(previous);
  selectedId = id;
  const profile = profiles.get(id);
  browsing = !profile.preset;
  for (const [key, input] of Object.entries(fields)) {
    input.value = profile[key];
    input.removeAttribute("aria-invalid");
  }
  modelSelect.removeAttribute("aria-invalid");
  writeStatus();
  renderProfiles();
  updateControls(profile);
}

function browseProviders() {
  const profile = profiles.get(selectedId);
  if (profile.lookup?.state === "loading") resetLookup(profile);
  browsing = true;
  writeStatus();
  updateControls(profile);
  providerSearch.focus();
}

function changeAddress(profile, next) {
  const currentOrigin = keyOrigin(profile.baseUrl);
  const changedOrigin = !currentOrigin || currentOrigin !== keyOrigin(next);
  if (changedOrigin) {
    profile.apiKey = "";
    fields.apiKey.value = "";
    profile.clearKey = false;
  }
  if (profile.baseUrl.trim() !== next.trim()) resetLookup(profile);
  profile.baseUrl = next;
  return changedOrigin;
}

function connectionIssue(profile) {
  if (!keyOrigin(profile.baseUrl)) return [fields.baseUrl, "请填写完整的 HTTP 或 HTTPS 地址，地址不能包含账号、密码、查询参数或片段。"];
  if (!keylessLocal(profile.baseUrl) && !canKeepKey(profile) && !profile.apiKey.trim()) {
    return [fields.apiKey, "请填写此模型的 API Key。"];
  }
  return null;
}

function issue(profile) {
  if (!profile.preset) return [null, "请选择服务。"];
  const problem = connectionIssue(profile);
  if (problem) return problem;
  const model = profile.model.trim();
  if (!profile.manualModel && !modelOptions(profile).includes(model)) return [modelSelect, "请从服务返回的列表中选择模型。"];
  if (!model) return [fields.model, "请填写模型服务提供的模型 ID。"];
  return null;
}

function showIssue([field, message]) {
  writeStatus(message, true);
  if (!field) {
    providerSearch.focus();
    return;
  }
  field.setAttribute("aria-invalid", "true");
  field.focus();
}

function validate(profile) {
  const problem = issue(profile);
  if (!problem) return true;
  if (profile.id !== selectedId || (browsing && profile.preset)) selectProfile(profile.id);
  showIssue(problem);
  return false;
}

for (const preset of presets) {
  const button = document.createElement("button");
  const text = document.createElement("span");
  const hint = document.createElement("small");
  button.type = "button";
  button.className = "preset";
  button.dataset.preset = preset.id;
  text.textContent = preset.label;
  hint.id = `preset-info-${preset.id}`;
  hint.textContent = preset.base ? new URL(preset.base).host : "手动填写兼容 OpenAI 的地址";
  button.setAttribute("aria-label", preset.label);
  button.setAttribute("aria-describedby", hint.id);
  button.addEventListener("click", () => {
    const profile = profiles.get(selectedId);
    if (preset.id !== profile.preset) {
      if (changeAddress(profile, preset.base)) profile.model = "";
      profile.preset = preset.id;
    }
    selectProfile(profile.id);
    serviceTitle.focus();
  });
  button.append(text, hint);
  providerButtons.set(preset.id, button);
  element(`${preset.group}-presets`).append(button);
}

providerSearch.addEventListener("input", () => renderCatalog(profiles.get(selectedId)));
providerSearch.addEventListener("keydown", (event) => {
  if (event.key === "Enter") event.preventDefault();
});
for (const button of serviceFilters) {
  button.addEventListener("click", () => {
    serviceFilter = button.dataset.serviceFilter;
    renderCatalog(profiles.get(selectedId));
  });
}
element("change-service").addEventListener("click", browseProviders);
element("back-to-service").addEventListener("click", () => {
  selectProfile(selectedId);
  element("change-service").focus();
});

fields.baseUrl.addEventListener("input", () => {
  const profile = profiles.get(selectedId);
  const hadTemporaryKey = !!profile.apiKey;
  const changedOrigin = changeAddress(profile, fields.baseUrl.value);
  fields.baseUrl.removeAttribute("aria-invalid");
  writeStatus(hadTemporaryKey && changedOrigin ? "服务地址已变更，请重新填写 API Key。" : "");
  renderProfiles();
  renderModelPicker(profile);
  updateKeyField(profile);
});

function changeModel(value) {
  profiles.get(selectedId).model = value;
  fields.model.value = value;
  fields.model.removeAttribute("aria-invalid");
  modelSelect.removeAttribute("aria-invalid");
  writeStatus();
  renderProfiles();
}

fields.model.addEventListener("input", () => changeModel(fields.model.value));
modelSelect.addEventListener("change", () => changeModel(modelSelect.value));
modelEntryMode.addEventListener("click", () => {
  const profile = profiles.get(selectedId);
  profile.manualModel = !profile.manualModel;
  fields.model.removeAttribute("aria-invalid");
  modelSelect.removeAttribute("aria-invalid");
  writeStatus();
  renderModelPicker(profile);
  (profile.manualModel ? fields.model : modelSelect).focus();
});
fields.apiKey.addEventListener("input", () => {
  const profile = profiles.get(selectedId);
  profile.apiKey = fields.apiKey.value;
  resetLookup(profile);
  renderModelPicker(profile);
  fields.apiKey.removeAttribute("aria-invalid");
  writeStatus();
});
clearKey.addEventListener("change", () => {
  const profile = profiles.get(selectedId);
  profile.clearKey = clearKey.checked;
  profile.apiKey = "";
  fields.apiKey.value = "";
  resetLookup(profile);
  renderModelPicker(profile);
  updateKeyField(profile);
});

fetchModels.addEventListener("click", async () => {
  const profile = profiles.get(selectedId);
  if (saving || browsing || profile.lookup?.state === "loading") return;
  const problem = connectionIssue(profile);
  if (problem) {
    showIssue(problem);
    return;
  }
  writeStatus();
  const pending = { state: "loading", message: "正在读取服务的模型列表…" };
  profile.lookup = pending;
  profile.manualModel = true;
  renderModelPicker(profile);
  let lookup;
  try {
    const { models } = await invoke("model_settings_list_models", { input: {
      id: profile.id, baseUrl: profile.baseUrl.trim(), apiKey: profile.apiKey, clearKey: profile.clearKey,
    } });
    lookup = {
      state: "ready", models,
      message: models.length ? `已获取 ${models.length} 个模型。` : "服务没有返回模型。你可以手动填写模型 ID。",
    };
  } catch (error) {
    lookup = {
      state: "error",
      message: typeof error === "string" ? error : "无法获取模型列表。你可以重试或手动填写模型 ID。",
    };
  }
  if (selectedId !== profile.id || profile.lookup !== pending) return;
  profile.lookup = lookup;
  const model = profile.model.trim();
  profile.manualModel = !lookup.models?.length || (!!model && !lookup.models.includes(model));
  renderModelPicker(profile);
});

add.addEventListener("click", () => {
  if (validate(profiles.get(selectedId))) {
    providerSearch.value = "";
    serviceFilter = "all";
    selectProfile(newDraft().id);
    providerSearch.focus();
  }
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
  if (savedModels.hidden) element("change-service").focus();
  else profileList.querySelector('[aria-current="true"]')?.focus();
  writeStatus("已移除这条配置，保存后生效。");
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (saving || browsing || ![...profiles.values()].every(validate)) return;
  saving = true;
  const selected = profiles.get(selectedId);
  if (selected.lookup?.state === "loading") resetLookup(selected);
  renderProfiles();
  updateControls(selected);
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
        preset: matchingPreset(baseUrl).id, originalOrigin: hasKey ? keyOrigin(baseUrl) : null, manualModel: true,
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
