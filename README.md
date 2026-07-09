# Forge

> Intelligent AI Gateway for Coding Agents — point every coding agent at one endpoint, and stop thinking about models.

Forge sits between your coding agents (Cline, Aider, Continue, …) and your LLM providers. It analyzes each request, picks the best available model for the task, and fails over transparently when a model is rate-limited or down.

```text
Cline / Aider / Continue / RooCode ──▶ localhost:4000 ──▶ Forge ──▶ best model right now
```

**Why not just LiteLLM?** LiteLLM routes by model group. Forge routes by **request content**: it detects what you're doing (refactoring? debugging? writing docs?), filters out models that can't handle the request (no function calling? context too small?), keeps a conversation pinned to one model for prompt-cache hits, and scores the rest on live health, latency, and capability. Every routing decision is explainable — no black-box learned router.

> **Status: pre-release (v0.2-dev).** Core routing, failover, and metrics work. Policy engine, Anthropic (`/v1/messages`) support, and the dashboard UI are in progress. Expect breaking changes.

## Features

- **OpenAI-compatible API** — `/v1/chat/completions` (streaming included), `/v1/embeddings`, `/v1/models`
- **Task-aware routing** — request analysis → capability matrix → best model, with `auto:refactor`-style hints when you know better
- **Hard compatibility filter** — requests needing tools / JSON mode / vision / large context never land on a model that can't serve them
- **Session affinity** — the same conversation stays on the same model (prompt-cache hits, consistent behavior); moves only on failover
- **Self-healing failover** — 429 → instant cooldown (honors `Retry-After`) → next candidate; context-overflow → retry on a bigger-context model; all invisible to the client
- **Auto discovery** — new models on your provider are registered automatically at boot
- **Real metrics** — per-model latency (TTFT for streams), success rate, token usage, cost — stored locally in SQLite

## Quickstart

Requires Python 3.10+.

```bash
git clone https://github.com/WhiteJbb/forge.git
cd forge
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # Windows
# .venv/bin/pip install -r requirements.txt     # macOS/Linux

export NVIDIA_API_KEY=nvapi-...                  # or put it in .env
python -m src.server
```

Forge listens on `http://127.0.0.1:4000`. Check `http://127.0.0.1:4000/health` to see your model pool.

Configuration lives in [`forge.yaml`](forge.yaml) — providers, model tiers/capabilities, cooldowns, timeouts. It ships with a working NVIDIA free-tier setup.

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

**Claude Code** — needs the Anthropic-format endpoint (`/v1/messages`), which lands in the next milestone.

Every response carries routing metadata in headers: `X-Forge-Model`, `X-Forge-Tier`, `X-Forge-Task`, `X-Forge-Attempt`.

## Endpoints

| Endpoint | Description |
| --- | --- |
| `POST /v1/chat/completions` | OpenAI-compatible chat, streaming + transparent failover |
| `POST /v1/embeddings` | Embeddings (explicit model id) |
| `GET /v1/models` | Model pool + `auto` aliases |
| `GET /health` | Gateway + per-model health |
| `GET /v1/stats` | Usage / latency / cost metrics (JSON) |
| `GET /dashboard` | Dashboard data (JSON) |
| `POST /admin/cooldown/{model}/clear` | Manually clear a cooldown (loopback only) |

## Privacy

Forge runs entirely on your machine. **No telemetry, ever.** Prompt and response bodies are never stored — metrics keep numbers only (latency, token counts, status codes). API keys are read from environment variables and never written to disk or logs.

## Roadmap

See [DESIGN.md](DESIGN.md) — up next: YAML policy engine ("route by policy, not by model"), Anthropic `/v1/messages` support for Claude Code, proactive rate limiting for free tiers, `/v1/route/explain` dry-run, and a web dashboard.

## License

[MIT](LICENSE)
