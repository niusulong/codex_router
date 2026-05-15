# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Does

A local proxy server that converts OpenAI Responses API (`/v1/responses`) requests to Chat Completions API (`/v1/chat/completions`) requests. This enables Codex CLI (which only supports the Responses API) to use any Chat Completions-compatible model provider (e.g., iFlytek, DeepSeek, Qwen).

## Running

```bash
pip install -e .
python -m codex_router          # Start proxy (reads config.yaml from CWD)
```

The proxy auto-configures Codex CLI on startup by patching `~/.codex/auth.json` (API key) and `~/.codex/config.toml` (`openai_base_url`, `model`). Original files are restored on exit.

## Configuration

Primary config is `config.yaml`. Env vars (prefix `CODEX_ROUTER_`) override YAML values — see `.env.example`. Key fields: `upstream.base_url`, `upstream.api_key`, `model_override`, `codex.auto_configure`.

## Architecture

The proxy serves `/v1/responses` via both **HTTP POST** and **WebSocket**. Codex CLI prefers WebSocket (Realtime API protocol), falling back to HTTP POST.

**Request flow (both protocols):**
1. Parse Responses API request → `models.py` (Pydantic models)
2. Convert to Chat Completions request → `converters/request.py`
3. Forward to upstream via httpx → `router.py` (HTTP) or `ws_handler.py` (WebSocket)
4. Convert upstream response back → `converters/response.py` (non-streaming) or `converters/streaming.py` (streaming SSE→Responses events)

**Key conversion details in `converters/request.py`:**
- `input` string/array → `messages` array; `instructions` → prepended system message
- `role:"developer"` → `role:"system"`
- Tools: Responses API format is flat `{type,name,parameters}`, Chat Completions is nested `{type,function:{name,...}}`. `_convert_tools` handles both formats.
- Built-in tools (web_search, file_search, etc.) are filtered out via `ignored_builtin_tools` config
- `max_output_tokens` → `max_tokens`; `text.format` → `response_format`

**Streaming converter (`converters/streaming.py`):**
Stateful async generator with `StreamState` tracking open message/function_call items. Consumes Chat Completions SSE chunks, emits Responses API SSE events. Uses `finalized` flag to prevent duplicate `response.completed` events.

**WebSocket handler (`ws_handler.py`):**
Codex sends `{"type":"response.create", ...}` with Responses API params directly. Handler strips the `type` field, parses as `ResponsesRequest`, converts, streams upstream, and sends each event back as JSON via WebSocket. Validates Origin header (allows localhost/127.0.0.1 only). Also exports `build_upstream_headers()` and `build_upstream_url()` shared with `router.py`.

**Shared utilities (`converters/common.py`):**
`gen_id(prefix)` for ID generation, `STATUS_MAP` for finish_reason→status mapping, `convert_usage()` for token usage conversion, and ID prefix constants (`RESPONSE_ID_PREFIX`, `MESSAGE_ID_PREFIX`, `FUNCTION_CALL_ID_PREFIX`, `CALL_ID_PREFIX`).

**Codex config management (`codex_config.py`):**
Uses `tomlkit` for safe structured config.toml modification. Backs up both files to memory + disk on startup, restores on exit via `atexit` + signal handlers. Uses atomic writes (`_atomic_write`) with `0o600` permissions for `auth.json`.

## Key Gotchas

- Codex CLI connects via WebSocket first; if WS fails it fallsbacks to HTTP POST. Both must work.
- Codex sends no `Sec-WebSocket-Protocol` header — accept WS without subprotocol negotiation.
- Responses API tools use flat format (`{type:"function", name:"shell"}`) not nested (`{type:"function", function:{name:"shell"}}`). The converter must handle both.
- `previous_response_id` is unsupported (stateful feature) — logged as warning, ignored.
- `config.yaml` contains the upstream API key — do not commit to public repos.