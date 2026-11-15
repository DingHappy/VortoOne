import { MATERIAL_LAYERS } from "./material-data.js";

/** Identity pigments, matched to the approved coral, lavender, mint and yellow family. */
export const SLIME_PIGMENTS = {
  sky: "#ff9a82",
  indigo: "#8992e5",
  violet: "#af9bf1",
  rose: "#eb8fa6",
  amber: "#ffd261",
  green: "#82d3a2",
  teal: "#75d2a8",
  graphite: "#9ba6ad",
};
export const MATERIAL_BOUNDS = {
  body: [0, 0.12, 1, 124 / 160],
  glasses: [0.18, 0.5, 0.61, (0.61 * 44) / 160],
  headphones: [0.655, 0.19, 0.33, (0.33 * 185) / 160],
  scarf: [0.035, 0.705, 0.95, 0.225],
};
const layers = Object.fromEntries(
  Object.entries(MATERIAL_LAYERS).map(([name, layer]) => [
    name,
    {
      ...layer,
      pixels: Uint8ClampedArray.from(atob(layer.rgba), (c) => c.charCodeAt(0)),
    },
  ]),
);
function hsl(r, g, b) {
  r /= 255;
  g /= 255;
  b /= 255;
  const hi = Math.max(r, g, b),
    lo = Math.min(r, g, b),
    d = hi - lo,
    l = (hi + lo) / 2;
  let h = 0;
  if (d)
    h =
      hi === r
        ? ((g - b) / d) % 6
        : hi === g
          ? (b - r) / d + 2
          : (r - g) / d + 4;
  return [(h / 6 + 1) % 1, d ? d / (1 - Math.abs(2 * l - 1)) : 0, l];
}
function rgb(h, s, l) {
  const a = s * Math.min(l, 1 - l);
  return [0, 8, 4].map((n) => {
    const k = (n + h * 12) % 12;
    return Math.round(255 * (l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1))));
  });
}
// Continuous color ramps sampled from the approved family, including the darker
// side material. Interpolation retains the texture's subtle gradients between samples.
const PRESET_RAMPS = {
  violet: [
    [0.35, [76, 63, 152]],
    [0.524, [102, 88, 186]],
    [0.727, [174, 155, 241]],
    [0.86, [205, 184, 255]],
  ],
  teal: [
    [0.35, [23, 104, 82]],
    [0.524, [43, 139, 106]],
    [0.727, [118, 210, 168]],
    [0.86, [170, 232, 193]],
  ],
  amber: [
    [0.35, [155, 96, 23]],
    [0.524, [194, 133, 31]],
    [0.727, [254, 209, 97]],
    [0.86, [255, 231, 151]],
  ],
};
const BOTTOM_PIGMENTS = {
  violet: [89, 78, 171],
  teal: [37, 128, 101],
  amber: [175, 114, 25],
};
const mixRgb = (a, b, k) => a.map((v, i) => Math.round(v + (b[i] - v) * k));
function rampColor(stops, light) {
  if (light <= stops[0][0]) return stops[0][1];
  for (let i = 1; i < stops.length; i++)
    if (light <= stops[i][0]) {
      const [from, a] = stops[i - 1],
        [to, b] = stops[i];
      return mixRgb(a, b, (light - from) / (to - from));
    }
  return stops.at(-1)[1];
}
const pigments = new Map(),
  canvases = new Map(),
  hexes = new Map();
export function bodyMaterial(color = "sky") {
  const key = color || "sky";
  if (pigments.has(key)) return pigments.get(key);
  const source = layers.body,
    pixels = new Uint8ClampedArray(source.pixels);
  if (key === "sky") {
    // Match the approved sheet's front-face coral without washing out highlights or red side depth.
    for (let i = 0; i < pixels.length; i += 4) {
      if (!pixels[i + 3]) continue;
      const light = hsl(pixels[i], pixels[i + 1], pixels[i + 2])[2];
      const weight = Math.max(0, 1 - Math.abs(light - 0.73) / 0.16);
      pixels[i] = Math.min(255, pixels[i] + 5 * weight);
      pixels[i + 1] = Math.min(255, pixels[i + 1] + 12 * weight);
      pixels[i + 2] = Math.min(255, pixels[i + 2] + 8 * weight);
      const shade = Math.max(0, 1 - Math.abs(light - 0.52) / 0.13);
      const v =
        0.12 + (Math.floor(i / 4 / source.width) / source.height) * (124 / 160);
      const bottom = Math.max(0, Math.min(1, (v - 0.79) / 0.06));
      pixels[i] = Math.max(0, pixels[i] - (3 + 11 * bottom) * shade);
      pixels[i + 1] = Math.min(255, pixels[i + 1] + (10 - 6 * bottom) * shade);
      pixels[i + 2] = Math.min(255, pixels[i + 2] + (10 - 8 * bottom) * shade);
    }
  } else {
    const hex = /^#[a-f\d]{6}$/i.test(key)
      ? key
      : SLIME_PIGMENTS[key] || SLIME_PIGMENTS.sky;
    const number = parseInt(hex.slice(1), 16),
      target = hsl(number >> 16, (number >> 8) & 255, number & 255);
    const center =
      (Math.floor(source.height * 0.57) * source.width +
        Math.floor(source.width * 0.43)) *
      4;
    const reference = hsl(...source.pixels.subarray(center, center + 3));
    for (let i = 0; i < pixels.length; i += 4) {
      if (!pixels[i + 3]) continue;
      const original = hsl(pixels[i], pixels[i + 1], pixels[i + 2]),
        delta = original[2] - reference[2];
      if (PRESET_RAMPS[key]) {
        let pigment = rampColor(PRESET_RAMPS[key], original[2]);
        const v =
          0.12 +
          (Math.floor(i / 4 / source.width) / source.height) * (124 / 160);
        const bottom =
          Math.max(0, Math.min(1, (v - 0.79) / 0.06)) *
          Math.max(0, Math.min(1, (0.66 - original[2]) / 0.12));
        if (bottom) pigment = mixRgb(pigment, BOTTOM_PIGMENTS[key], bottom);
        pixels.set(pigment, i);
        continue;
      }
      const light = Math.max(
        0.04,
        Math.min(0.96, target[2] + delta * (delta > 0 ? 1.15 : 1.25)),
      );
      const saturation = target[1] * (0.82 + 0.18 * original[1]);
      pixels.set(rgb(target[0], saturation, light), i);
    }
  }
  const material = { ...source, pixels };
  if (pigments.size > 64) {
    pigments.clear();
    canvases.clear();
    hexes.clear();
  }
  pigments.set(key, material);
  return material;
}
export function bodyAlphaAt(u, v) {
  const [x, y, w, h] = MATERIAL_BOUNDS.body;
  if (u < x || u >= x + w || v < y || v >= y + h) return 0;
  const a = layers.body;
  return a.pixels[
    (Math.floor(((v - y) / h) * a.height) * a.width +
      Math.floor(((u - x) / w) * a.width)) *
      4 +
      3
  ];
}
export function bodyColorAt(color, u, v) {
  const [x, y, w, h] = MATERIAL_BOUNDS.body,
    a = bodyMaterial(color);
  const px = Math.max(
    0,
    Math.min(a.width - 1, Math.floor(((u - x) / w) * a.width)),
  );
  const py = Math.max(
    0,
    Math.min(a.height - 1, Math.floor(((v - y) / h) * a.height)),
  );
  let colors = hexes.get(color || "sky");
  if (!colors) {
    colors = new Array(a.width * a.height);
    hexes.set(color || "sky", colors);
  }
  const at = py * a.width + px;
  return (colors[at] ??=
    "#" +
    [...a.pixels.subarray(at * 4, at * 4 + 3)]
      .map((n) => n.toString(16).padStart(2, "0"))
      .join(""));
}
export function materialAt(name, u, v) {
  if (name === "headset") return micAt(u, v) || materialAt("headphones", u, v);
  const a = layers[name],
    r = MATERIAL_BOUNDS[name];
  if (!a || !r) return null;
  if (u < r[0] || v < r[1] || u >= r[0] + r[2] || v >= r[1] + r[3]) return null;
  const x = Math.floor(((u - r[0]) / r[2]) * a.width),
    y = Math.floor(((v - r[1]) / r[3]) * a.height),
    at = (y * a.width + x) * 4;
  return a.pixels[at + 3] >= 128
    ? "#" +
        [...a.pixels.subarray(at, at + 3)]
          .map((n) => n.toString(16).padStart(2, "0"))
          .join("")
    : null;
}
export function drawMaterial(ctx, name, s, color) {
  if (name === "headset") {
    drawMaterial(ctx, "headphones", s, color);
    ctx.fillStyle = "#464b57";
    for (const r of MIC_BOOM)
      ctx.fillRect(r[0] * s, r[1] * s, r[2] * s, r[3] * s);
    return true;
  }
  const a = name === "body" ? bodyMaterial(color) : layers[name],
    r = MATERIAL_BOUNDS[name];
  if (!a || !r) return false;
  const key = name === "body" ? `body:${color || "sky"}` : name;
  if (typeof document !== "undefined" && typeof ctx.drawImage === "function") {
    let canvas = canvases.get(key);
    if (!canvas) {
      canvas = document.createElement("canvas");
      canvas.width = a.width;
      canvas.height = a.height;
      const g = canvas.getContext("2d");
      if (g) {
        const img = g.createImageData(a.width, a.height);
        img.data.set(a.pixels);
        g.putImageData(img, 0, 0);
        canvases.set(key, canvas);
      } else canvas = null;
    }
    if (canvas) {
      ctx.imageSmoothingEnabled = true;
      ctx.drawImage(canvas, r[0] * s, r[1] * s, r[2] * s, r[3] * s);
      return true;
    }
  }
  // Native export adapters without DOM canvases consume the same authored texture.
  for (let y = 0; y < a.height; y++)
    for (let x = 0; x < a.width; x++) {
      const i = (y * a.width + x) * 4;
      if (a.pixels[i + 3] < 128) continue;
      ctx.fillStyle = `rgb(${a.pixels[i]},${a.pixels[i + 1]},${a.pixels[i + 2]})`;
      ctx.fillRect(
        (r[0] + (x / a.width) * r[2]) * s,
        (r[1] + (y / a.height) * r[3]) * s,
        (r[2] / a.width) * s + 0.01,
        (r[3] / a.height) * s + 0.01,
      );
    }
  return true;
}
const MIC_BOOM = [
  [0.9, 0.53, 0.022, 0.1],
  [0.86, 0.61, 0.06, 0.022],
  [0.78, 0.64, 0.1, 0.022],
  [0.75, 0.63, 0.04, 0.04],
];
function micAt(u, v) {
  return MIC_BOOM.some(
    ([x, y, w, h]) => u >= x && u < x + w && v >= y && v < y + h,
  )
    ? "#464b57"
    : null;
}
