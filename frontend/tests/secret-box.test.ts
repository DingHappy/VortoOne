import assert from "node:assert/strict";
import test from "node:test";
import { sealSecret, secretAad } from "../packages/client/src/lib/secret-box";

test("a v1 sender can still seal to an existing paired worker", async () => {
  const { subtle } = globalThis.crypto;
  const curve = { name: "ECDH", namedCurve: "P-256" } as const;
  const recipient = await subtle.generateKey(curve, true, ["deriveBits"]);
  const recipientKey = await subtle.exportKey("raw", recipient.publicKey);
  const fields = JSON.stringify({ password: "synthetic test value" });
  const aad = "vortoone-browser-login|v1|test-offer|https://example.test";
  const box = await sealSecret(Buffer.from(recipientKey).toString("base64"), fields, aad);
  assert.equal(box.v, 1);

  const decode = (value: string) => Uint8Array.from(Buffer.from(value, "base64"));
  const epk = decode(box.epk);
  const sender = await subtle.importKey("raw", epk, curve, false, []);
  const shared = await subtle.deriveBits({ name: "ECDH", public: sender }, recipient.privateKey, 256);
  const hkdf = await subtle.importKey("raw", shared, "HKDF", false, ["deriveKey"]);
  const encoder = new TextEncoder();
  const key = await subtle.deriveKey(
    { name: "HKDF", hash: "SHA-256", salt: epk, info: encoder.encode("agentes.chat secret v1") },
    hkdf,
    { name: "AES-GCM", length: 256 },
    false,
    ["decrypt"],
  );
  const plaintext = await subtle.decrypt(
    { name: "AES-GCM", iv: decode(box.iv), additionalData: encoder.encode(aad) },
    key,
    decode(box.ct),
  );
  assert.equal(new TextDecoder().decode(plaintext), fields);
});

test("the v1 secret request AAD keeps its established prefix", () => {
  assert.equal(
    secretAad({ requestId: "request", orgId: "home", name: "NAME", target: { kind: "env" } }),
    "agentes-secret|v1|request|home|NAME|env",
  );
});
