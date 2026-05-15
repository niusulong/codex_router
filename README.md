# Codex Router

本地代理服务器，将 OpenAI Responses API (`/v1/responses`) 请求转换为 Chat Completions API (`/v1/chat/completions`) 请求，使 Codex CLI 能使用任何兼容 OpenAI Chat Completions 格式的模型服务（讯飞星辰、DeepSeek、通义千问等）。同时支持 Anthropic Messages API 格式。

## 功能特性

- **协议转换**：Responses API → Chat Completions API（OpenAI 格式 / Anthropic 格式）
- **管理面板**：内置 Web 管理界面，支持模型预设管理和实时切换
- **模型热切换**：通过管理面板一键切换上游模型服务商，无需重启
- **请求统计**：实时追踪请求数、延迟、成功率等指标
- **会话链接**：支持 `previous_response_id` 的对话上下文续接
- **双协议支持**：WebSocket + HTTP POST
- **自动配置**：启动时自动配置 Codex CLI，退出时恢复原始配置

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

启动时会自动配置 Codex CLI（修改 `~/.codex/auth.json` 和 `~/.codex/config.toml`），退出时自动恢复原始配置。启动后自动打开管理面板页面。

### 4. 使用 Codex CLI

正常启动 Codex CLI 即可，代理会自动拦截并转换请求。

## 管理面板

启动后访问 `http://127.0.0.1:8080/admin/` 打开 Web 管理面板（仅限本地访问）：

- **配置查看**：查看当前上游配置、模型覆盖等
- **预设管理**：添加、编辑、删除模型预设（不同的 API 地址 / Key / 模型）
- **模型热切换**：一键切换当前使用的预设，即时生效
- **连接验证**：验证预设的 API 连通性和延迟
- **请求统计**：查看请求数、成功率、平均延迟等实时指标
- **配置导入/导出**：导入导出预设和配置，方便迁移
- **系统信息**：查看 Python 版本、内存使用等运行信息

### 管理面板 API

所有管理接口均限制本地访问（`127.0.0.1` / `::1`）：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/admin/` | Web 管理界面 |
| GET | `/admin/api/config` | 获取当前配置 |
| PUT | `/admin/api/config` | 更新运行时配置 |
| GET | `/admin/api/presets` | 列出所有预设 |
| POST | `/admin/api/presets` | 添加预设 |
| PUT | `/admin/api/presets/{name}` | 更新预设 |
| DELETE | `/admin/api/presets/{name}` | 删除预设 |
| POST | `/admin/api/presets/{name}/activate` | 激活预设（热切换） |
| POST | `/admin/api/presets/{name}/verify` | 验证预设连通性 |
| GET | `/admin/api/stats` | 请求统计摘要 |
| GET | `/admin/api/stats/requests` | 最近请求列表 |
| GET | `/admin/api/stats/export` | 导出配置 |
| POST | `/admin/api/stats/import` | 导入配置 |
| GET | `/admin/api/logs` | 操作日志 |
| GET | `/admin/api/system` | 系统信息 |
| POST | `/admin/api/codex/restore` | 手动恢复 Codex CLI 配置 |

## 配置说明

### config.yaml

| 字段 | 说明 | 默认值 |
|------|------|--------|
| `upstream.base_url` | 上游 API 地址 | — |
| `upstream.api_key` | 上游 API Key | — |
| `upstream.timeout` | 请求超时（秒） | 120 |
| `upstream.api_format` | 上游 API 格式（`openai` 或 `anthropic`） | openai |
| `server.host` | 监听地址 | 127.0.0.1 |
| `server.port` | 监听端口 | 8080 |
| `model_override` | 模型名覆盖 | 不覆盖 |
| `passthrough_api_key` | 透传客户端 API Key | false |
| `ignored_builtin_tools` | 过滤的内置工具列表 | web_search 等 |
| `codex.auto_configure` | 自动配置 Codex CLI | true |
| `presets` | 模型预设列表 | [] |
| `active_preset` | 当前激活的预设名称 | — |

### 模型预设示例

```yaml
presets:
  - name: "DeepSeek"
    base_url: "https://api.deepseek.com/v1"
    api_key: "sk-xxx"
    model: "deepseek-chat"
    timeout: 120
  - name: "Qwen"
    base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
    api_key: "sk-yyy"
    model: "qwen-plus"
    timeout: 120
  - name: "Claude"
    base_url: "https://api.anthropic.com"
    api_key: "sk-ant-zzz"
    model: "claude-sonnet-4-20250514"
    api_format: "anthropic"
    timeout: 120
active_preset: "DeepSeek"
```

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

- 内置工具（`web_search`、`file_search`、`code_interpreter`、`mcp`）被过滤
- `input_image` 的 `file_id` 方式转为 `file://` URL，兼容性取决于上游
