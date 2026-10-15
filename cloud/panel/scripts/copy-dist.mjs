// next build → out/ ; tayyor statik fayllar cloud/panel_dist ga ko'chiriladi (bulut serveri shu papkani beradi).
// build.json — manba fayllar xeshi: tests/test_panel.py build eskirganini (manba o'zgarib, build qilinmaganini) aniqlaydi.
import { createHash } from "node:crypto";
import { cpSync, existsSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
const src = join(root, "out");
const dst = join(root, "..", "panel_dist");

export function sourceHash() {
  const files = [];
  const walk = (d) => {
    for (const n of readdirSync(d)) {
      const p = join(d, n);
      if (statSync(p).isDirectory()) walk(p);
      else files.push(p);
    }
  };
  for (const d of ["app", "components", "lib", "public"]) walk(join(root, d));
  for (const f of ["package.json", "package-lock.json", "next.config.mjs", "tsconfig.json"]) files.push(join(root, f));
  const h = createHash("sha256");
  const rel = files.map((f) => [relative(root, f).split(sep).join("/"), f]).sort((a, b) => (a[0] < b[0] ? -1 : 1));
  for (const [r, f] of rel) {
    // Windows'dagi CRLF farq qilmasin
    const body = readFileSync(f).toString("binary").replace(/\r/g, "");
    h.update(r + "\0" + body + "\0", "binary");
  }
  return h.digest("hex");
}

if (!existsSync(join(src, "index.html"))) {
  console.error("out/index.html topilmadi — next build muvaffaqiyatsiz");
  process.exit(1);
}
rmSync(dst, { recursive: true, force: true });
cpSync(src, dst, { recursive: true });
const version = JSON.parse(readFileSync(join(root, "package.json"), "utf8")).version;
writeFileSync(join(dst, "build.json"), JSON.stringify({ version, source_sha256: sourceHash(), built_at: new Date().toISOString() }, null, 2) + "\n");
console.log("Panel build:", dst);
