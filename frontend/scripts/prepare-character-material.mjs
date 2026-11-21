/** Compile the imagegen material layers to portable RGBA data for browser and native exports. */
import { readFileSync, writeFileSync } from "node:fs";
import { inflateSync } from "node:zlib";
import { fileURLToPath } from "node:url";

export function readPng(file) {
  const b = readFileSync(file),
    parts = [];
  let width, height, channels;
  for (let i = 8; i < b.length; ) {
    const size = b.readUInt32BE(i),
      type = b.toString("ascii", i + 4, i + 8),
      data = b.subarray(i + 8, i + 8 + size);
    if (type === "IHDR") {
      width = data.readUInt32BE(0);
      height = data.readUInt32BE(4);
      channels = data[9] === 6 ? 4 : 3;
      if (data[8] !== 8 || ![2, 6].includes(data[9]))
        throw Error("Expected RGB8 or RGBA8 PNG");
    }
    if (type === "IDAT") parts.push(data);
    i += size + 12;
  }
  const raw = inflateSync(Buffer.concat(parts)),
    decoded = new Uint8Array(width * height * channels),
    stride = width * channels;
  const paeth = (a, b, c) => {
    const p = a + b - c,
      pa = Math.abs(p - a),
      pb = Math.abs(p - b),
      pc = Math.abs(p - c);
    return pa <= pb && pa <= pc ? a : pb <= pc ? b : c;
  };
  for (let y = 0; y < height; y++) {
    const mode = raw[y * (stride + 1)];
    for (let x = 0; x < stride; x++) {
      const i = y * stride + x,
        a = x >= channels ? decoded[i - channels] : 0,
        up = y ? decoded[i - stride] : 0,
        c = y && x >= channels ? decoded[i - stride - channels] : 0;
      decoded[i] =
        (raw[y * (stride + 1) + 1 + x] +
          (mode === 0
            ? 0
            : mode === 1
              ? a
              : mode === 2
                ? up
                : mode === 3
                  ? Math.floor((a + up) / 2)
                  : paeth(a, up, c))) &
        255;
    }
  }
  if (channels === 4) return { width, height, pixels: decoded };
  const pixels = new Uint8Array(width * height * 4);
  for (let i = 0; i < width * height; i++) {
    pixels.set(decoded.subarray(i * 3, i * 3 + 3), i * 4);
    pixels[i * 4 + 3] = 255;
  }
  return { width, height, pixels };
}
function compile(name) {
  const input = new URL(
    `../assets/character-reference/material/${name}-source.png`,
    import.meta.url,
  );
  const { width, height, pixels } = readPng(input),
    visited = new Uint8Array(width * height);
  let best = [];
  // Ignore disconnected extraction dust; preserve the subject's own alpha and its transparent holes.
  for (let p = 0; p < visited.length; p++) {
    if (visited[p] || pixels[p * 4 + 3] < 128) continue;
    const component = [p];
    visited[p] = 1;
    for (let at = 0; at < component.length; at++) {
      const i = component[at],
        x = i % width;
      for (const j of [
        x ? i - 1 : -1,
        x < width - 1 ? i + 1 : -1,
        i - width,
        i + width,
      ]) {
        if (
          j < 0 ||
          j >= visited.length ||
          visited[j] ||
          pixels[j * 4 + 3] < 128
        )
          continue;
        visited[j] = 1;
        component.push(j);
      }
    }
    if (component.length > best.length) best = component;
  }
  const mask = new Uint8Array(width * height);
  let left = width,
    right = 0,
    top = height,
    bottom = 0;
  for (const p of best) {
    mask[p] = 1;
    const x = p % width,
      y = Math.floor(p / width);
    left = Math.min(left, x);
    right = Math.max(right, x);
    top = Math.min(top, y);
    bottom = Math.max(bottom, y);
  }
  const w = 160,
    h = Math.round((w * (bottom - top + 1)) / (right - left + 1)),
    out = Buffer.alloc(w * h * 4);
  for (let y = 0; y < h; y++)
    for (let x = 0; x < w; x++) {
      const sx = Math.min(
          right,
          Math.floor(left + ((x + 0.5) * (right - left + 1)) / w),
        ),
        sy = Math.min(
          bottom,
          Math.floor(top + ((y + 0.5) * (bottom - top + 1)) / h),
        );
      const at = sy * width + sx,
        idx = (y * w + x) * 4;
      if (!mask[at]) continue;
      out.set(pixels.subarray(at * 4, at * 4 + 4), idx);
    }
  console.log(name, {
    width,
    height,
    bounds: [left, top, right - left + 1, bottom - top + 1],
    texture: [w, h],
  });
  return { width: w, height: h, rgba: out.toString("base64") };
}
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const layers = Object.fromEntries(
    ["body", "glasses", "headphones", "scarf"].map((name) => [
      name,
      compile(name),
    ]),
  );
  writeFileSync(
    new URL("../packages/ui/src/agent/material-data.js", import.meta.url),
    "// Generated from approved-style material layers; run scripts/prepare-character-material.mjs.\nexport const MATERIAL_LAYERS = " +
      JSON.stringify(layers) +
      ";\n",
  );
}
