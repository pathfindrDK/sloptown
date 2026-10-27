#!/usr/bin/env bash
# Test design/sync-from-miira.mjs against a fake miira3 repo, in a node container.
#   design/test/test-sync.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
NODE_IMAGE="${NODE_IMAGE:-docker.io/library/node:22-bookworm-slim}"
podman run --rm -i -v "$ROOT/design:/design:ro,Z" "$NODE_IMAGE" bash -s <<'IN'
set -euo pipefail
apt-get -qq update >/dev/null && apt-get -qq install -y git >/dev/null 2>&1
git config --global user.email t@t; git config --global user.name t
pass=0; fail=0
check() { if eval "$2"; then echo "  ok   $1"; pass=$((pass+1)); else echo "  FAIL $1"; fail=$((fail+1)); fi; }
S="node /design/sync-from-miira.mjs"
M=/tmp/miira3; OUT=/tmp/out
mkdir -p $M/drive/public/fonts $M/drive/public/icons/filetypes $OUT
cd $M && git init -q
echo ':root { --miira-color-primary: #3355ff; }' > drive/public/tokens.css
cat > drive/public/styleguide.html <<'H'
<html><head><style>.btn--3d { box-shadow: 0 2px 0 var(--miira-color-primary); }</style>
<style media="screen">.modal { border-radius: 12px; }</style></head><body>demo</body></html>
H
echo font > drive/public/fonts/RoobertPRO-Regular.woff2
echo '<svg/>' > drive/public/icons/filetypes/vibe.svg
echo 'Fonts: Roobert PRO, licensed to Miira ApS for miira.dk only.' > THIRD-PARTY-RIGHTS.md
git add -A && git commit -qm one && C1=$(git rev-parse HEAD)

check "refuses without --commit" '! $S $M --out-root $OUT 2>/dev/null'
check "refuses unknown commit" '! $S $M --commit deadbeef --out-root $OUT 2>/dev/null'
check "--check fails before any sync" '! $S --check --out-root $OUT 2>/dev/null'
out=$($S $M --commit $C1 --out-root $OUT 2>&1)
check "sync runs" '[[ "$out" == *"3 file(s) x 2 destinations"* ]]'
check "fonts skipped without --fonts-licensed" '[[ "$out" == *"skipping 1 font"* && ! -e $OUT/design/dist/fonts ]]'
check "tokens copied with header" 'grep -q "miira-color-primary" $OUT/design/dist/tokens.css && head -1 $OUT/design/dist/tokens.css | grep -q "Do not edit"'
check "components.css has both style blocks" 'grep -q "btn--3d" $OUT/design/dist/components.css && grep -q "\.modal" $OUT/design/dist/components.css'
check "studio vendor copy" 'cmp -s $OUT/design/dist/tokens.css $OUT/studio/src/styles/vendor/tokens.css'
check "icons copied" '[ -f $OUT/design/dist/icons/filetypes/vibe.svg ]'
check "PINNED records commit" 'grep -q "\"commit\": \"$C1\"" $OUT/design/PINNED'
check "--check ok after sync" '$S --check --out-root $OUT >/dev/null'
check "--check with checkout ok" '$S $M --check --out-root $OUT >/dev/null'
echo '.x{}' >> $OUT/studio/src/styles/vendor/components.css
check "--check catches hand edits" '! $S --check --out-root $OUT 2>/dev/null'
$S $M --commit $C1 --out-root $OUT >/dev/null 2>&1
echo ':root { --miira-color-primary: #000; }' > drive/public/tokens.css && git commit -qam two
git checkout -q $C1 2>/dev/null; git checkout -q - 2>/dev/null || true
check "--check with checkout ok at pinned commit even if HEAD moved" '$S $M --check --out-root $OUT >/dev/null'
check "--fonts-licensed copies fonts when rights mention Roobert" '$S $M --commit $C1 --fonts-licensed --out-root $OUT >/dev/null 2>&1 && [ -f $OUT/design/dist/fonts/RoobertPRO-Regular.woff2 ] && grep -q "\"fonts\": true" $OUT/design/PINNED'
echo 'No fonts here.' > THIRD-PARTY-RIGHTS.md && git commit -qam three && C3=$(git rev-parse HEAD)
check "--fonts-licensed refused when rights don't cover Roobert" '! $S $M --commit $C3 --fonts-licensed --out-root $OUT 2>/dev/null'
mkdir -p $OUT/design/dist/keep && echo k > $OUT/design/dist/keep/me
$S $M --commit $C3 --out-root $OUT >/dev/null 2>&1
check "re-sync removes old fonts, keeps unrelated files" '[ ! -e $OUT/design/dist/fonts ] && [ -f $OUT/design/dist/keep/me ]'
check "--dry-run writes nothing" 'before=$(cat $OUT/design/PINNED); $S $M --commit $C1 --dry-run --out-root $OUT >/dev/null 2>&1; [ "$before" = "$(cat $OUT/design/PINNED)" ]'
echo "== $pass passed, $fail failed"
[ "$fail" = 0 ]
IN
