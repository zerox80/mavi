# syntax=docker/dockerfile:1
# From the repository root: docker build -f docker/server/Dockerfile .
FROM rust:1.98-slim-trixie AS builder
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    git cmake pkg-config build-essential \
    && rm -rf /var/lib/apt/lists/*

# Keep every workspace manifest; Cargo resolves the full workspace lockfile.
COPY . .
RUN --mount=type=cache,target=/usr/local/cargo/registry,sharing=locked \
    --mount=type=cache,target=/usr/local/cargo/git,sharing=locked \
    --mount=type=cache,target=/app/target,sharing=locked \
    cargo build --release --locked -p mavi-vpn && \
    cp /app/target/release/mavi-vpn /mavi-vpn

FROM debian:trixie-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    iptables iproute2 procps ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /mavi-vpn /app/mavi-vpn
COPY --from=builder /app/Cargo.lock /usr/share/mavi-vpn/Cargo.lock
COPY --from=builder /app/LICENSE /usr/share/mavi-vpn/LICENSE
COPY backend/entrypoint.sh /app/entrypoint.sh
RUN sed -i 's/\r$//' /app/entrypoint.sh \
    && chmod +x /app/entrypoint.sh && mkdir -p /app/data

ENTRYPOINT ["/app/entrypoint.sh"]
