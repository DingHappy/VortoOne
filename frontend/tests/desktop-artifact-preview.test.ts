import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import test from "node:test";
import { runInNewContext } from "node:vm";
import {
  DESKTOP_ARTIFACT_SANDBOX,
  desktopArtifactPreviewStatus,
  hasNativeArtifactFrame,
} from "../apps/web/src/lib/desktop-artifact-preview";

const file = readFileSync(
  new URL("../apps/web/public/artifact-preview.html", import.meta.url),
  "utf8",
);
const script = /<script>([\s\S]*?)<\/script>/.exec(file)?.[1] ?? "";
const style = /<style>([\s\S]*?)<\/style>/.exec(file)?.[1] ?? "";
const origin = "https://workspace.example";
const ns = "http://www.w3.org/1999/xhtml";

test("the static macOS frame requires the real Tauri bridge and a live site", () => {
  const shell = {
    __TAURI_INTERNALS__: { invoke() {} },
    location: { protocol: "https:" },
  };
  const mac = { __vortooneDesktop: { platform: "mac" } };
  assert.equal(hasNativeArtifactFrame({ ...shell, ...mac }), true);
  assert.equal(
    hasNativeArtifactFrame({ ...shell, __agentesDesktop: { platform: "mac" } }),
    true,
  );
  assert.equal(
    hasNativeArtifactFrame({ location: shell.location, ...mac }),
    false,
  );
  assert.equal(hasNativeArtifactFrame(shell), false);
  assert.equal(
    hasNativeArtifactFrame({
      ...shell,
      __vortooneDesktop: { platform: "linux" },
    }),
    false,
  );
  assert.equal(
    hasNativeArtifactFrame({
      ...shell,
      __vortooneDesktop: { platform: "windows" },
    }),
    false,
  );
  assert.equal(
    hasNativeArtifactFrame({
      ...shell,
      ...mac,
      location: { protocol: "tauri:" },
    }),
    false,
  );
});

test("the frame is opaque and its policy pins only its fixed script and stylesheet", () => {
  assert.equal(DESKTOP_ARTIFACT_SANDBOX, "allow-scripts");
  const pin = (value: string) =>
    "'sha256-" + createHash("sha256").update(value).digest("base64") + "'";
  const policy =
    /http-equiv="Content-Security-Policy" content="([^"]+)"/.exec(file)?.[1] ??
    "";
  for (const directive of [
    "default-src 'none'",
    "script-src " + pin(script),
    "style-src " + pin(style),
    "img-src data:",
    "connect-src 'none'",
    "frame-src 'none'",
    "base-uri 'none'",
    "form-action 'none'",
    "object-src 'none'",
  ]) {
    assert.ok(policy.split("; ").includes(directive), directive);
  }
  assert.ok(file.indexOf(policy) < file.indexOf("<script>"));
});

test("the parent accepts statuses only from its opaque child window", () => {
  const child = {} as Window;
  const status = (source: Window, sender: string, value: unknown) =>
    desktopArtifactPreviewStatus(
      {
        source,
        origin: sender,
        data: { vortooneArtifactPreview: value },
      } as MessageEvent,
      child,
    );
  assert.equal(status(child, "null", "ready"), "ready");
  assert.equal(status(child, "null", "failed"), "failed");
  assert.equal(status(child, origin, "ready"), null);
  assert.equal(status({} as Window, "null", "ready"), null);
  assert.equal(status(child, "null", "render"), null);
});

class Element {
  nodeType = 1;
  attributes: Map<string, string>;

  constructor(
    public localName: string,
    attributes: Record<string, string> = {},
    public childNodes: Array<Element | TextNode> = [],
    public namespaceURI = ns,
  ) {
    this.attributes = new Map(Object.entries(attributes));
  }

  getAttribute(name: string) {
    return this.attributes.get(name) ?? null;
  }

  setAttribute(name: string, value: string) {
    this.attributes.set(name, value);
  }

  append(child: Element | TextNode) {
    this.childNodes.push(child);
  }

  replaceChildren(fragment?: Element) {
    this.childNodes = fragment?.childNodes ?? [];
  }
}

class TextNode {
  nodeType = 3;
  constructor(public data: string) {}
}

test("the shell ignores unrelated messages and rebuilds only inert, permitted content", () => {
  const preview = new Element("main");
  const sourceHeading = new Element(
    "h1",
    { onclick: "untrusted", id: "preview" },
    [new TextNode("Fixture heading")],
  );
  const fixtures = [
    sourceHeading,
    new Element("script", {}, [new TextNode("untrusted")]),
    new Element("style", {}, [new TextNode("untrusted")]),
    new Element("a", { href: "https://outside.example", target: "_top" }, [
      new TextNode("Fixture link"),
    ]),
    new Element("form", { action: "https://outside.example" }, [
      new Element("button"),
    ]),
    new Element("iframe", { src: "https://outside.example" }),
    new Element("img", { src: "https://outside.example/logo.png" }),
    new Element("img", { src: "data:image/svg+xml;base64,AAAA" }),
    new Element("img", {
      src: "data:image/png;base64,AAAA",
      srcset: "https://outside.example/a",
      alt: "Fixture",
    }),
    new Element(
      "a",
      { href: "https://outside.example" },
      [new TextNode("SVG link")],
      "http://www.w3.org/2000/svg",
    ),
  ];
  let parsed = "";
  const template = {
    set innerHTML(value: string) {
      parsed = value;
    },
    content: { childNodes: fixtures },
  };
  const posts: Array<[string, string]> = [];
  const parent = {
    postMessage: (data: { vortooneArtifactPreview: string }, target: string) =>
      posts.push([data.vortooneArtifactPreview, target]),
  };
  let receive: (event: object) => void = () =>
    assert.fail("no message listener installed");
  const document = {
    getElementById: () => preview,
    createElement: (name: string) =>
      name === "template" ? template : new Element(name),
    createTextNode: (text: string) => new TextNode(text),
    createDocumentFragment: () => new Element("fragment"),
  };
  runInNewContext(script, {
    window: {
      parent,
      location: { href: origin + "/artifact-preview.html" },
      addEventListener: (_: string, listener: typeof receive) => {
        receive = listener;
      },
    },
    document,
    Node: { ELEMENT_NODE: 1, TEXT_NODE: 3 },
    URL,
  });

  const render = {
    vortooneArtifactPreview: "render",
    html: "<h1>Fixture heading</h1>",
  };
  receive({ source: {}, origin, data: render });
  receive({ source: parent, origin: "https://outside.example", data: render });
  receive({ source: parent, origin, data: { ...render, html: {} } });
  assert.equal(parsed, "");
  receive({
    source: parent,
    origin,
    data: { vortooneArtifactPreview: "probe" },
  });
  assert.deepEqual(posts, [["ready", origin]]);
  receive({ source: parent, origin, data: render });

  assert.equal(parsed, render.html);
  const [heading, link, image] = preview.childNodes as Element[];
  assert.deepEqual(
    preview.childNodes.map((node) => (node as Element).localName),
    ["h1", "span", "img"],
  );
  assert.notEqual(heading, sourceHeading);
  assert.deepEqual([...heading.attributes], []);
  assert.equal((heading.childNodes[0] as TextNode).data, "Fixture heading");
  assert.deepEqual([...link.attributes], []);
  assert.equal((link.childNodes[0] as TextNode).data, "Fixture link");
  assert.deepEqual(
    [...image.attributes],
    [
      ["src", "data:image/png;base64,AAAA"],
      ["alt", "Fixture"],
    ],
  );

  let deeplyNested = new Element("p");
  for (let i = 0; i < 100; i++)
    deeplyNested = new Element("div", {}, [deeplyNested]);
  fixtures.push(deeplyNested);
  receive({ source: parent, origin, data: render });
  assert.deepEqual(preview.childNodes, []);
  assert.deepEqual(posts, [
    ["ready", origin],
    ["failed", origin],
  ]);
});
