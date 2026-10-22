# Repository Guidelines

## Project Structure & Module Organization

Mavi VPN is a Rust workspace with QUIC, HTTP/3, and HTTP/2 transports.

- `backend/`: Linux VPN server; `shared/`: protocols and reusable client helpers.
- `windows/`, `linux/`: platform clients and services; `quic-tester/`: transport probes.
- `android/app/src/main/`: Kotlin app, `rust/` core, and `res/` assets.
- `gui/src/`: JavaScript frontend; `gui/src-tauri/`: Rust integration and icons.
- `scripts/tests/`: deployment/installer tests; `docs/`: documentation.

## Build, Test, and Development Commands

Use Rust 1.95+; GUI CI uses Node.js 24. Run Cargo commands from the repository root.

- `cargo build --release -p windows-vpn`: build Windows CLI/service; use `-p linux-vpn` on Linux.
- `cargo test-core-workspace`: shared, Android Rust, backend, and probe tests (Linux CI).
- `cargo test -p windows-vpn` / `cargo test -p linux-vpn`: native platform tests.
- In `gui/`, run `npm ci`, then `npm run tauri dev` for desktop development; `npm run build` builds frontend assets.
- `cargo test-gui-backend`: Tauri tests after building frontend assets.
- In `gui/`: `npm test`, `npm run lint`, `npm run test:coverage`.
- `python -m unittest discover -s scripts/tests`: installer/deployment checks.
- In `android/`: `./gradlew testDebugUnitTest` (JUnit); use `gradlew.bat` on Windows.

## Coding Style & Naming Conventions

Use `cargo fmt --all`, four-space Rust indentation, `snake_case` functions/modules, and `PascalCase` types. GUI Prettier uses two spaces, single quotes, and semicolons; run `npm run format`. Keep source files within CI's 500-line limit. Run Clippy with `--all-targets -- -D warnings` for affected packages.

## Testing Guidelines

Follow existing Rust `#[test]`/`#[tokio::test]` and shared `proptest` patterns. Keep regression tests near the changed module (`tests.rs`); GUI Vitest/jsdom tests belong in `gui/src/__tests__/*.test.js`. Cover changed behavior and failure paths. CI reports coverage without a global minimum; Android's optional JaCoCo verification requires 50% line coverage per class.

## Commit & Pull Request Guidelines

Use concise imperative subjects, e.g. `Fix automatic reconnect after generic server rejection`. Keep PRs focused; explain behavior, rationale, and validation, link relevant issues, and include screenshots/logs for UI/setup changes. Use short descriptive branches, e.g. `zerox80/fix-reconnect`; never include `codex/`.

## Required Fork Policy

Always use the latest commits of the `zerox80` forks: h2 `master`, h3/Quinn `main`. Preserve branch patches; never substitute registry releases or fixed revision/tag pins. Refresh and verify:

```sh
cargo update -p h2 -p quinn -p quinn-proto -p quinn-udp -p h3 -p h3-quinn -p h3-datagram
python scripts/check_fork_sources.py --check-remote
```

Commit refreshed `Cargo.lock`.

## Security & Configuration

Start from `backend/.env.example`; keep tokens/private keys untracked and redact logs. Follow `SECURITY.md` for private vulnerability reporting.
