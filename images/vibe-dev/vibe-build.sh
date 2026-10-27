#!/bin/sh
# vibe-build <app dir>: build the frontend of a published snapshot into <app dir>/web/dist.
# Run by the control plane in a one-shot vibe-dev container during publish:
#   podman run --rm --read-only --network none --entrypoint vibe-build -v vibe-pub-<pid>-<sha7>:/srv/app vibe-dev /srv/app
# (the entrypoint must be overridden: the dev entrypoint needs a project token).
set -eu
APP="${1:-/srv/app}"
mkdir -p /tmp/vite /tmp/vite-temp  # targets of the node_modules/.vite{,-temp} symlinks
cd "$APP/web"
rm -rf node_modules dist
ln -s /opt/vibe/node_modules node_modules
node /opt/vibe/node_modules/vite/bin/vite.js build --outDir dist --emptyOutDir
rm node_modules
test -f dist/index.html
echo "vibe-build: ok ($APP/web/dist)"
