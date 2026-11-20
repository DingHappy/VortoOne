import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

class Element {
  constructor(tag = "div") { this.tag = tag; this.children = []; this.listeners = {}; this.value = ""; this.textContent = ""; this.hidden = false; this.disabled = false; }
  set innerHTML(_) { throw new Error("untrusted UI text must not become HTML"); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  addEventListener(event, callback) { this.listeners[event] = callback; }
  focus() {}
  async fire(event) { await this.listeners[event]?.({ preventDefault() {} }); }
}
const HOME = "11111111111141118111111111111111";
async function page(handler) {
  const source = await readFile(new URL("./computers.js", import.meta.url), "utf8");
  const elements = new Map();
  const element = (id) => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  const calls = [];
  const timers = [];
  const context = vm.createContext({
    document: { getElementById: element, createElement: (tag) => new Element(tag) },
    window: { __TAURI_INTERNALS__: { invoke: async (name, args) => { calls.push({ name, args }); return handler(name, args, element); } } },
    setInterval: (callback) => timers.push(callback),
  });
  await vm.runInContext(`(async () => { ${source}\n})()`, context);
  return { element, calls, timers };
}
const text = (element) => [element.textContent, ...element.children.map(text)].join(" ");

test("folder grants are scoped to the selected registration and let the native picker choose the path", async () => {
  const id = "a".repeat(32);
  let grants = [];
  const ui = await page(async (name, args) => {
    if (name === "computers_get") return { computers: [{ registrationId: HOME,
      homeInstanceId: HOME, name: "This Mac", origin: "https://home.example",
      state: "online", secretStorage: "keychain", enabled: true }] };
    assert.equal(name, "computer_folder_settings");
    assert.equal(args.registrationId, HOME);
    assert.equal(args.directory, undefined);
    if (args.operation === "grant") {
      assert.equal(args.read, true);
      assert.equal(args.write, false);
      grants = [{ folder_id: id, alias: "Docs", directory: "/selected/Docs", read: true, write: false }];
    }
    if (args.operation === "revoke") {
      assert.equal(args.folderId, id);
      grants = [];
    }
    return { folders: grants };
  });
  await ui.element("connections").children[0].children.at(-2).fire("click");
  assert.equal(ui.element("folder-panel").hidden, false);
  await ui.element("folder-choose").fire("click");
  assert.match(text(ui.element("folder-list")), /Docs · 读取/);
  assert.match(text(ui.element("folder-list")), /\/selected\/Docs/);
  await ui.element("folder-list").children[0].children.at(-1).fire("click");
  assert.match(text(ui.element("folder-list")), /还没有开放的文件夹/);
});

test("pairing binds the preview, clears the code and never supplies a replacement origin", async () => {
  const ui = await page(async (name, args, element) => {
    if (name === "computers_get") return [];
    if (name === "computer_pair_preview") return { previewId: "native-preview", origin: "https://home.example", homeInstanceId: HOME };
    if (name === "computer_pair_confirm") {
      assert.equal(element("code").value, "");
      assert.equal(args.previewId, "native-preview");
      assert.equal(args.code, "ABCD EFGH 2345");
      assert.equal(args.name, "My Mac");
      assert.equal(args.origin, undefined);
      return;
    }
    throw new Error("unexpected host command");
  });
  ui.element("origin").value = "https://home.example";
  ui.element("name").value = "My Mac";
  await ui.element("preview-form").fire("submit");
  assert.equal(ui.element("verified-home").textContent, HOME);
  assert.equal(ui.element("pair-form").hidden, false);
  ui.element("origin").value = "https://different.example";
  ui.element("name").value = "Changed after preview";
  ui.element("code").value = "ABCD EFGH 2345";
  await ui.element("pair-form").fire("submit");
  assert.equal(ui.element("code").value, "");
  assert.equal(ui.element("pair-form").hidden, true);
  assert.match(ui.element("status").textContent, /可为这个家设置本机文件夹授权/);
  assert.equal(ui.calls.filter((call) => call.name === "computer_pair_confirm").length, 1);
});

test("connection cards use inert text, show file degradation, and remove only the selected registration", async () => {
  let removed = false;
  const ui = await page(async (name, args) => {
    if (name === "computers_get") return removed ? [] : [{ registrationId: HOME, homeInstanceId: HOME, name: "<img src=x onerror=alert(1)>", origin: "https://home.example", state: "online", secretStorage: "private_file", capabilitiesReady: false }];
    if (name === "computer_remove") { assert.equal(args.registrationId, HOME); removed = true; return; }
    throw new Error("unexpected host command");
  });
  const card = ui.element("connections").children[0];
  assert.equal(card.children[0].textContent, "<img src=x onerror=alert(1)>");
  assert.match(text(card), /全能力未就绪/);
  assert.match(text(card), /降级存储/);
  await card.children.at(-1).fire("click");
  assert.equal(removed, true);
  assert.match(text(ui.element("connections")), /尚未向任何家登记/);
});

test("declined native confirmation clears the consumed preview and sensitive input", async () => {
  const ui = await page(async (name) => {
    if (name === "computers_get") return [];
    if (name === "computer_pair_preview") return { previewId: "one-shot-preview", origin: "https://home.example", homeInstanceId: HOME };
    if (name === "computer_pair_confirm") throw "已取消配对，请重新核对后再试";
    throw new Error("unexpected host command");
  });
  ui.element("origin").value = "https://home.example";
  ui.element("name").value = "Mac";
  await ui.element("preview-form").fire("submit");
  ui.element("code").value = "ABCD EFGH 2345";
  await ui.element("pair-form").fire("submit");
  assert.equal(ui.element("code").value, "");
  assert.equal(ui.element("preview-form").hidden, false);
  assert.match(ui.element("status").textContent, /已取消/);
  await ui.element("pair-form").fire("submit");
  assert.equal(ui.calls.filter((call) => call.name === "computer_pair_confirm").length, 1);
});

test("automatic pairing failure is visible without implying an enabled connection", async () => {
  const ui = await page(async (name) => {
    if (name === "computers_get") return { computers: [], autoPairing: { state: "secret_storage", message: "系统秘密存储不可用，本机自动配对未启用；请打开本机设置，并在家中移除本次登记后手工配对。" } };
    throw new Error("unexpected host command");
  });
  assert.match(ui.element("auto-status").textContent, /自动配对未启用/);
  assert.match(text(ui.element("connections")), /尚未向任何家登记/);
  assert.equal(ui.calls.filter(({ name }) => name === "computers_get").length, 1);
});

test("the computer card starts and stops preview through native commands for its registration", async () => {
  let browserShare = "idle";
  const ui = await page(async (name, args) => {
    if (name === "computers_get") return { computers: [{ registrationId: HOME,
      homeInstanceId: HOME, name: "This Mac", origin: "https://home.example",
      state: "online", secretStorage: "keychain", enabled: true, browserShare }] };
    assert.equal(name, "computer_browser_share");
    assert.deepEqual({ ...args }, { registrationId: HOME, share: browserShare === "idle" });
    browserShare = args.share ? "sharing" : "idle";
  });
  const card = () => ui.element("connections").children[0];
  const button = (label) => card().children.find((child) => child.tag === "button" && child.textContent === label);
  await button("打开并分享专用浏览器").fire("click");
  assert.match(text(card()), /专用浏览器正在分享/);
  await button("停止分享").fire("click");
  assert.ok(button("打开并分享专用浏览器"));
});

test("the task browser uses native consent and renders the saved setting after a decline", async () => {
  let enabled = false;
  let declined = true;
  const ui = await page(async (name, args) => {
    if (name === "computers_get") return [];
    if (name === "task_browser_enabled") return enabled;
    assert.equal(name, "task_browser_set");
    assert.equal(args.enabled, !enabled);
    if (declined) throw "已取消启用";
    enabled = args.enabled;
  });
  const toggle = ui.element("task-browser-toggle");
  assert.equal(ui.element("task-browser").hidden, false);
  assert.equal(toggle.textContent, "启用任务浏览器");
  await toggle.fire("click");
  assert.equal(toggle.textContent, "启用任务浏览器");
  assert.equal(ui.element("task-browser-status").textContent, "已取消启用");

  declined = false;
  await toggle.fire("click");
  assert.equal(toggle.textContent, "关闭任务浏览器");
  assert.equal(ui.element("task-browser-status").textContent, "");
  await toggle.fire("click");
  assert.equal(toggle.textContent, "启用任务浏览器");
  assert.equal(enabled, false);
});

test("a task code and local consent are sent to the selected registration and cleared before sharing", async () => {
  const code = "a".repeat(32);
  const consents = [];
  const ui = await page(async (name, args, element) => {
    if (name === "task_browser_enabled") return false;
    if (name === "computers_get") return { computers: [{ registrationId: HOME,
      homeInstanceId: HOME, name: "This Mac", origin: "https://home.example",
      state: "online", secretStorage: "keychain", enabled: true, browserShare: "idle" }] };
    assert.equal(name, "computer_browser_share");
    assert.equal(args.registrationId, HOME);
    assert.equal(args.share, true);
    assert.equal(args.taskCode, code);
    assert.equal(element("paired-task-code").value, "");
    assert.equal(element("paired-task-allow-control").checked, false);
    assert.equal(element("paired-task-allow-login").checked, false);
    assert.equal(element("paired-task-allow-login").disabled, true);
    consents.push([args.allowControl, args.allowLogin]);
  });
  await ui.element("connections").children[0].children.find((child) => child.textContent === "打开任务浏览器").fire("click");
  assert.equal(ui.element("paired-task-panel").hidden, false);
  ui.element("paired-task-code").value = "invalid";
  await ui.element("paired-task-form").fire("submit");
  assert.deepEqual(consents, []);
  ui.element("paired-task-code").value = code.toUpperCase();
  ui.element("paired-task-allow-login").checked = true;
  await ui.element("paired-task-form").fire("submit");
  assert.deepEqual(consents, [[undefined, undefined]]);
  assert.match(ui.element("paired-task-status").textContent, /正在打开专用窗口/);
  ui.element("paired-task-code").value = code;
  ui.element("paired-task-allow-control").checked = true;
  await ui.element("paired-task-form").fire("submit");
  assert.deepEqual(consents, [[undefined, undefined], [true, undefined]]);
  ui.element("paired-task-code").value = code;
  const controls = ui.element("paired-task-allow-control");
  controls.checked = true;
  await controls.fire("change");
  assert.equal(ui.element("paired-task-allow-login").disabled, false);
  ui.element("paired-task-allow-login").checked = true;
  await ui.element("paired-task-form").fire("submit");
  assert.deepEqual(consents, [[undefined, undefined], [true, undefined], [true, true]]);
});
