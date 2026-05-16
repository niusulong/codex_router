# Codex Router

本地代理服务器，将 OpenAI Responses API 转换为 Chat Completions API，使 Codex CLI 能使用任何兼容 OpenAI Chat Completions 格式的模型服务（DeepSeek、通义千问、讯飞星辰等）。同时支持 Anthropic Messages API 格式。

## 功能特性

- **协议转换**：Responses API → Chat Completions API（OpenAI / Anthropic 格式）
- **管理面板**：内置 Web 管理界面，支持模型预设管理和实时切换
- **模型热切换**：通过管理面板一键切换上游模型服务商，无需重启
- **请求统计**：实时追踪请求数、延迟、成功率、Token 用量等指标
- **会话链接**：支持 `previous_response_id` 的对话上下文续接
- **双协议支持**：WebSocket + HTTP POST
- **自动配置**：启动时自动配置 Codex CLI，退出时恢复原始配置

## 安装

```bash
pip install codex-router-proxy-proxy
```

从源码安装（开发模式）：

```bash
pip install -e .
```

## 配置

复制示例配置文件并填入你的上游 API 信息：

```bash
cp config.yaml.example config.yaml
```

编辑 `config.yaml`，设置上游 API 地址、密钥和模型：

```yaml
upstream:
  base_url: "https://api.deepseek.com/v1"
  api_key: "sk-xxx"
  timeout: 120
  # api_format: "openai"  # 默认 openai，Anthropic 格式设为 "anthropic"

server:
  host: "127.0.0.1"
  port: 8080

# 模型名称覆盖（可选）
model_override: "deepseek-chat"
```

所有配置项可通过环境变量覆盖，前缀 `CODEX_ROUTER_`，嵌套分隔符 `__`：

```bash
CODEX_ROUTER_UPSTREAM__BASE_URL=https://api.deepseek.com/v1
CODEX_ROUTER_UPSTREAM__API_KEY=sk-xxx
CODEX_ROUTER_SERVER__PORT=9090
```

## 启动

```bash
python -m codex_router
```

或使用安装后的命令：

```bash
codex-router-proxy
```

查看版本：

```bash
codex-router-proxy --version
```

代理默认监听 `http://127.0.0.1:8080`。启动时会自动配置 Codex CLI（修改 `~/.codex/auth.json` 和 `~/.codex/config.toml`），退出时自动恢复原始配置。启动后自动打开管理面板页面。

## 使用 Codex CLI

正常启动 Codex CLI 即可，代理会自动拦截并转换请求。Codex CLI 通过 WebSocket 或 HTTP POST 发送 Responses API 请求到代理，代理将其转换为 Chat Completions API 请求转发给上游服务。

## 管理面板

启动后访问 `http://127.0.0.1:8080/admin/` 打开 Web 管理面板（仅限本地访问）：

- **配置查看**：查看当前上游配置、模型覆盖等
- **预设管理**：添加、编辑、删除模型预设（不同的 API 地址 / Key / 模型）
- **模型热切换**：一键切换当前使用的预设，即时生效
- **连接验证**：验证预设的 API 连通性和延迟
- **请求统计**：查看请求数、成功率、平均延迟、Token 用量等实时指标
- **配置导入/导出**：导入导出预设和配置，方便迁移

## 模型预设

在 `config.yaml` 中预定义多套上游配置，通过管理面板一键切换：

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

## 版本管理

版本号定义在 `codex_router/version.py` 中，为项目唯一来源。发布新版本只需修改该文件中的 `__version__` 值，`pyproject.toml` 会自动读取。

## 构建与发布

```bash
# 构建
pip install build
python -m build

# 发布到 PyPI
pip install twine
twine upload dist/*
```

## 已知限制

- 内置工具（`web_search`、`file_search`、`code_interpreter`、`mcp`）被过滤
- `input_image` 的 `file_id` 方式转为 `file://` URL，兼容性取决于上游
