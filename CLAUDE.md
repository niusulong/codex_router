# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Does

A local proxy server that converts OpenAI Responses API (`/v1/responses`) requests to Chat Completions API (`/v1/chat/completions`) requests or Anthropic Messages API (`/v1/messages`) requests. This enables Codex CLI (which only supports the Responses API) to use any Chat Completions-compatible model provider (e.g., iFlytek, DeepSeek, Qwen) or Anthropic-compatible provider (e.g., Claude).

## Development

```bash
pip install -e .                       # Install in editable mode
python -m codex_router                 # Start proxy (reads config.yaml from CWD)
codex-router --version                 # Check version
codex-router 18482                     # Start on custom port (overrides config)
```

**Requirements:** Python >=3.11. Build system: hatchling. Entry points: `codex-router` and `codex_router`.

No test suite, linter, or CI pipeline exists yet. The project uses `pytest_cache/` in gitignore, suggesting pytest is expected.

## Configuration

Primary config is `config.yaml`. Env vars (prefix `CODEX_ROUTER_`, nested delimiter `__`) override YAML values — see `.env.example`. Key fields: `upstream.base_url`, `upstream.api_key`, `upstream.api_format`, `codex.auto_configure`, `presets`, `active_preset`.

`upstream.api_format` determines the conversion path: `"openai"` uses Chat Completions converters, `"anthropic"` uses Anthropic converters.

`passthrough_api_key: true` forwards the client's API key to upstream when `upstream.api_key` is empty.

**Security:** `config.yaml` contains upstream API keys and is gitignored. Never commit it. `config.yaml` may be present locally in the working directory for development.

## Architecture

The proxy serves `/v1/responses` via both **HTTP POST** and **WebSocket**. Codex CLI prefers WebSocket (Realtime API protocol), falling back to HTTP POST.

**Request flow (both protocols):**
1. Parse Responses API request → `models.py` (Pydantic models)
2. Resolve `previous_response_id` → `response_store.py` (conversation chaining)
3. Convert to upstream format → `converters/request.py` (OpenAI) or `converters/anthropic_request.py` (Anthropic), selected by `upstream.api_format`
4. Forward to upstream via httpx → `router.py` (HTTP) or `ws_handler.py` (WebSocket)
5. Convert upstream response back → `converters/response.py` or `converters/anthropic_response.py` (non-streaming), `converters/streaming.py` or `converters/anthropic_streaming.py` (streaming SSE→Responses events)
6. Record stats → `stats.py`; persist token usage → `token_db.py` (SQLite with WAL mode)
7. Store response → `response_store.py`

**App bootstrap (`main.py`):**
`create_app()` builds the FastAPI app with all shared state on `app.state`: `config`, `config_manager`, `response_store`, `token_db`, `request_stats`, `http_client`. The `_lifespan` async context manager creates/closes the shared `httpx.AsyncClient` and `TokenDB`.

**Dual-format converter pattern:**
Each upstream format (openai, anthropic) has its own converter module set under `converters/`:
- `request.py` / `anthropic_request.py` — Responses API → upstream request
- `response.py` / `anthropic_response.py` — upstream response → Responses API
- `streaming.py` / `anthropic_streaming.py` — upstream SSE chunks → Responses API events
- `common.py` — shared: `gen_id()`, `STATUS_MAP`, `convert_usage()`

**Key conversion details in `converters/request.py`:**
- `input` string/array → `messages` array; `instructions` → prepended system message
- `role:"developer"` → `role:"system"`
- Tools: Responses API format is flat `{type,name,parameters}`, Chat Completions is nested `{type,function:{name,...}}`. `_convert_tools` handles both.
- Built-in tools (web_search, file_search, etc.) are filtered out via `ignored_builtin_tools` config
- `max_output_tokens` → `max_tokens`; `text.format` → `response_format`

**WebSocket handler (`ws_handler.py`):**
Codex sends `{"type":"response.create", ...}` with Responses API params. Handler strips the `type` field, resolves `previous_response_id`, converts, streams upstream, and sends each event back as JSON via WebSocket. Validates Origin header (localhost/127.0.0.1 only). Exports `build_upstream_headers()` and `build_upstream_url()` shared with `router.py`.

**Admin panel (`admin.py` + `static/admin.html`):**
Web management UI at `/admin/` with REST API for config, presets, stats, logs, system info, and config import/export. All endpoints restricted to localhost access (`_local_only` dependency checks `request.client.host`). API keys are masked in responses.

**Config manager (`config_manager.py`):**
Runtime preset management: CRUD operations on model presets, hot-swap via `activate_preset()` which applies preset values to runtime config + syncs Codex CLI + persists to config.yaml. Includes preset connectivity verification.

**Codex config management (`codex_config.py`):**
Uses `tomlkit` for safe structured config.toml modification. Backs up both `auth.json` and `config.toml` on startup, restores on exit via `atexit` + signal handlers. Uses atomic writes (`_atomic_write`) with restricted permissions for `auth.json`.

**Error handling (`errors.py`):**
`UpstreamError` for upstream API failures (502), `RequestValidationError` handler for 400s. Both return OpenAI-style error JSON.

## Key Gotchas

- Codex CLI connects via WebSocket first; if WS fails it falls back to HTTP POST. Both must work.
- Codex sends no `Sec-WebSocket-Protocol` header — accept WS without subprotocol negotiation.
- Responses API tools use flat format (`{type:"function", name:"shell"}`) not nested (`{type:"function", function:{name:"shell"}}`). The converter must handle both.
- `response_store.py` enables `previous_response_id` support — in-memory store (max 200 entries) that expands conversation history by chaining responses.
- Preset hot-swap via admin panel takes effect immediately without server restart.
- `streaming.py` uses a `StreamState` with a `finalized` flag to prevent duplicate `response.completed` events.
