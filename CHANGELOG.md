# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/). This project is
pre-1.0 (see [DESIGN.md](DESIGN.md) for the milestone plan); versioning is not yet strict semver.

## [Unreleased]

## [0.3.0] - 2026-07-11

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
- Four more auto-registered providers: Cerebras, Gemini (Google AI Studio), and SambaNova join
  the built-in catalog, plus Z.ai (GLM) as an opt-in paid provider. `registry.merge_discovered`
  now prices OpenRouter's `:free`-suffixed models at $0 regardless of the provider's own billing
  state, instead of requiring a hand-maintained list.
- `PROVIDER_CATALOG` entries can carry a `capability_seed` — benchmark-sourced tier/capability
  scores for specific model IDs (e.g. `cerebras:zai-glm-4.7`, `gemini:models/gemini-3-flash-preview`)
  that apply automatically the moment the provider auto-registers, with no `forge.yaml` edit
  needed. Seeded entries are treated as config-sourced, so they're also eligible for active
  health probing (previously only real traffic could establish health for auto-discovered models).
- Four paid providers join the built-in catalog: x.ai (Grok), Cohere, Together AI, and Fireworks
  AI. `capability_seed` entries can now also carry a `price_per_mtok` override, sourced directly
  from each provider's official pricing page rather than LiteLLM's bundled cost table (see
  Research.md for citations); models without an officially-confirmed price or benchmark are
  registered without a seed and fall back to LiteLLM's cost table or `unknown`. AWS Bedrock and
  Azure OpenAI were evaluated but excluded — both need per-resource credentials/config that don't
  fit the single-API-key catalog pattern.
- Speed-aware routing: the `default`/`heavy-work`/`hard-tasks` policies' `prefer` order now
  reflects real measured TTFT across every registered provider (free and paid), not just NVIDIA.
  `default` exhausts fast free options before trying fast paid ones; `heavy-work`/`hard-tasks`
  still prefer the free `deepseek-v4-pro` and only fall back to its much faster paid hosts
  (Fireworks/Together, ~1-1.5s vs NVIDIA's ~18s) when it's unavailable. See Research.md for the
  full TTFT table and the `capability_seed.speed` field's dead-code finding that motivated this.

### Changed

- Tier consistency review across the whole catalog: `xai:grok-4.5` demoted tier1→tier2 (its
  benchmark is a self-reported, third-party-unverified claim — the same evidentiary bar that
  already kept its sibling `grok-build-0.1` at tier2), and `fireworks:glm-5p2` promoted to tier1
  after realizing it's the same model as the already-tier1 `nvidia:z-ai/glm-5.2`. Three existing
  NVIDIA entries (`mistral-medium-3.5`, `deepseek-v4-flash`, and Gemini's `gemini-3.5-flash`)
  promoted to tier1 where their official benchmark numbers directly overlap the existing tier1
  range on the same metric. See DecisionLog.md for the evidentiary standard this establishes.

### Fixed

- `server.py`'s logging setup didn't guard against non-ASCII characters in upstream error
  messages, which could crash the log write on a narrow-codepage Windows console
  (`UnicodeEncodeError`) and hide the real error; `cli.py` already reconfigured stdout/stderr
  with `errors="replace"`, now `server.py` does the same on the server-boot path.
- A module-level app construction in `server.py` was loading real API keys during test/tool
  imports, leaking live traffic into what were meant to be isolated simulator tests.
- `Retry-After` header handling for litellm 1.91.x (the header lives on
  `e.litellm_response_headers`, not the top-level exception).
- Spurious `usage`-only streaming chunks with empty `choices` were leaking through to clients.
- SambaNova was briefly (and incorrectly) marked as a recurring free-tier provider. It only
  offers a one-time ~$5 trial credit; once exhausted, requests fail with `402
  CREDITS_EXHAUSTED` regardless of billing status. It's now registered as a regular paid
  provider (excluded by `allow_paid: false`), while its benchmark-based capability scores are
  kept since those are independent of pricing.

## [0.3.0.dev0] - 2026-07-09

### Added

- Initial PyPI registration of `forge-gateway` as a dev release — see [PUBLISHING.md](PUBLISHING.md).
