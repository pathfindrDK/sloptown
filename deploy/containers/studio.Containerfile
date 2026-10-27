# Studio SPA as a data-only image: /srv/studio. edge/Containerfile copies it in.
# Build context: repo root.
ARG NODE_IMAGE=docker.io/library/node:22-bookworm-slim
FROM ${NODE_IMAGE} AS build
WORKDIR /build
COPY studio/package.json studio/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY studio/ ./
RUN rm -rf node_modules/.vite dist && npm run build && test -f dist/index.html

FROM scratch
COPY --from=build /build/dist /srv/studio
