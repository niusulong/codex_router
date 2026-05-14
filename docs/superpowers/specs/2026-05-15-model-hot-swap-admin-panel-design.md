# Codex Router — 模型热切换与 Web 管理面板 设计文档

> 基于 `docs/模型热切换与管理面板需求方案.md`，经过可行性分析与优化决策后的最终设计。

## 1. 决策记录

| 决策点 | 选择 | 理由 |
|--------|------|------|
| Config 访问模式 | 架构重构 → `app.state.config` 动态读取 | 显式、可维护，避免隐式闭包依赖 |
| Web UI 方案 | 独立 HTML 模板文件 + Pico CSS (CDN) | 可维护、IDE 支持好、零构建依赖 |
| 并发写入策略 | 内存为唯一真相来源 + 原子写入 | 本地工具无重并发，简单可靠 |
| Timeout 处理 | 每次请求传 timeout 参数 | 零切换开销，无需管理 client 生命周期 |
| 操作日志 | Python logging + 最小化内存日志 | logging 记录到终端，ConfigManager 维护 deque(maxlen=50) 供 UI 折叠区域展示 |
| 实现路径 | 路径 1：完整分层实现 | ConfigManager 独立可测试，admin.py 保持轻量 |

## 2. 整体架构

```
┌──────────────────────────────────────────────────────┐
│                    FastAPI App                        │
│                                                      │
│  ┌───────────┐  ┌──────────┐  ┌───────────────────┐  │
│  │ /v1/      │  │ /admin/  │  │ /admin/api/       │  │
│  │ responses │  │ (Web UI) │  │ (管理 REST API)   │  │
│  │ (代理)    │  │          │  │                   │  │
│  └─────┬─────┘  └──────────┘  └────────┬──────────┘  │
│        │          HTML模板加载          │              │
│        │                               │              │
│        ▼                               ▼              │
│  ┌─────────────────────────────────────────────┐      │
│  │         app.state.config_manager            │      │
│  │  (ConfigManager 单例)                       │      │
│  │                                             │      │
│  │  内存状态 (唯一真相来源):                    │      │
│  │  - presets: dict[str, PresetConfig]          │      │
│  │  - active_preset: str | None                 │      │
│  │  - config: ProxyConfig (可变引用)            │      │
│  │                                             │      │
│  │  持久化: config.yaml (原子写入 temp+rename)  │      │
│  │  Codex同步: auth.json + config.toml          │      │
│  └─────────────────────────────────────────────┘      │
│                                                      │
│  app.state.config ─── ProxyConfig 可变引用            │
│  app.state.http_client ─── httpx.AsyncClient          │
└──────────────────────────────────────────────────────┘
```

### 请求流（代理侧）

1. 请求进来 → 从 `request.app.state.config` 动态读取最新 ProxyConfig
2. 构建 upstream URL / headers → `client.send(req, timeout=httpx.Timeout(config.upstream.timeout))` 转发
3. timeout 逐请求传入，不依赖 client 创建时的固定值

### 切换流（管理侧）

1. API 调用 `POST /admin/api/presets/{name}/activate`
2. ConfigManager 验证预设存在 → 更新 `config.upstream.*` + `config.model_override`
3. 调用 `configure_codex(config)` 同步 `~/.codex/auth.json` + `config.toml`
4. 原子写入 `config.yaml`
5. `logger.info("切换预设: X → Y")`

## 3. 文件变更清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `codex_router/config.py` | 修改 | 新增 `PresetConfig`，`ProxyConfig` 加 `presets`/`active_preset`，新增 `save_to_file()` |
| `codex_router/config_manager.py` | **新增** | ConfigManager：预设 CRUD、热切换、持久化、Codex 同步 |
| `codex_router/admin.py` | **新增** | 管理 API 路由 + Web UI 模板加载 |
| `codex_router/static/admin.html` | **新增** | Web UI 独立 HTML 文件（Pico CSS + 原生 JS） |
| `codex_router/router.py` | 修改 | 闭包 → `app.state.config` 动态读取 + 逐请求 timeout |
| `codex_router/ws_handler.py` | 修改 | 同上 |
| `codex_router/main.py` | 修改 | 初始化 ConfigManager、注册 admin 路由 |

## 4. 数据模型

### PresetConfig（新增）

```python
class PresetConfig(BaseModel):
    name: str
    base_url: str
    api_key: str
    model: str
    timeout: float = 120.0
    created_at: Optional[float] = None
```

### ProxyConfig 扩展

```python
class ProxyConfig(BaseSettings):
    # ... 现有字段不变 ...
    presets: list[PresetConfig] = []
    active_preset: Optional[str] = None

    def save_to_file(self, path: Path) -> None:
        """序列化为 YAML，原子写入 (temp + rename)"""
```

### 向后兼容

- 无 `presets`/`active_preset` 的旧配置文件正常加载
- ConfigManager 初始化时检测空 `presets`，自动从当前 `upstream` + `model_override` 生成 "default" 预设

## 5. ConfigManager

核心组件，协调所有配置操作。

### 接口

```python
class ConfigManager:
    def __init__(self, config: ProxyConfig, config_path: Path)

    # 预设 CRUD
    def list_presets(self) -> list[PresetConfig]
    def get_preset(self, name: str) -> PresetConfig | None
    async def add_preset(self, preset: PresetConfig) -> None
    async def update_preset(self, name: str, updates: dict) -> PresetConfig
    async def delete_preset(self, name: str) -> None

    # 热切换
    async def activate_preset(self, name: str) -> None

    # 连接验证
    async def verify_preset(self, name: str) -> VerifyResult

    # 持久化
    async def save_config(self) -> None

    # 操作日志
    def get_logs(self, limit: int = 50) -> list[LogEntry]

    # 属性
    @property
    def config(self) -> ProxyConfig
    @property
    def active_preset_name(self) -> str | None
```

### 关键行为

**`_init_presets()`：** 启动时从 `config.presets` 加载到内部 dict。为空时自动生成 "default" 预设。

**`activate_preset(name)`：** 原子切换，按序执行：
1. 验证预设存在
2. 更新 `config.upstream.base_url` / `api_key` / `timeout`
3. 更新 `config.model_override`
4. 更新 `config.active_preset` 和内部 `_active_preset`
5. 调用 `configure_codex(config)` 同步 Codex CLI
6. `save_config()` 持久化
7. `logger.info("切换预设: %s → %s", old, new)`

**`update_preset(name, updates)`：** 如果编辑的是活跃预设，自动触发与 activate 相同的同步逻辑（更新 config + Codex CLI + 持久化）。

**`verify_preset(name)`：** 用独立 `httpx.AsyncClient`（timeout=10s）发送最小化 chat completion：
```python
{"model": preset.model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1, "stream": False}
```
返回 `VerifyResult(ok, message, latency_ms)`。

**`save_config()`：** 从 ConfigManager 内部 dict 生成 `config.presets` list，序列化 ProxyConfig 为 YAML dict，通过 `temp + rename` 原子写入。

**对象引用保证：** ConfigManager 持有的 `self._config` 与 `app.state.config` 是**同一个 ProxyConfig 对象引用**。main.py 初始化时确保 `cm.config is app.state.config`。

**presets 双数据结构同步：** ConfigManager 内部用 `dict[str, PresetConfig]` 做快速查找。`save_config()` 时从 dict 生成 list 写回 `config.presets` 再序列化。所有预设变更（add/update/delete/activate）在 dict 操作后立即调用 `save_config()` 同步。

**操作日志：** ConfigManager 维护 `deque(maxlen=50)` 记录操作历史（切换、添加、编辑、删除），每条包含 `timestamp`、`action`、`detail`。同时通过 `logger.info()` 输出到终端。

## 6. Router & WebSocket 改造

### router.py

```python
# 修改前
def create_router(config: ProxyConfig) -> APIRouter: ...

# 修改后
def create_router() -> APIRouter:
    router = APIRouter()

    @router.post("/v1/responses")
    async def create_response(request: Request):
        config: ProxyConfig = request.app.state.config
        # ... 后续逻辑不变，config 读取点自动拿到最新值

    @router.websocket("/v1/responses")
    async def ws_responses(ws: WebSocket):
        config: ProxyConfig = ws.app.state.config
        await handle_websocket(ws, config)
```

**timeout 传递：** 在 `create_response` 中构建 timeout 后传入 `_handle_streaming` / `_handle_non_streaming`：
- `_handle_non_streaming` 中 `client.post(url, json=..., headers=..., timeout=config.upstream.timeout)`
- `_handle_streaming` 中 `client.stream("POST", url, json=..., headers=..., timeout=config.upstream.timeout)`
- 两个辅助函数的签名和内部逻辑不变，timeout 通过 httpx 方法参数传入

### ws_handler.py

`handle_websocket` 和 `_handle_response_create` 去掉 `config` 参数，统一从 `ws.app.state.config` 获取：

```python
async def handle_websocket(ws: WebSocket):
    await ws.accept()
    while True:
        event = json.loads(await ws.receive_text())
        config: ProxyConfig = ws.app.state.config  # 每次事件循环获取最新配置
        if event["type"] == "response.create":
            await _handle_response_create(ws, event, config)

async def _handle_response_create(ws, event, config):
    # config 由调用方传入最新引用，不再覆盖
    # ... 后续逻辑不变
    # timeout 通过 client.stream(..., timeout=config.upstream.timeout) 传入
```

### main.py

```python
def create_app(config=None):
    ...
    config, config_path = load_config()  # load_config 返回 (ProxyConfig, Path | None)
    cm = ConfigManager(config, config_path)
    app.state.config = config           # 同一个对象引用
    app.state.config_manager = cm       # cm.config is app.state.config
    app.include_router(create_router())
    app.include_router(create_admin_router())
```

**`load_config` 改动：** 返回类型从 `ProxyConfig` 改为 `tuple[ProxyConfig, Path | None]`，同时返回解析到的配置文件路径。

**lifespan 中 AsyncClient 创建：** 保留 `timeout=config.upstream.timeout` 作为兜底超时，逐请求 timeout 会覆盖此值。

## 7. Admin API

所有管理接口挂载在 `/admin/api/`，由 `admin.py` 中的 `create_admin_router()` 创建。

### 接口列表

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/admin/` | 管理面板 HTML 页面 |
| `GET` | `/admin/api/config` | 当前运行时配置（api_key 脱敏） |
| `GET` | `/admin/api/presets` | 预设列表（api_key 脱敏） |
| `POST` | `/admin/api/presets` | 添加预设 |
| `PUT` | `/admin/api/presets/{name}` | 编辑预设 |
| `DELETE` | `/admin/api/presets/{name}` | 删除预设（活跃预设不可删） |
| `POST` | `/admin/api/presets/{name}/activate` | 切换到指定预设 |
| `POST` | `/admin/api/presets/{name}/verify` | 验证预设连接可用性 |
| `PUT` | `/admin/api/settings` | 更新通用配置 |
| `GET` | `/admin/api/logs` | 获取最近操作日志 |

### API Key 脱敏与回传

```python
def _mask_key(key: str) -> str:
    if len(key) <= 8:
        return "****"
    return key[:4] + "****" + key[-4:]
```

**编辑预设时的 API Key 保护：** 后端在 `PUT /admin/api/presets/{name}` 中检测 `api_key` 字段：
- 如果值为空或包含 `****`（脱敏格式），则保留原值不更新 api_key
- 只有传入完整的非脱敏 key 才更新
- 前端表单中 API Key 输入框 placeholder 显示脱敏值，实际 value 为空，提示"留空则保留原密钥"

### `PUT /admin/api/settings`

请求体：
```json
{
  "ignored_builtin_tools": ["web_search_preview", "file_search"],
  "timeout": 120,
  "auto_configure": true
}
```

所有字段可选，只传需要修改的字段。响应：`{"ok": true, "message": "配置已更新"}`。

### 错误处理

- 预设不存在 → 404 `{"ok": false, "message": "预设 XXX 不存在"}`
- 删除活跃预设 → 400 `{"ok": false, "message": "不能删除当前活跃预设"}`
- 预设名重复 → 409 `{"ok": false, "message": "预设名 XXX 已存在"}`

### Admin API 访问控制

`create_admin_router()` 中添加 FastAPI 依赖，检查请求来源 IP：
```python
async def _local_only(request: Request):
    if request.client.host not in ("127.0.0.1", "::1"):
        raise HTTPException(403, "管理接口仅限本地访问")
```
所有 `/admin/api/*` 路由使用 `dependencies=[Depends(_local_only)]`。即使 Router 绑定 `0.0.0.0`，管理接口仍仅限本地访问。

## 8. Web UI

### 技术方案

- **位置：** `codex_router/static/admin.html`（独立文件）
- **CSS：** Pico CSS via CDN（`<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css">`）
- **JS：** 原生 `fetch()` 调用 Admin API
- **模板加载：** `admin.py` 启动时 `Path(__file__).parent / "static" / "admin.html"` 读取文件内容，缓存到模块变量，请求时返回

### 页面布局

```
┌──────────────────────────────────────────────────────┐
│  Codex Router 管理面板              [状态: 运行中 ●]  │
├──────────────────────────────────────────────────────┤
│                                                      │
│  ┌─ 当前配置 ──────────────────────────────────────┐ │
│  │  预设: DeepSeek     模型: deepseek-chat          │ │
│  │  上游: https://api.deepseek.com/v1              │ │
│  │  密钥: sk-x****abcd    超时: 120s               │ │
│  └─────────────────────────────────────────────────┘ │
│                                                      │
│  ┌─ 模型预设 ──────────────────────────────────────┐ │
│  │  [名称]  [上游地址]      [模型]        [操作]    │ │
│  │  ★ Deep  api.deepseek..  ds-chat       编辑|删除 │ │
│  │    Qwen  dashscope.ali.  qwen-plus  验证|切换    │ │
│  │    GLM   open.bigmodel.  glm-4      验证|切换    │ │
│  │                                                  │ │
│  │  [+ 添加预设]                                    │ │
│  └─────────────────────────────────────────────────┘ │
│                                                      │
│  ┌─ 通用配置 ──────────────────────────────────────┐ │
│  │  过滤工具: [web_search_preview] [file_search]... │ │
│  │  自动配置Codex: [✓]    超时: [120]s             │ │
│  │                                        [保存]    │ │
│  └─────────────────────────────────────────────────┘ │
│                                                      │
│  ┌─ 操作日志 ▼ (折叠) ────────────────────────────┐ │
│  │  10:30:15 切换预设: DeepSeek → Qwen             │ │
│  │  10:30:14 验证预设 Qwen 连接成功 (320ms)         │ │
│  │  10:25:00 添加预设: GLM                          │ │
│  └─────────────────────────────────────────────────┘ │
│                                                      │
└──────────────────────────────────────────────────────┘
```

### 交互流程

- **切换预设：** 点击"切换" → 后台调 verify → 行内显示验证结果（成功+延迟 / 失败+原因） → 用户确认 → 调 activate → 刷新当前配置区 + 预设列表活跃标记
- **添加/编辑预设：** Pico CSS `<dialog>` 模态框 → 填表单 → POST/PUT → 刷新列表
- **删除预设：** `confirm()` 确认 → DELETE → 刷新列表
- **通用配置：** 页面内表单 → PUT `/admin/api/settings` → 行内提示"已保存"
- **操作日志：** 默认折叠的 `<details>` 区域，展开时 `fetch("/admin/api/logs")` 加载最近操作记录，自动滚动到最新

### JS 实现要点

- 页面加载时并行 `fetch("/admin/api/config")` + `fetch("/admin/api/presets")` 获取初始数据
- 渲染函数：`renderCurrentConfig()`、`renderPresetList()`、`renderSettings()`、`renderLogs()`
- 所有操作通过 `fetch()` 调用 API，完成后局部刷新对应 DOM
- 验证结果用行内提示展示，不弹 alert
- 编辑预设表单中 API Key 输入框为空（placeholder 显示脱敏值），提示"留空保留原密钥"
- 无需任何构建工具

## 9. 安全性

| 项目 | 措施 |
|------|------|
| 访问限制 | Admin API 应用层检查 `request.client.host`，仅允许 127.0.0.1/::1 |
| API Key 脱敏 | API 响应中仅显示前4位+后4位，中间 `****`；编辑时脱敏值不覆盖原 key |
| API Key 存储 | 明文存储在 config.yaml（与现有行为一致） |
| 无认证 | 本地工具，暂不实现登录认证 |
| 外部文件修改 | 内存为真相来源，运行期间外部修改 config.yaml 会在下次 save 时被覆盖 |
| 离线场景 | Pico CSS CDN 不可用时 UI 无样式但仍可正常使用 |

## 10. 非功能需求

| 项目 | 要求 |
|------|------|
| 配置读取延迟 | 从 app.state.config 读取 < 1μs |
| 持久化开销 | 异步写入，不阻塞请求 |
| UI 首次加载 | < 500ms |
| 验证超时 | 10s |
| 原子写入 | temp + rename |
| 向后兼容 | 无 presets 字段的旧配置自动生成 default 预设 |
| 无新外部依赖 | Pico CSS 通过 CDN 加载，不引入 Python 包 |
