#!/bin/sh
# vibe-dev entrypoint: seed /workspace on first start, re-assert platform-owned
# files on every start, then hand over to the supervisor.
set -eu
W="${VIBE_WORKSPACE:-/workspace}"
T=/opt/template
# Normally set by the control plane (SLOPTOWN_AGENT_MODEL / SLOPTOWN_AGENT_SMALL_MODEL).
: "${VIBE_AGENT_MODEL:=gpt-6.1-sol}"
: "${VIBE_AGENT_SMALL_MODEL:=gpt-6-luna}"
export VIBE_AGENT_MODEL VIBE_AGENT_SMALL_MODEL
# opencode serve: HTTP basic auth, user "opencode", password = project token.
# VIBE_PROJECT_ID may be empty (warm-pool container not yet claimed).
export OPENCODE_SERVER_PASSWORD="${VIBE_PROJECT_TOKEN:?VIBE_PROJECT_TOKEN must be set}"
mkdir -p /tmp/vite /tmp/vite-temp "$HOME" "$XDG_CACHE_HOME" "$XDG_CONFIG_HOME" "$W/.data" "$W/.opencode"

# Files the agent must not change. Overwritten from the image on every start, so
# an image update also updates them in existing projects.
PLATFORM_FILES="AGENTS.md web/package.json web/package-lock.json web/vite.config.ts api/requirements.lock"
# A "fra bunden" project (agentd /reset-scratch writes the marker) has no web app:
# only its AGENTS.md is the platform's, and the supervisor runs no Vite or API.
if [ "$(cat "$W/.vibe-kind" 2>/dev/null)" = scratch ]; then
  T=/opt/template-scratch
  PLATFORM_FILES="AGENTS.md"
fi

git_c() { git -C "$W" -c user.name=SlopTown -c user.email=vibe@sloptown.invalid "$@"; }
# Platform ignores live in .git/info/exclude (untracked), so they apply to old
# projects too and the agent's .gitignore edits cannot un-ignore them.
# "node_modules" has no trailing slash: web/node_modules is a symlink.
write_exclude() {
  printf '%s\n' "# managed by vibe-dev" .data/ .opencode/ .vibe-kind .vibe-design node_modules web/dist/ __pycache__/ > "$W/.git/info/exclude"
}

if [ ! -d "$W/.git" ]; then
  cp -a "$T/." "$W/"
  git_c init -q -b main
  git_c config core.fileMode false
  write_exclude
  git_c add -A
  git_c commit -q -m "Start fra skabelon" -m "Vibe-Author: SlopTown"
fi

# copy_platform <source> <workspace path>: a read-only copy of a platform file.
copy_platform() {
  if [ -f "$1" ]; then
    mkdir -p "$(dirname "$W/$2")"
    rm -f "$W/$2"
    cp "$1" "$W/$2"
    chmod a-w "$W/$2"
  fi
}

write_exclude
for f in $PLATFORM_FILES; do
  copy_platform "$T/$f" "$f"
done
# A "fra bunden" project always has a page (index.html) for the preview, styled with
# Miira's tokens (miira-tokens.css): older projects without them get the placeholder
# and the tokens, and the agent then builds the page. Not platform files: they stay
# the project's own and are committed after the next turn.
if [ "$T" = /opt/template-scratch ]; then
  for f in index.html miira-tokens.css; do
    [ -e "$W/$f" ] || [ ! -f "$T/$f" ] || cp "$T/$f" "$W/$f"
  done
fi

# The design system and palette (agentd /design writes the marker "<style> <palette>").
# No marker: an older Vibe without design files. Ids are checked against the
# directories, because the agent can write the marker.
D=/opt/template-design
if [ "$T" = /opt/template ] && [ -f "$W/.vibe-design" ]; then
  read -r STYLE PALETTE _ < "$W/.vibe-design" || true
  case "$STYLE$PALETTE" in *[!a-z0-9-]*|"") STYLE="" ;; esac
  if [ -n "$STYLE" ] && [ -d "$D/styles/$STYLE" ] && [ -f "$D/palettes/$PALETTE.css" ]; then
    copy_platform "$D/styles/$STYLE/DESIGN.md" DESIGN.md
    copy_platform "$D/styles/$STYLE/design.css" web/src/design.css
    copy_platform "$D/palettes/$PALETTE.css" web/src/palette.css
    PLATFORM_FILES="$PLATFORM_FILES DESIGN.md web/src/design.css web/src/palette.css"
  fi
fi

# An image update can bring new platform files: record them as their own commit
# so the working tree is clean before the agent's first turn.
if [ -n "$(git -C "$W" status --porcelain -- $PLATFORM_FILES)" ]; then
  git_c add -- $PLATFORM_FILES
  git_c commit -q -m "Platformopdatering" -m "Vibe-Author: SlopTown"
fi

# Dependencies live only in the image.
if [ -d "$W/web" ]; then
  rm -rf "$W/web/node_modules"
  ln -s /opt/vibe/node_modules "$W/web/node_modules"
fi

exec /usr/local/bin/vibe-supervisor
