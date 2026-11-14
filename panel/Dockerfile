# syntax=docker/dockerfile:1
# Build with the repository root context: docker build -f panel/Dockerfile .

# Builder Stage
FROM rust:1.98-slim AS builder
WORKDIR /app

# Install git for Cargo to fetch git dependencies
# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y --no-install-recommends \
    git cmake pkg-config build-essential && rm -rf /var/lib/apt/lists/*

# Keep the checked-in workspace manifest and lockfile together. Rewriting the
# workspace member list would require Cargo to rewrite Cargo.lock and is
# therefore incompatible with --locked.
COPY . .
RUN --mount=type=cache,target=/usr/local/cargo/registry,sharing=locked \
    --mount=type=cache,target=/usr/local/cargo/git,sharing=locked \
    --mount=type=cache,target=/app/target,sharing=locked \
    cargo build --release --locked -p mavi-panel && \
    cp /app/target/release/mavi-panel /mavi-panel

# Runtime Stage
FROM debian:trixie-slim
WORKDIR /app

# curl is only used by the container healthcheck.
# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /mavi-panel /usr/local/bin/mavi-panel
COPY --from=builder /app/Cargo.lock /usr/share/mavi-panel/Cargo.lock
COPY --from=builder /app/LICENSE /usr/share/mavi-panel/LICENSE

EXPOSE 8081
USER 1000:1000
ENTRYPOINT ["/usr/local/bin/mavi-panel"]
