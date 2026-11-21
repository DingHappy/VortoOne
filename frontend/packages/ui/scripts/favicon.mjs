// Boot/connector mark uses the same material and neutral face as the live avatar.
import { writeFileSync } from "node:fs";
import {
  bodyColorAt,
  EYE_COLOR,
  eyeShapes,
  inBody,
} from "../src/agent/engine.js";
import { png, render } from "./app-icons/raster.mjs";
const N = 160;
const bitmap = png(
  render(N, [
    {
      mask: (x, y) => inBody(x / N, y / N),
      color: (x, y) => bodyColorAt("sky", x / N, y / N),
    },
  ]),
);
const eyes = eyeShapes()
  .map(
    (e) =>
      `<rect x="${e.x * 32}" y="${e.y * 32}" width="${e.w * 32}" height="${e.h * 32}" rx=".25"/>`,
  )
  .join("");
const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32"><title>VortoOne</title><image width="32" height="32" href="data:image/png;base64,${bitmap.toString("base64")}"/><g fill="${EYE_COLOR}">${eyes}<path d="M15.04 23.04h.9v.9h-.9zM16 22.08h.9v.9H16z"/></g></svg>\n`;
writeFileSync(
  new URL("../../../apps/web/public/favicon.svg", import.meta.url),
  svg,
);
console.log("Generated material-based VortoOne mark");
