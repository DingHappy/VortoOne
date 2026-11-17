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
