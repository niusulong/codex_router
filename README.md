# Codex Router

本地代理服务器，将 OpenAI Responses API (`/v1/responses`) 请求转换为 Chat Completions API (`/v1/chat/completions`) 请求，使 Codex CLI 能使用任何兼容 OpenAI Chat Completions 格式的模型服务（讯飞星辰、DeepSeek、通义千问等）。

## 快速开始

### 1. 安装

```bash
pip install -e .
```

### 2. 配置

复制示例配置文件并填入你的上游 API 信息：

```bash
cp config.yaml.example config.yaml
```

编辑 `config.yaml`，设置 `upstream.base_url`、`upstream.api_key` 和 `model_override`。

### 3. 启动

```bash
python -m codex_router
```

代理默认监听 `http://127.0.0.1:8080`。

启动时会自动配置 Codex CLI（修改 `~/.codex/auth.json` 和 `~/.codex/config.toml`），退出时自动恢复原始配置。

### 4. 使用 Codex CLI

正常启动 Codex CLI 即可，代理会自动拦截并转换请求。

## 配置说明

### config.yaml

| 字段 | 说明 | 默认值 |
|------|------|--------|
| `upstream.base_url` | 上游 API 地址 | — |
| `upstream.api_key` | 上游 API Key | — |
| `upstream.timeout` | 请求超时（秒） | 120 |
| `server.host` | 监听地址 | 127.0.0.1 |
| `server.port` | 监听端口 | 8080 |
| `model_override` | 模型名覆盖 | 不覆盖 |
| `passthrough_api_key` | 透传客户端 API Key | false |
| `ignored_builtin_tools` | 过滤的内置工具列表 | web_search 等 |
| `codex.auto_configure` | 自动配置 Codex CLI | true |

### 环境变量覆盖

所有配置项可通过环境变量覆盖，前缀 `CODEX_ROUTER_`，嵌套分隔符 `__`：

```bash
CODEX_ROUTER_UPSTREAM__BASE_URL=https://api.deepseek.com/v1
CODEX_ROUTER_UPSTREAM__API_KEY=sk-xxx
CODEX_ROUTER_SERVER__PORT=9090
```

## 支持的协议

- **WebSocket**（Codex CLI 优先使用）：`ws://127.0.0.1:8080/v1/responses`
- **HTTP POST**（回退）：`http://127.0.0.1:8080/v1/responses`

## 已知限制

- `previous_response_id` 不支持（无状态代理）
- 内置工具（`web_search`、`file_search`、`code_interpreter`、`mcp`）被过滤
- `input_image` 的 `file_id` 方式转为 `file://` URL，兼容性取决于上游
