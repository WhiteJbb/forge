# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/). This project is
pre-1.0 (see [DESIGN.md](DESIGN.md) for the milestone plan); versioning is not yet strict semver.

## [Unreleased]

### Added

- **M1 — Foundation**: `forge.yaml` config schema/loader, Provider protocol + LiteLLM SDK adapter,
  Model Registry (EWMA latency, sliding-window availability, instant cooldown on 429), Scheduler
  (hard feature filter → session affinity → scoring), streaming failover with first-chunk commit,
  forced `usage` collection on streams, client-cancellation propagation, three-tier timeout budget,
  `FORGE_API_KEY` auth, `/v1/embeddings`, passive-first Health Monitor.
- **M2 — Intelligence layer**: Policy Engine (first-match YAML rules, tier/model/attribute
  selectors, cumulative constraints), `/v1/messages` (Anthropic Messages API for Claude Code),
  `/v1/route/explain` routing dry-run, `POST /admin/reload` hot reload, proactive per-provider
  rate limiting (token bucket), pricing table + cost tracking, `forge` CLI
  (`start` / `init` / `doctor` / `models`).
- **M2.5 — Cleanup sprint**: renamed the `src/` package to `forge_gateway/`, added
  `pyproject.toml` with `forge` / `forge-gw` console-script entry points, replaced hand-guessed
  capability scores with benchmark-seeded values, added a Provider Simulator test harness
  (12 end-to-end failure scenarios), fixed `.env` auto-loading and health-probe calibration,
  fixed a `/v1/messages` streaming crash, registered `forge-gateway` on PyPI.
- **M3 — Platform**: capability learning loop (telemetry-based score correction with automatic
  feature demotion, `forge_gateway/core/tuner.py`), a built-in static dashboard SPA served at
  `GET /dashboard/ui` (replaces the originally planned separate Next.js app), Prometheus metrics
  at `GET /metrics`, and `GET /v1/stats/recent` for a recent-request feed.
- **UX sprint (U1-U5)**: `forge reload`, `forge guard` (`--no-paid` / `--max-cost` / `--allow-paid`
  / `--off`), and `forge policies` CLI commands; a `forge.local.yaml` overlay so the CLI can manage
  spend-guard policies without touching hand-written comments in `forge.yaml`; a `forge start`
  startup banner; spend-guard onboarding warnings; and a dashboard "Recent Requests" section plus
  an in-browser Route Explain runner.
- Automatic provider registration (`auto_providers`, on by default): any provider in the built-in
  catalog (OpenRouter, Groq, Mistral, DeepSeek, OpenAI, Anthropic, Ollama — in addition to NVIDIA)
  is registered and its models discovered at boot as soon as a matching API key is found in
  `.env` / the environment, with no `forge.yaml` edit required.

### Fixed

- A module-level app construction in `server.py` was loading real API keys during test/tool
  imports, leaking live traffic into what were meant to be isolated simulator tests.
- `Retry-After` header handling for litellm 1.91.x (the header lives on
  `e.litellm_response_headers`, not the top-level exception).
- Spurious `usage`-only streaming chunks with empty `choices` were leaking through to clients.

## [0.3.0.dev0] - 2026-07-09

### Added

- Initial PyPI registration of `forge-gateway` as a dev release — see [PUBLISHING.md](PUBLISHING.md).
