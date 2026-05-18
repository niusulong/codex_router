# Codex Router

让 [Codex CLI](https://github.com/openai/codex) 使用任何大模型服务的本地代理。

Codex CLI 默认只支持 OpenAI 官方 API。Codex Router 在本地启动一个代理服务器，将 Codex 发出的请求自动转换为你的模型服务商支持的格式，让你可以用 DeepSeek、通义千问、讯飞星辰、Claude 等任何模型来驱动 Codex CLI。

## 支持哪些模型

只要是兼容以下任一 API 格式的服务商都可以：

- **OpenAI Chat Completions API** — DeepSeek、通义千问、讯飞星辰、Groq 等
- **Anthropic Messages API** — Claude 系列

## 快速开始

### 1. 安装

```bash
pip install codex-router-proxy
```

已安装旧版本时，需要加 `--upgrade` 才会更新到最新版：

```bash
pip install codex-router-proxy --upgrade
```

### 2. 创建配置文件

在工作目录下创建 `config.yaml`，填入你的模型服务商信息：

```yaml
upstream:
  base_url: "https://api.deepseek.com/v1"
  api_key: "sk-xxx"
  timeout: 120

server:
  host: "127.0.0.1"
  port: 8080

model_override: "deepseek-chat"
```

`upstream.api_format` 默认为 `openai`。使用 Claude 时改为 `anthropic`：

```yaml
upstream:
  base_url: "https://api.anthropic.com"
  api_key: "sk-ant-xxx"
  api_format: "anthropic"

model_override: "claude-sonnet-4-20250514"
```

### 3. 启动代理

```bash
codex_router              # 默认端口 8080
codex_router 9090         # 指定端口
```

启动后会自动配置 Codex CLI 并打开管理面板。

### 4. 使用 Codex CLI

正常启动 Codex CLI 即可，无需额外配置。代理会自动拦截并转换请求。

## 管理面板

启动后浏览器会自动打开 `http://127.0.0.1:8080/admin/`（仅限本地访问），可以：

- **一键切换模型** — 配置多个服务商预设，随时切换，无需重启
- **查看请求统计** — 请求数、成功率、延迟、Token 用量
- **验证连通性** — 测试预设的 API 是否可用
- **导入/导出配置** — 方便在多台机器间迁移

## 多模型预设

在 `config.yaml` 中预定义多个服务商，启动后通过管理面板一键切换：

```yaml
presets:
  - name: "DeepSeek"
    base_url: "https://api.deepseek.com/v1"
    api_key: "sk-xxx"
    model: "deepseek-chat"
  - name: "Qwen"
    base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
    api_key: "sk-yyy"
    model: "qwen-plus"
  - name: "Claude"
    base_url: "https://api.anthropic.com"
    api_key: "sk-ant-zzz"
    model: "claude-sonnet-4-20250514"
    api_format: "anthropic"

active_preset: "DeepSeek"
```

## 环境变量

所有配置项都支持环境变量覆盖，前缀 `CODEX_ROUTER_`，层级用 `__` 分隔：

```bash
CODEX_ROUTER_UPSTREAM__BASE_URL=https://api.deepseek.com/v1
CODEX_ROUTER_UPSTREAM__API_KEY=sk-xxx
CODEX_ROUTER_SERVER__PORT=9090
```
