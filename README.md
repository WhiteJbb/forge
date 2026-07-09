# Forge

> Intelligent AI Gateway for Coding Agents — point every coding agent at one endpoint, and stop thinking about models.

Forge sits between your coding agents (Cline, Aider, Continue, …) and your LLM providers. It analyzes each request, picks the best available model for the task, and fails over transparently when a model is rate-limited or down.

```text
Cline / Aider / Continue / RooCode ──▶ localhost:4000 ──▶ Forge ──▶ best model right now
```

**Why not just LiteLLM?** LiteLLM routes by model group. Forge routes by **request content**: it detects what you're doing (refactoring? debugging? writing docs?), filters out models that can't handle the request (no function calling? context too small?), keeps a conversation pinned to one model for prompt-cache hits, and scores the rest on live health, latency, and capability. Every routing decision is explainable — no black-box learned router.

> **Status: pre-release (v0.3-dev).** Core routing, failover, policies, Anthropic-format support, and the browser dashboard all work. Expect breaking changes.

## Features

- **OpenAI-compatible API** — `/v1/chat/completions` (streaming included), `/v1/embeddings`, `/v1/models`
- **Anthropic-compatible API** — `/v1/messages` with full streaming-event and tool-use conversion, so Claude Code can use any provider behind Forge
- **Policy engine** — manage *policies*, not models: first-match YAML rules pick candidate pools by task/client/context size, with hard constraints like `allow_paid: false`
- **Task-aware routing** — request analysis → capability matrix → best model, with `auto:refactor`-style hints when you know better
- **Hard compatibility filter** — requests needing tools / JSON mode / vision / large context never land on a model that can't serve them
- **Session affinity** — the same conversation stays on the same model (prompt-cache hits, consistent behavior); moves only on failover
- **Proactive rate limiting** — per-provider token buckets steer traffic away *before* the 429 happens; reactive cooldowns remain as the safety net
- **Self-healing failover** — 429 → instant cooldown (honors `Retry-After`) → next candidate; context-overflow → retry on a bigger-context model; all invisible to the client
- **Explainable decisions** — `POST /v1/route/explain` dry-runs any request and shows the matched policy, every exclusion reason, and the score table
- **Auto discovery & hot reload** — new provider models register at boot; edit `forge.yaml` and `POST /admin/reload` without dropping in-flight requests
- **Real metrics** — per-model latency (TTFT for streams), success rate, token usage, cost — stored locally in SQLite

## Quickstart

Requires Python 3.10+.

```bash
git clone https://github.com/WhiteJbb/forge.git
cd forge
python -m venv .venv
.venv/Scripts/pip install -e .     # Windows  (macOS/Linux: .venv/bin/pip install -e .)

export NVIDIA_API_KEY=nvapi-...    # or put it in .env
forge doctor                       # check keys & provider connectivity
forge start
```

Forge listens on `http://127.0.0.1:4000`. Check `http://127.0.0.1:4000/health` to see your model pool, or run `forge models` for an offline view. No config yet? `forge init` generates a `forge.yaml` from the API keys it finds in your environment.

> If the `forge` command collides with Foundry's on your PATH, use the `forge-gw` alias.

Configuration lives in [`forge.yaml`](forge.yaml) — providers, model tiers/capabilities, policies, cooldowns, timeouts. It ships with a working NVIDIA free-tier setup.

**Adding a provider is just an API key.** Drop `OPENROUTER_API_KEY` / `GROQ_API_KEY` / `MISTRAL_API_KEY` / `DEEPSEEK_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `CEREBRAS_API_KEY` / `SAMBANOVA_API_KEY` / `GEMINI_API_KEY` / `ZAI_API_KEY` (or `OLLAMA_API_BASE`) into `.env` and Forge auto-registers the provider at startup — models are discovered and join the routing pool automatically. Explicit `forge.yaml` entries always take precedence; set `auto_providers: false` to opt out. Keep paid spend in check with an `allow_paid: false` or `max_cost_per_request` policy — or skip the YAML and run `forge guard --no-paid` / `forge guard --max-cost 0.01` (see [CLI](#cli) below).

Free-tier providers Forge recognizes out of the box: NVIDIA, Cerebras, SambaNova, and Gemini all default to a no-cost path until you attach billing (`allow_paid: false` keeps you there). OpenRouter's `:free`-suffixed models are priced at $0 regardless of provider billing state. Z.ai (GLM) mixes free and paid models on the same key, so it's registered without a blanket free flag — see `.env.example` for how to pin a specific free model (e.g. GLM-4.7-Flash) with a `models:` price override.

A handful of standout free models (e.g. `cerebras:zai-glm-4.7`, `gemini:models/gemini-3-flash-preview`) are also pre-seeded with benchmark-based tier/capability scores as soon as their provider registers — no `forge.yaml` edits needed. See [Research.md](docs/Research.md) for sources.

## Connect your coding agent

Use model **`auto`** and let Forge choose, or `auto:debug` / `auto:refactor` / `auto:documentation` / `auto:testing` to force a task type. Unless you set `FORGE_API_KEY`, the API key can be any non-empty string.

**Cline (VS Code)** — Settings → API Provider: *OpenAI Compatible*
```text
Base URL: http://127.0.0.1:4000/v1
API Key:  forge
Model ID: auto
```

**Continue** — `config.json`:
```json
{
  "models": [{
    "title": "Forge",
    "provider": "openai",
    "model": "auto",
    "apiBase": "http://127.0.0.1:4000/v1",
    "apiKey": "forge"
  }]
}
```

**Aider**
```bash
export OPENAI_API_BASE=http://127.0.0.1:4000/v1
export OPENAI_API_KEY=forge
aider --model openai/auto
```

**Claude Code**
```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:4000
export ANTHROPIC_API_KEY=forge        # or your FORGE_API_KEY
```
Claude Code speaks the Anthropic Messages API; Forge converts it (tool use and streaming events included) and routes to whatever provider your policies choose.

Every response carries routing metadata in headers: `X-Forge-Model`, `X-Forge-Tier`, `X-Forge-Task`, `X-Forge-Attempt`.

## Endpoints

| Endpoint | Description |
| --- | --- |
| `POST /v1/chat/completions` | OpenAI-compatible chat, streaming + transparent failover |
| `POST /v1/messages` | Anthropic-compatible chat (Claude Code) |
| `POST /v1/route/explain` | Dry-run: matched policy, exclusion reasons, score table |
| `POST /v1/embeddings` | Embeddings (explicit model id) |
| `GET /v1/models` | Model pool + `auto` aliases |
| `GET /health` | Gateway + per-model health |
| `GET /v1/stats` | Usage / latency / cost metrics (JSON) |
| `GET /v1/stats/recent` | Recent request feed (JSON) — "why did that request go there?" |
| `GET /dashboard` | Dashboard data (JSON), incl. throttle state |
| `GET /dashboard/ui` | Browser dashboard (built-in static SPA) |
| `GET /metrics` | Prometheus-format metrics |
| `POST /admin/reload` | Hot-reload `forge.yaml` (loopback only) |
| `POST /admin/cooldown/{model}/clear` | Manually clear a cooldown (loopback only) |

## CLI

| Command | Description |
| --- | --- |
| `forge start` | Start the server |
| `forge init` | Generate a starter `forge.yaml` from detected env keys |
| `forge doctor` | Check provider keys, connectivity, and model discovery |
| `forge models` | List models known to the Registry (offline view) |
| `forge reload` | Hot-reload the running server's config (`POST /admin/reload`) |
| `forge guard --no-paid` / `--max-cost USD` / `--allow-paid` / `--off` | Manage a local spend guard (`forge.local.yaml`) without hand-editing `forge.yaml` — applies to the running server automatically |
| `forge policies` | List effective policies in evaluation order |

## Privacy

Forge runs entirely on your machine. **No telemetry, ever.** Prompt and response bodies are never stored — metrics keep numbers only (latency, token counts, status codes). API keys are read from environment variables and never written to disk or logs.

## Roadmap

See [DESIGN.md](DESIGN.md) — up next: a PostgreSQL storage backend and Redis-backed `StateStore` for multi-instance deployments, multi-API-key rotation per provider (multiply free-tier limits), and A/B testing / an AI Judge for capability scoring.

## License

[MIT](LICENSE)
