# design/ — Miira design assets, vendored

SlopTown looks like Miira by copying Miira's design assets from a **pinned**
miira3 commit. Nothing here is hand-written: change Miira, then re-sync.

## Files

| Path | |
|---|---|
| `sync-from-miira.mjs` | the sync (Node ≥ 18, needs `git`). Usage and outputs are in its header comment |
| `PINNED` | JSON written by the sync: miira3 commit, time, fonts flag, sha256 of every output file |
| `dist/` | `tokens.css`, `components.css`, `icons/filetypes/*`, `fonts/*` (only if licensed) |
| `test/test-sync.sh` | runs the sync against a fake miira3 repo in a node container (18 checks) |

The sync also writes the same files to `studio/src/styles/vendor/`. Vibe apps
get the theme through the SDK: `sdk/js/scripts/copy-theme.mjs` builds
`@miira/vibe/theme.css` from `design/dist` (so the template needs no copy).

The first real sync ran on 2026-10-28 (`PINNED`). Before it, `dist/tokens.css`
was a placeholder with a purple accent, and Vibe images built then still carry
it: every app in them is purple until the image is rebuilt.

## Commands

```bash
# sync (needs a miira3 checkout; the commit is required)
node design/sync-from-miira.mjs ../miira3 --commit <sha>
node design/sync-from-miira.mjs ../miira3 --commit <sha> --fonts-licensed   # see licence rule
node design/sync-from-miira.mjs ../miira3 --commit <sha> --dry-run

# verify (CI): outputs unchanged since the sync; with a checkout, also no drift from the commit
node design/sync-from-miira.mjs --check
node design/sync-from-miira.mjs --check ../miira3

design/test/test-sync.sh
```

After a sync: rebuild `vibe-base` (SDK theme) and the studio; commit `PINNED`,
`dist/` and `studio/src/styles/vendor/` together.

## Invariants

- **Licence gate for fonts.** Roobert PRO is commercially licensed. Serving it
  from SlopTown's domains is a second distribution. Fonts are copied only with
  `--fonts-licensed`, and the script refuses if miira3's `THIRD-PARTY-RIGHTS.md`
  at that commit doesn't mention Roobert. The keyword check is a tripwire, not
  a licence review: a human reads the licence before passing the flag.
- Files are read with `git show <commit>:<path>`. The checkout's working tree
  doesn't matter, so the result is reproducible.
- Source paths are in `SOURCES` at the top of the script
  (`drive/public/tokens.css`, `drive/public/styleguide.html` → all `<style>`
  blocks, `drive/public/fonts/**`, `drive/public/icons/filetypes/**`). If Miira
  moves them, change them there and in this file.
- Re-sync deletes only what a sync produces (`tokens.css`, `components.css`,
  `fonts/`, `icons/`) in each destination and keeps anything else.
- Never edit outputs by hand; `--check` reports it.

## Not verified

There is no miira3 checkout on the SlopTown server. The real source paths (in
particular `icons/filetypes` and `fonts`) are assumptions from the plan; check
them on the first real sync.
