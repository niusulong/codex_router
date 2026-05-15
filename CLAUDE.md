# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Does

A local proxy server that converts OpenAI Responses API (`/v1/responses`) requests to Chat Completions API (`/v1/chat/completions`) requests or Anthropic Messages API (`/v1/messages`) requests. This enables Codex CLI (which only supports the Responses API) to use any Chat Completions-compatible model provider (e.g., iFlytek, DeepSeek, Qwen) or Anthropic-compatible provider (e.g., Claude).

## Running

```bash
pip install -e .
python -m codex_router          # Start proxy (reads config.yaml from CWD)
```

The proxy auto-configures Codex CLI on startup by patching `~/.codex/auth.json` (API key) and `~/.codex/config.toml` (`openai_base_url`, `model`). Original files are restored on exit. It also opens the admin panel in the browser automatically.

## Configuration

Primary config is `config.yaml`. Env vars (prefix `CODEX_ROUTER_`) override YAML values — see `.env.example`. Key fields: `upstream.base_url`, `upstream.api_key`, `upstream.api_format`, `model_override`, `codex.auto_configure`, `presets`, `active_preset`.

## Architecture

The proxy serves `/v1/responses` via both **HTTP POST** and **WebSocket**. Codex CLI prefers WebSocket (Realtime API protocol), falling back to HTTP POST.

**Request flow (both protocols):**
1. Parse Responses API request → `models.py` (Pydantic models)
2. Resolve `previous_response_id` → `response_store.py` (conversation chaining)
3. Convert to upstream format → `converters/request.py` (OpenAI) or `converters/anthropic_request.py` (Anthropic), selected by `upstream.api_format`
4. Forward to upstream via httpx → `router.py` (HTTP) or `ws_handler.py` (WebSocket)
5. Convert upstream response back → `converters/response.py` or `converters/anthropic_response.py` (non-streaming), `converters/streaming.py` or `converters/anthropic_streaming.py` (streaming SSE→Responses events)
6. Record stats → `stats.py`
7. Store response → `response_store.py`

**Key conversion details in `converters/request.py`:**
- `input` string/array → `messages` array; `instructions` → prepended system message
- `role:"developer"` → `role:"system"`
- Tools: Responses API format is flat `{type,name,parameters}`, Chat Completions is nested `{type,function:{name,...}}`. `_convert_tools` handles both formats.
- Built-in tools (web_search, file_search, etc.) are filtered out via `ignored_builtin_tools` config
- `max_output_tokens` → `max_tokens`; `text.format` → `response_format`

**Streaming converter (`converters/streaming.py`):**
Stateful async generator with `StreamState` tracking open message/function_call items. Consumes Chat Completions SSE chunks, emits Responses API SSE events. Uses `finalized` flag to prevent duplicate `response.completed` events.

**WebSocket handler (`ws_handler.py`):**
Codex sends `{"type":"response.create", ...}` with Responses API params directly. Handler strips the `type` field, resolves `previous_response_id`, converts, streams upstream, and sends each event back as JSON via WebSocket. Validates Origin header (allows localhost/127.0.0.1 only). Also exports `build_upstream_headers()` and `build_upstream_url()` shared with `router.py`. Records request stats.

**Response store (`response_store.py`):**
In-memory store (max 200 entries) for completed responses. `resolve()` expands `previous_response_id` by prepending previous input/output as conversation history. This provides basic stateful conversation chaining.

**Request stats (`stats.py`):**
`RequestStats` tracks request count, success/fail rate, latency, method (http/websocket) in a deque of max 200 entries. Provides `get_summary()` and `get_recent()` for the admin panel.

**Config manager (`config_manager.py`):**
Runtime preset management: CRUD operations on model presets, hot-swap via `activate_preset()` which applies preset values to runtime config + syncs Codex CLI + persists to config.yaml. Includes preset connectivity verification and operation logging.

**Admin panel (`admin.py` + `static/admin.html`):**
Web management UI at `/admin/` with REST API for config, presets, stats, logs, system info, and config import/export. All endpoints restricted to localhost access. API keys are masked in responses.

**Shared utilities (`converters/common.py`):**
`gen_id(prefix)` for ID generation, `STATUS_MAP` for finish_reason→status mapping, `convert_usage()` for token usage conversion, and ID prefix constants.

**Codex config management (`codex_config.py`):**
Uses `tomlkit` for safe structured config.toml modification. Backs up both files to memory + disk on startup, restores on exit via `atexit` + signal handlers. Uses atomic writes (`_atomic_write`) with `0o600` permissions for `auth.json`.

## Key Gotchas

- Codex CLI connects via WebSocket first; if WS fails it fallsback to HTTP POST. Both must work.
- Codex sends no `Sec-WebSocket-Protocol` header — accept WS without subprotocol negotiation.
- Responses API tools use flat format (`{type:"function", name:"shell"}`) not nested (`{type:"function", function:{name:"shell"}}`). The converter must handle both.
- `config.yaml` contains the upstream API key — do not commit to public repos.
- `upstream.api_format` determines the conversion path: `openai` uses Chat Completions converters, `anthropic` uses Anthropic converters.
- Preset hot-swap via admin panel takes effect immediately without restart.
- `response_store.py` enables `previous_response_id` support (was previously unsupported).
- Admin API endpoints are localhost-only — `_local_only` dependency checks `request.client.host`.
