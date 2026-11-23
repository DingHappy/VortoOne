import assert from "node:assert/strict";
import test from "node:test";
import { serverLocation } from "../apps/desktop/src/control/first-run";

test("a bundled server opens the local installer and a remote choice opens address entry", () => {
  assert.deepEqual(serverLocation("here", true), {
    place: "here",
    next: "here",
  });
  assert.deepEqual(serverLocation("box", true), {
    place: "box",
    next: "connect",
  });
});

test("without a bundled server local artwork and continuation fall back to remote", () => {
  assert.deepEqual(serverLocation("here", false), {
    place: "box",
    next: "connect",
  });
  assert.deepEqual(serverLocation("box", false), {
    place: "box",
    next: "connect",
  });
});

test("cloud artwork can be previewed and never has a destination", () => {
  assert.deepEqual(serverLocation("cloud", true), {
    place: "cloud",
    next: null,
  });
  assert.deepEqual(serverLocation("cloud", false), {
    place: "cloud",
    next: null,
  });
});
