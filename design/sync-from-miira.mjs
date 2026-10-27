#!/usr/bin/env node
// Vendor Miira's design assets into SlopTown from a pinned miira3 commit.
//
//   node design/sync-from-miira.mjs <miira3 checkout> --commit <sha> [--fonts-licensed] [--dry-run]
//   node design/sync-from-miira.mjs --check [<miira3 checkout>]
//
// Files are read with `git show <commit>:<path>`, so the checkout's working tree
// and current branch don't matter; only the commit does. Outputs (see design/AGENTS.md):
//
//   design/dist/tokens.css          drive/public/tokens.css, verbatim
//   design/dist/components.css      every <style> block of drive/public/styleguide.html
//   design/dist/fonts/*             drive/public/fonts/** (only with --fonts-licensed)
//   design/dist/icons/filetypes/*   drive/public/icons/filetypes/**
//   studio/src/styles/vendor/...    the same files, for the studio build
//   design/PINNED                   JSON: commit, time, fonts flag, sha256 of every output
//
// The SDK (sdk/js, copy-theme.mjs) and with it every Vibe app read design/dist.
//
// Fonts: Roobert PRO is commercially licensed. Serving it from SlopTown's
// domains (studio + every app) is a second distribution, so fonts are copied only
// with --fonts-licensed, after checking miira3's THIRD-PARTY-RIGHTS.md covers it.
// Without the flag the CSS falls back to the stacks in tokens.css.
//
// --check without a checkout: verifies the outputs still match the hashes in
// design/PINNED (catches hand edits). With a checkout: also regenerates from the
// pinned commit and fails on any difference (catches drift).
//
// Flags: --out-root <dir> writes under <dir> instead of the repo root (tests).

import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const REPO = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const SOURCES = {
  tokens: "drive/public/tokens.css",
  styleguide: "drive/public/styleguide.html",
  fonts: "drive/public/fonts",
  icons: "drive/public/icons/filetypes",
  rights: "THIRD-PARTY-RIGHTS.md",
};
const OUT_DIRS = ["design/dist", "studio/src/styles/vendor"];
const FONT_EXT = /\.(woff2?|ttf|otf)$/i;
const ICON_EXT = /\.(svg|png)$/i;

function parseArgs(argv) {
  const args = { positional: [], commit: null, check: false, fontsLicensed: false, dryRun: false, outRoot: REPO };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--commit") args.commit = argv[++i];
    else if (a === "--check") args.check = true;
    else if (a === "--fonts-licensed") args.fontsLicensed = true;
    else if (a === "--dry-run") args.dryRun = true;
    else if (a === "--out-root") args.outRoot = resolve(argv[++i]);
    else if (a === "-h" || a === "--help") args.help = true;
    else if (a.startsWith("--")) fail(`unknown option ${a}`);
    else args.positional.push(a);
  }
  return args;
}

function fail(msg) {
  console.error(`sync-from-miira: ${msg}`);
  process.exit(1);
}

const sha256 = (buf) => createHash("sha256").update(buf).digest("hex");

function git(repo, args, opts = {}) {
  return execFileSync("git", ["-C", repo, ...args], { maxBuffer: 64 << 20, ...opts });
}

function show(repo, commit, path) {
  try {
    return git(repo, ["show", `${commit}:${path}`], { stdio: ["ignore", "pipe", "ignore"] });
  } catch {
    return null;
  }
}

function listTree(repo, commit, dir) {
  try {
    return git(repo, ["ls-tree", "-r", "--name-only", commit, "--", dir], { encoding: "utf8" })
      .split("\n")
      .filter(Boolean);
  } catch {
    return [];
  }
}

// Concatenate the <style> blocks of the styleguide. The styleguide is the source
// of truth for component CSS (.btn--3d, .modal, .menu, ...); demo-only rules are
// left in, they are scoped to the styleguide's own markup.
function extractStyles(html) {
  const blocks = [...html.matchAll(/<style\b[^>]*>([\s\S]*?)<\/style>/gi)].map((m) => m[1].trim());
  if (blocks.length === 0) return null;
  return blocks.join("\n\n") + "\n";
}

function rightsCoverFonts(text) {
  if (!text) return false;
  return /roobert/i.test(text);
}

// Build the full output set (relative path -> Buffer) from the pinned commit.
function generate(repo, commit, { fontsLicensed }) {
  const files = new Map();
  const tokens = show(repo, commit, SOURCES.tokens);
  if (!tokens) fail(`${SOURCES.tokens} not found at ${commit}`);
  const html = show(repo, commit, SOURCES.styleguide);
  if (!html) fail(`${SOURCES.styleguide} not found at ${commit}`);
  const components = extractStyles(html.toString("utf8"));
  if (!components) fail(`no <style> blocks in ${SOURCES.styleguide}`);

  const header = (src) => `/* Vendored from miira3@${commit.slice(0, 12)}:${src} by design/sync-from-miira.mjs. Do not edit. */\n`;
  files.set("tokens.css", Buffer.from(header(SOURCES.tokens) + tokens.toString("utf8")));
  files.set("components.css", Buffer.from(header(SOURCES.styleguide) + components));

  for (const p of listTree(repo, commit, SOURCES.icons)) {
    if (ICON_EXT.test(p)) files.set(join("icons/filetypes", relative(SOURCES.icons, p)), show(repo, commit, p));
  }

  const fontPaths = listTree(repo, commit, SOURCES.fonts).filter((p) => FONT_EXT.test(p));
  if (fontPaths.length && fontsLicensed) {
    if (!rightsCoverFonts(show(repo, commit, SOURCES.rights)?.toString("utf8"))) {
      fail(`--fonts-licensed given, but ${SOURCES.rights} at ${commit} does not mention Roobert. Check the licence first.`);
    }
    for (const p of fontPaths) files.set(join("fonts", relative(SOURCES.fonts, p)), show(repo, commit, p));
  } else if (fontPaths.length) {
    console.warn(
      `sync-from-miira: skipping ${fontPaths.length} font file(s). Roobert PRO may only be served from SlopTown's ` +
        `domains if ${SOURCES.rights} allows it; re-run with --fonts-licensed once confirmed.`,
    );
  }
  return files;
}

function pinnedPath(root) {
  return join(root, "design/PINNED");
}

function readPinned(root) {
  const p = pinnedPath(root);
  if (!existsSync(p)) fail(`${relative(root, p)} missing; run a sync first`);
  try {
    return JSON.parse(readFileSync(p, "utf8"));
  } catch {
    fail(`${relative(root, p)} is not valid JSON`);
  }
}

function write(root, commit, files, { fontsLicensed, dryRun }) {
  const hashes = {};
  for (const outDir of OUT_DIRS) {
    const base = join(root, outDir);
    if (!dryRun) {
      // Remove what a previous sync produced, keep anything else in the directory.
      for (const sub of ["tokens.css", "components.css", "fonts", "icons"]) rmSync(join(base, sub), { recursive: true, force: true });
    }
    for (const [rel, buf] of files) {
      const dest = join(base, rel);
      hashes[join(outDir, rel)] = sha256(buf);
      if (dryRun) {
        console.log(`would write ${relative(root, dest)} (${buf.length} bytes)`);
        continue;
      }
      mkdirSync(dirname(dest), { recursive: true });
      writeFileSync(dest, buf);
    }
  }
  const pinned = {
    source: "miira3",
    commit,
    syncedAt: new Date().toISOString(),
    fonts: fontsLicensed && [...files.keys()].some((k) => k.startsWith("fonts/")),
    files: Object.fromEntries(Object.entries(hashes).sort()),
  };
  if (!dryRun) writeFileSync(pinnedPath(root), JSON.stringify(pinned, null, 2) + "\n");
  console.log(`sync-from-miira: ${files.size} file(s) x ${OUT_DIRS.length} destinations from miira3@${commit}${dryRun ? " (dry run)" : ""}`);
}

function check(root, repo) {
  const pinned = readPinned(root);
  if (!/^[0-9a-f]{40}$/.test(pinned.commit ?? "")) fail("design/PINNED has no full commit sha");
  const problems = [];
  for (const [rel, hash] of Object.entries(pinned.files ?? {})) {
    const p = join(root, rel);
    if (!existsSync(p)) problems.push(`missing ${rel}`);
    else if (sha256(readFileSync(p)) !== hash) problems.push(`edited by hand: ${rel}`);
  }
  if (repo) {
    const files = generate(repo, pinned.commit, { fontsLicensed: pinned.fonts });
    for (const outDir of OUT_DIRS) {
      for (const [rel, buf] of files) {
        const key = join(outDir, rel);
        if (pinned.files[key] !== sha256(buf)) problems.push(`differs from miira3@${pinned.commit.slice(0, 12)}: ${key}`);
      }
    }
    const expected = new Set(OUT_DIRS.flatMap((d) => [...files.keys()].map((k) => join(d, k))));
    for (const key of Object.keys(pinned.files)) if (!expected.has(key)) problems.push(`no longer in miira3: ${key}`);
  }
  if (problems.length) {
    for (const p of problems) console.error(`  ${p}`);
    fail(`check failed (${problems.length} problem(s)); re-run the sync`);
  }
  console.log(`sync-from-miira: check ok (miira3@${pinned.commit.slice(0, 12)}, ${Object.keys(pinned.files).length} files)`);
}

const args = parseArgs(process.argv.slice(2));
if (args.help) {
  console.log(readFileSync(fileURLToPath(import.meta.url), "utf8").split("\n").slice(1, 30).join("\n").replace(/^\/\/ ?/gm, ""));
  process.exit(0);
}
const repo = args.positional[0] ? resolve(args.positional[0]) : null;
if (repo) {
  try {
    git(repo, ["rev-parse", "--git-dir"], { stdio: "ignore" });
  } catch {
    fail(`${repo} is not a git checkout of miira3`);
  }
}
if (args.check) {
  check(args.outRoot, repo);
} else {
  if (!repo) fail("usage: sync-from-miira.mjs <miira3 checkout> --commit <sha> [--fonts-licensed] [--dry-run]");
  if (!args.commit) fail("--commit <sha> is required: the sync is always pinned to an explicit miira3 commit");
  let commit;
  try {
    commit = git(repo, ["rev-parse", "--verify", `${args.commit}^{commit}`], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim();
  } catch {
    fail(`commit ${args.commit} not found in ${repo} (git fetch first?)`);
  }
  const files = generate(repo, commit, args);
  write(args.outRoot, commit, files, args);
}
