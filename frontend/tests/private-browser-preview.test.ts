import assert from "node:assert/strict";
import { test } from "node:test";
import { setTimeout as delay } from "node:timers/promises";
import { type LoginFormOffer, watchControllableBrowser, watchHandoffBrowser, watchPrivateScreen } from "../apps/web/src/screen/private-browser-preview";

class Socket {
  binaryType = "";
  messages: string[] = [];
  closed = false;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;

  send(message: string) { this.messages.push(message); }
  close() { this.closed = true; this.onclose?.(); }
  receive(data: unknown) { this.onmessage?.({ data }); }
}

function preview(origin = "https://home.example:8443") {
  const socket = new Socket();
  const urls: string[] = [];
  const frames: Blob[] = [];
  let ended = 0;
  const stop = watchPrivateScreen({
    origin, orgId: "this-home", machineId: "my-mac",
    onFrame: (frame) => frames.push(frame),
    onEnd: () => { ended++; },
    createSocket(url) { urls.push(url); return socket as unknown as WebSocket; },
  });
  return { socket, urls, frames, stop, ended: () => ended };
}

const ready = '{"type":"ready","min_frame_ms":500}';
const image = new Blob([new Uint8Array(68)]);

test("the viewer pulls one frame at a time on the current authenticated origin and closes without reconnecting", async () => {
  const { socket, frames, urls, stop, ended } = preview();
  try {
    assert.deepEqual(urls, ["wss://home.example:8443/api/app/organizations/this-home/machines/my-mac/browser-preview"]);
    assert.equal(socket.binaryType, "blob");
    assert.deepEqual(socket.messages, []);
    socket.receive(ready);
    assert.deepEqual(socket.messages.map((message) => JSON.parse(message)), [{ type: "next" }]);
    socket.receive(image);
    assert.equal(frames[0]?.type, "image/png");
    assert.equal(socket.messages.length, 1);
    await delay(525);
    assert.equal(socket.messages.length, 2);
    socket.close();
    assert.equal(ended(), 1);
    socket.receive(image);
    assert.equal(frames.length, 1);
    assert.equal(urls.length, 1);
  } finally {
    stop();
  }
});

test("the viewer rejects unsolicited and oversized frames, and only permits plaintext on loopback", () => {
  assert.throws(() => preview("http://home.example"), /secure origin/);
  for (const invalid of [image, new Blob([new Uint8Array(768 * 1024 + 1)])]) {
    const { socket, frames, stop, ended, urls } = preview("http://[::1]:8112");
    try {
      assert.ok(urls[0]?.startsWith("ws://[::1]:8112/"));
      if (invalid !== image) socket.receive(ready);
      socket.receive(invalid);
      assert.equal(frames.length, 0);
      assert.equal(socket.closed, true);
      assert.equal(ended(), 1);
    } finally {
      stop();
    }
  }
});

test("the approval viewer stops on a paused page and only a deliberate restart can open another session", async () => {
  const sockets: Socket[] = [];
  const received: Blob[] = [];
  const ended: string[] = [];
  const open = () => watchHandoffBrowser({
    origin: "https://home.example", orgId: "this-home", approvalId: "approval-1",
    onFrame: (frame) => received.push(frame),
    onEnd: (reason) => ended.push(reason),
    createSocket(url) {
      assert.equal(url, "wss://home.example/api/app/organizations/this-home/approvals/approval-1/browser-preview/watch");
      const socket = new Socket();
      sockets.push(socket);
      return socket as unknown as WebSocket;
    },
  });

  const stop = open();
  const first = sockets[0]!;
  try {
    first.receive(ready);
    first.receive(image);
    await delay(525);
    assert.equal(first.messages.length, 2);
    first.receive('{"type":"paused"}');
    first.receive(image);
    assert.equal(first.closed, true);
    assert.deepEqual(ended, ["paused"]);
    assert.equal(received.length, 1);
    await delay(525);
    assert.equal(sockets.length, 1);
    assert.equal(first.messages.length, 2);
  } finally {
    stop();
  }

  const restart = open();
  const second = sockets[1]!;
  second.receive(ready);
  restart();
  second.receive(image);
  assert.equal(second.closed, true);
  assert.equal(received.length, 1);
  assert.deepEqual(ended, ["paused"]);
});

test("taking control and applying input each requires a fresh frame on that viewer", async () => {
  const socket = new Socket();
  let claimed = 0;
  const ended: string[] = [];
  const watch = watchControllableBrowser({
    origin: "https://home.example", orgId: "this-home", approvalId: "current",
    onFrame() {},
    onControl: () => { claimed++; },
    onFrameReady() {},
    onEnd: (reason) => ended.push(reason),
    createSocket: () => socket as unknown as WebSocket,
  });
  const input = { kind: "click", x: 1638, y: 5560 } as const;
  try {
    socket.receive(ready);
    assert.equal(watch.takeControl(), false);
    socket.receive(image);
    assert.equal(watch.sendInput(input), false);
    assert.equal(watch.takeControl(), true);
    assert.equal(watch.takeControl(), false);
    socket.receive('{"type":"control_ready"}');
    assert.equal(claimed, 1);
    assert.equal(watch.sendInput(input), false);
    await delay(525);
    socket.receive(image);
    assert.equal(watch.sendInput(input), true);
    assert.equal(watch.sendInput(input), false);
    assert.deepEqual(socket.messages.map((message) => JSON.parse(message)), [
      { type: "next" }, { type: "take_control" }, { type: "next" }, { type: "input", action: input },
    ]);
    socket.receive('{"type":"paused"}');
    assert.deepEqual(ended, ["paused"]);
    assert.equal(watch.sendInput(input), false);
  } finally {
    watch.stop();
  }
});

test("a protected login offer waits for control and sends only a sealed box", async () => {
  const socket = new Socket();
  const forms: LoginFormOffer[] = [];
  const ended: string[] = [];
  let waiting = 0;
  const receiver = await crypto.subtle.generateKey({ name: "ECDH", namedCurve: "P-256" }, true, ["deriveBits"]);
  const raw = new Uint8Array(await crypto.subtle.exportKey("raw", receiver.publicKey));
  const offer = {
    type: "login_form", offer: "a".repeat(32), kind: "password", username: true, expires_in: 90,
    public_key: btoa(String.fromCharCode(...raw)),
  };
  const watch = watchControllableBrowser({
    origin: "https://home.example", orgId: "this-home", approvalId: "current",
    onFrame: () => assert.fail("A login form must not be treated as a PNG"),
    onLoginForm: (form) => forms.push(form), onLoginWaiting: () => { waiting++; }, onFrameReady() {}, onControl() {},
    onEnd: (reason) => ended.push(reason), createSocket: () => socket as unknown as WebSocket,
  });
  const site = "https://example.test";
  const fields = { username: "sample@example.test", password: "Synthetic only +%" };
  const code = { code: "05401726" };
  try {
    socket.receive(ready);
    socket.receive(JSON.stringify(offer));
    assert.equal(forms.length, 1);
    assert.equal(await watch.submitLogin(offer.offer, site, fields), false);
    assert.equal(watch.takeControl(), true);
    socket.receive('{"type":"control_ready"}');
    await delay(525);
    const fresh = { ...offer, offer: "b".repeat(32) };
    socket.receive(JSON.stringify(fresh));
    assert.equal(watch.sendInput({ kind: "text", text: "ordinary input" }), false);
    assert.equal(await watch.submitLogin(offer.offer, site, fields), false);
    assert.equal(await watch.submitLogin(fresh.offer, site, fields), true);
    assert.equal(await watch.submitLogin(fresh.offer, site, fields), false);
    const submission = JSON.parse(socket.messages.at(-1)!);
    assert.deepEqual(Object.keys(submission).sort(), ["box", "offer", "type"]);
    assert.equal(submission.type, "submit_login");
    assert.equal(submission.offer, fresh.offer);
    socket.receive('{"type":"login_submitted"}');
    assert.equal(waiting, 1);
    await delay(525);
    socket.receive('{"type":"login_waiting"}');
    assert.equal(waiting, 2);
    await delay(525);
    const verification = { ...offer, offer: "c".repeat(32), kind: "code", username: false };
    socket.receive(JSON.stringify(verification));
    assert.equal(forms.at(-1)?.kind, "code");
    assert.equal(await watch.submitLogin(fresh.offer, site, code), false);
    assert.equal(await watch.submitLogin(verification.offer, site, { password: code.code }), false);
    assert.equal(await watch.submitLogin(verification.offer, site, { code: "12AB" }), false);
    assert.equal(await watch.submitLogin(verification.offer, site, code), true);
    const submittedCode = JSON.parse(socket.messages.at(-1)!);
    assert.deepEqual(Object.keys(submittedCode).sort(), ["box", "offer", "type"]);
    assert.equal(submittedCode.offer, verification.offer);
    assert.equal(socket.messages.some((message) => [fields.password, fields.username, code.code].some(value => message.includes(value))), false);
    socket.receive('{"type":"login_submitted"}');
    await delay(525);
    socket.receive('{"type":"login_ready"}');
    assert.deepEqual(ended, ["login_ready"]);
    assert.equal(socket.closed, true);
  } finally {
    watch.stop();
  }
});
