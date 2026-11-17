import assert from "node:assert/strict";
import test from "node:test";
import { connectLink, parseConnectLink } from "../packages/client/src/lib/server";

const SERVER = "https://box.tail1234.ts.net";
const TOKEN = "a".repeat(43);

test("a connect link keeps its one-time sign-in in the fragment and reads back", () => {
  const link = connectLink(SERVER, { token: TOKEN });
  assert.equal(link, `vortoone://connect?server=${encodeURIComponent(SERVER)}#token=${TOKEN}`);
  assert.deepEqual(parseConnectLink(link), { server: SERVER, token: TOKEN });
  assert.equal(connectLink(SERVER), `vortoone://connect?server=${encodeURIComponent(SERVER)}`);
  assert.deepEqual(parseConnectLink(connectLink(SERVER)), { server: SERVER });
});

test("a secret in the query, another scheme, or plain http elsewhere is no link", () => {
  const server = encodeURIComponent(SERVER);
  assert.equal(parseConnectLink(`vortoone://connect?server=${server}&token=${TOKEN}`), null);
  assert.equal(parseConnectLink(`agenteschat://connect?server=${server}`), null);
  assert.equal(parseConnectLink(`vortoone://connect?server=${encodeURIComponent("http://box.example.com")}`), null);
  assert.equal(parseConnectLink(`vortoone://connect?server=${server}#token=short`), null);
  const here = `vortoone://connect?server=${encodeURIComponent("http://127.0.0.1:8080")}`;
  assert.deepEqual(parseConnectLink(here, { allowHttp: "loopback" }), { server: "http://127.0.0.1:8080" });
  assert.equal(parseConnectLink(here), null);
});

test("an app and a server work together while their api ranges overlap", async () => {
  const { apiFits } = await import("../packages/client/src/lib/server");
  // The app updated first (speaks 3, still 2): a server on 2 is fine, one on 1 is too old.
  const app = { api: 3, min: 2 };
  assert.deepEqual(apiFits(2, 1, app), { ok: true });
  assert.deepEqual(apiFits(1, 1, app), { ok: false, problem: "server-too-old" });
  // The server updated first (serves 3, no longer below 3): this app on 2 is too old.
  assert.deepEqual(apiFits(4, 3, { api: 2, min: 1 }), { ok: false, problem: "app-too-old" });
  assert.deepEqual(apiFits(4, 2, { api: 2, min: 1 }), { ok: true });
});
