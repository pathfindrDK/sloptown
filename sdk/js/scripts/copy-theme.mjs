// Copies Miira's synced design tokens (design/dist, written by design/sync-from-miira.mjs)
// into dist/theme.css so apps can `import "@miira/vibe/theme.css"`.
import { cpSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const designDist = process.env.MIIRA_DESIGN_DIST ?? join(here, "../../../design/dist");
const out = join(here, "../dist");
mkdirSync(out, { recursive: true });

const tokens = join(designDist, "tokens.css");
if (!existsSync(tokens)) {
  console.error(`copy-theme: ${tokens} not found (run design/sync-from-miira.mjs or set MIIRA_DESIGN_DIST)`);
  process.exit(1);
}
const base = readFileSync(join(here, "../src/base.css"), "utf8");
writeFileSync(join(out, "theme.css"), `${readFileSync(tokens, "utf8")}\n${base}`);
if (existsSync(join(designDist, "fonts"))) cpSync(join(designDist, "fonts"), join(out, "fonts"), { recursive: true });
console.log(`copy-theme: wrote dist/theme.css from ${tokens}`);
