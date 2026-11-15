/** Reproducible static exports of the live slime, not separate mascot artwork. */
import { mkdirSync, readFileSync, writeFileSync, copyFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import {
  ico,
  png,
  render,
  resize,
} from "../packages/ui/scripts/app-icons/raster.mjs";
import {
  APP_ICON_IDS,
  SCENES,
  tiled,
} from "../packages/ui/scripts/app-icons/scenes.mjs";
import {
  bodyTones,
  bodyColorAt,
  EYE_COLOR,
  eyeShapes,
  inBody,
  toneAt,
} from "../packages/ui/src/agent/engine.js";
import "../packages/ui/scripts/favicon.mjs";

const root = fileURLToPath(new URL("..", import.meta.url));
const web = join(root, "apps/web/public");
const desktop = join(root, "apps/desktop/public");
const write = (name, data) => {
  const file = join(web, name);
  mkdirSync(dirname(file), { recursive: true });
  writeFileSync(file, data);
};
for (const id of APP_ICON_IDS) {
  const scene = SCENES[id];
  const art = render(512, scene.light(), { k: 2 });
  const tile = render(512, tiled(scene.light()), { k: 2 });
  const fav = ico(
    [16, 32, 48].map((size) => ({
      size,
      png: png(
        render(size, scene.favicon(size === 16 ? 16 : 32), {
          k: size === 48 ? 32 / 48 : 1,
        }),
      ),
    })),
  );
  write(`app-icons/${id}.ico`, fav);
  write(`app-icons/${id}.png`, png(resize(art, 160)));
  if (id === APP_ICON_IDS[0]) {
    write("favicon.ico", fav);
    write("icon-512.png", png(tile));
    write("apple-touch-icon.png", png(resize(art, 180)));
    write("vortoone/app-icon.png", png(tile));
    write("og.png", png(art));
    write(
      "app-icons/night-sky.png",
      png(
        render(
          512,
          scene.light().filter((l) => l.part !== "agent" && l.part !== "eyes"),
          { k: 2 },
        ),
      ),
    );
  }
}
function mark(color = "sky") {
  const tones = bodyTones(color),
    eyes = eyeShapes();
  return png(
    render(256, [
      {
        mask: (x, y) => inBody(x / 256, y / 256),
        color: (x, y) => {
          const u = x / 256,
            v = y / 256;
          const eye = eyes.some(
            (e) => u >= e.x && u < e.x + e.w && v >= e.y && v < e.y + e.h,
          );
          const mouth =
            (u >= 0.47 && u < 0.5 && v >= 0.72 && v < 0.75) ||
            (u >= 0.5 && u < 0.53 && v >= 0.69 && v < 0.72);
          return eye || mouth ? EYE_COLOR : bodyColorAt(color, u, v);
        },
      },
    ]),
  );
}
for (const color of [
  "sky",
  "indigo",
  "violet",
  "green",
  "teal",
  "rose",
  "amber",
  "graphite",
])
  write(`email/agent-${color}.png`, mark(color));
for (const name of [
  "mark",
  "avatar",
  "logo-bone",
  "logo-ink",
  "logo-ink-tile",
  "agent-indigo-round-cap",
  "agent-green-wide-headphones",
  "agent-rose-dots-flower",
  "agent-amber-round-antenna",
])
  write(`email/${name}.png`, mark());
mkdirSync(desktop, { recursive: true });
copyFileSync(join(web, "favicon.svg"), join(desktop, "favicon.svg"));
const svg = readFileSync(join(web, "favicon.svg"), "utf8").trim();
const unavailable = join(web, "unavailable.html");
writeFileSync(
  unavailable,
  readFileSync(unavailable, "utf8")
    .replace(
      /<link rel="icon"[^>]*>/,
      `<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,${encodeURIComponent(svg)}">`,
    )
    .replace(
      /<svg class="mark"[\s\S]*?<\/svg>/,
      svg.replace("<svg ", '<svg class="mark" aria-hidden="true" '),
    )
    .replaceAll("agentes.chat", "VortoOne"),
);
console.log(
  "Generated VortoOne web marks, favicons, email assets and desktop favicon from the live engine.",
);
