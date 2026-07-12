<div align="center">

# Forge

### The local AI gateway that picks the right model for every coding task.

Point Claude Code, Cline, Aider, Continue, or any OpenAI-compatible agent at one endpoint. Forge routes each request by task, capability, cost, and live health—then fails over before your agent notices.

[![PyPI](https://img.shields.io/pypi/v/forge-gateway?color=2563eb&label=PyPI)](https://pypi.org/project/forge-gateway/)
[![Python](https://img.shields.io/pypi/pyversions/forge-gateway?color=3776ab)](https://pypi.org/project/forge-gateway/)
[![License](https://img.shields.io/github/license/WhiteJbb/forge?color=22c55e)](LICENSE)

[Quickstart](#quickstart) · [Why Forge](#why-forge) · [Connect an agent](#connect-your-coding-agent) · [Configuration](#control-cost-with-policies) · [Roadmap](docs/Roadmap.md)

</div>

```text
Claude Code / Cline / Aider / Continue
                  │
                  ▼  OpenAI or Anthropic API
          http://localhost:4000
                  │
                  ▼
               Forge
      analyze → filter → score → route
                  │
        ┌─────────┼─────────┐
        ▼         ▼         ▼
     NVIDIA   OpenRouter   Ollama   … 13 more providers
```

> **Alpha · v0.3.0.** The core gateway, streaming failover, policy engine, Anthropic compatibility, dashboard, and Prometheus metrics are working. Forge is pre-1.0, so configuration may still change between releases.

## Why Forge?

Most gateways ask you to choose a model or model group. Forge lets your coding agent request **`auto`** and makes the decision from the request itself.

- **Task-aware routing** — detects coding, debugging, refactoring, documentation, and testing work, then ranks models by the relevant capability.
- **Failover your agent does not see** — rate limits, timeouts, unavailable models, and context overflow move the request to the next compatible candidate.
- **Free-tier pooling** — proactive per-key rate limits and multi-key rotation stretch free provider quotas without sending paid traffic when `--no-paid` is enabled.
- **Explainable, not magical** — every exclusion and score is visible through `/v1/route/explain`; routing is deterministic and configurable.
- **Coding-agent compatibility** — serves both OpenAI and Anthropic APIs, including streaming and tool use, from the same local process.
- **Private by default** — runs on your machine, stores numeric metrics only, and sends no Forge telemetry.

Forge uses LiteLLM as its provider adapter. It is not trying to replace LiteLLM's broad platform surface; it adds an opinionated scheduler for local coding-agent traffic.

## Quickstart

Requires Python 3.10+. Install and start Forge in about two minutes:

```bash
pip install forge-gateway

export NVIDIA_API_KEY=nvapi-...   # PowerShell: $env:NVIDIA_API_KEY="nvapi-..."
forge init
forge doctor
forge start
```

Then open [http://127.0.0.1:4000/dashboard/ui](http://127.0.0.1:4000/dashboard/ui), or verify the gateway:

```bash
curl http://127.0.0.1:4000/health
```

`forge init` creates `forge.yaml`. Provider keys are read from the environment or a local `.env` file—never from the YAML itself. If the `forge` command conflicts with Foundry, use the identical `forge-gw` alias.

<details>
<summary><strong>Install from source</strong></summary>

```bash
git clone https://github.com/WhiteJbb/forge.git
cd forge
python -m venv .venv

# macOS / Linux
.venv/bin/pip install -e .

# Windows
.venv\Scripts\pip install -e .
```

</details>

## Connect your coding agent

Use model **`auto`** and Forge will choose. Use `auto:debug`, `auto:refactor`, `auto:documentation`, or `auto:testing` when you want to supply the task explicitly.

### Claude Code

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:4000
export ANTHROPIC_API_KEY=forge
```

Forge accepts the Anthropic Messages API and converts streaming events and tool calls for the selected provider.

### Cline / Roo Code

Choose **OpenAI Compatible**, then enter:

```text
Base URL: http://127.0.0.1:4000/v1
API Key:  forge
Model ID: auto
```

### Aider

```bash
export OPENAI_API_BASE=http://127.0.0.1:4000/v1
export OPENAI_API_KEY=forge
aider --model openai/auto
```

### Continue

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

Unless `FORGE_API_KEY` is set, clients may use any non-empty placeholder API key. Every response identifies the decision in `X-Forge-Model`, `X-Forge-Tier`, `X-Forge-Task`, and `X-Forge-Attempt` headers.

## How a request gets routed

```text
1. Analyze     "Fix this race condition" → task=debug
2. Constrain   Apply the first matching policy and its spending limits
3. Filter      Remove models without tools, vision, JSON mode, or enough context
4. Affinity    Keep an existing conversation on its model when possible
5. Score       Rank by task capability, health, latency, availability, and cost
6. Recover     On 429/5xx/timeout, cool down the failing key or model and retry
```

Preview the complete decision without calling a provider:

```bash
curl http://127.0.0.1:4000/v1/route/explain \
  -H "Content-Type: application/json" \
  -d '{"model":"auto","messages":[{"role":"user","content":"Refactor this parser"}]}'
```

The response includes the detected task, matched policy, every rejected model and reason, plus the final score table.

## What ships today

| Capability | What Forge does |
| --- | --- |
| OpenAI API | Chat completions, streaming, embeddings, and model listing |
| Anthropic API | Messages, streaming events, image blocks, and tool-use conversion |
| Smart scheduler | Hard compatibility filters, task scoring, session affinity, and context-aware failover |
| Reliability | First-chunk streaming failover, proactive throttling, `Retry-After` cooldowns, and hot reload |
| Cost controls | Free/paid classification, per-request caps, local spend guards, and cost tracking |
| Observability | Built-in dashboard, recent request feed, SQLite metrics, routing explanations, and Prometheus |
| Model learning | Runtime success data adjusts capability scores and demotes unreliable feature claims |

## Providers

Adding a provider is usually one environment variable. Forge auto-registers it, discovers models when the upstream supports discovery, and merges any benchmark-backed capability seeds.

| | Providers |
| --- | --- |
| Free/local paths | NVIDIA, Cerebras, Gemini, OpenRouter `:free` models, Ollama |
| Additional providers | Anthropic, OpenAI, Groq, Mistral, DeepSeek, SambaNova, Z.ai, xAI, Cohere, Together AI, Fireworks AI |

```bash
# Add another provider—no forge.yaml edit required
export OPENROUTER_API_KEY=sk-or-...

# Add another key for the same provider
export NVIDIA_API_KEY_2=nvapi-...
forge reload
```

Each provider key receives its own rate bucket. A 429 cools only the exhausted key; Forge tries the remaining keys before cooling down the model itself.

> “Free” means the provider currently offers a zero-cost path without billing attached; provider terms can change. Unknown-price models are treated conservatively and excluded by `--no-paid`. See the sourced notes in [Research.md](docs/Research.md).

## Control cost with policies

For the common case, no YAML editing is required:

```bash
forge guard --no-paid          # never route to paid or unknown-price models
forge guard --max-cost 0.01    # cap estimated cost per request in USD
forge guard --allow-paid       # remove the no-paid constraint
forge guard --off              # remove the local spend guard
```

For task-specific control, policies in `forge.yaml` are evaluated in order:

```yaml
policies:
  - name: free-documentation
    when:
      task: [documentation]
    route:
      prefer: [tier2]
      fallback: [tier3]
    constraints:
      allow_paid: false

  - name: default
    route:
      prefer: [tier1]
      fallback: [tier2, tier3]
```

Apply changes without interrupting in-flight requests:

```bash
forge reload
forge policies
```

See the commented, working [forge.yaml](forge.yaml) for provider, model, timeout, cooldown, and routing examples.

## API and CLI

<details>
<summary><strong>HTTP endpoints</strong></summary>

| Endpoint | Description |
| --- | --- |
| `POST /v1/chat/completions` | OpenAI-compatible chat, streaming, and failover |
| `POST /v1/messages` | Anthropic-compatible Messages API |
| `POST /v1/route/explain` | Dry-run a routing decision |
| `POST /v1/embeddings` | Embeddings with an explicit model ID |
| `GET /v1/models` | Discovered model pool and `auto` aliases |
| `GET /health` | Gateway and per-model health |
| `GET /v1/stats` | Usage, latency, and cost metrics |
| `GET /v1/stats/recent` | Recent routing and failover decisions |
| `GET /dashboard/ui` | Built-in browser dashboard |
| `GET /metrics` | Prometheus-format metrics |
| `POST /admin/reload` | Hot-reload configuration; loopback only |

</details>

<details>
<summary><strong>CLI commands</strong></summary>

| Command | Description |
| --- | --- |
| `forge start` | Start the gateway |
| `forge init` | Generate a starter config from detected provider keys |
| `forge doctor` | Validate config, credentials, connectivity, and discovery |
| `forge models` | List configured models and capabilities |
| `forge reload` | Reload a running gateway |
| `forge guard` | View or change the local spending guard |
| `forge policies` | Show effective policies in evaluation order |

</details>

## Forge or LiteLLM?

Choose **Forge** when you want a small, local, opinionated gateway for coding agents: the client asks for `auto`, the request content influences the model choice, free quotas are pooled, and the full decision is inspectable.

Choose **LiteLLM Proxy** when you need the broadest provider and enterprise gateway surface, virtual keys, teams, budgets, or production-scale deployment features. Forge already uses LiteLLM internally for provider compatibility; the projects solve different layers of the problem.

## Privacy and security

- Forge has no telemetry and no hosted control plane.
- Prompt and response bodies are never persisted; metrics contain numbers and routing metadata only.
- Provider secrets come from environment variables and are masked in logs.
- The server binds to `127.0.0.1` by default. Set `FORGE_API_KEY` before exposing it beyond loopback.

## Project status

Forge is actively developed and available on [PyPI](https://pypi.org/project/forge-gateway/). Near-term work focuses on routing-quality evaluation, protocol compatibility, CI/release hardening, and a shadow-evaluation loop—not expanding into a general enterprise gateway.

Read the [6-month roadmap](docs/Roadmap.md), [architecture](DESIGN.md), [changelog](CHANGELOG.md), and [research notes](docs/Research.md) for the full rationale and current decisions.

Bug reports, provider compatibility reports, and focused pull requests are welcome in [GitHub Issues](https://github.com/WhiteJbb/forge/issues).

## License

[MIT](LICENSE) — use it, modify it, and ship it.

<div align="center">

If Forge saves you from one more manual model switch, consider giving the project a ⭐. It helps other coding-agent users find it.

</div>
