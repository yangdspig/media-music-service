# 音乐/有声读物统一搜索下载服务设计方案

- 文档状态：设计已获用户认可；M1（核心服务）、M2（MCP 适配器）、M3（部署与文档）已完成并验证，M4 为可选增强（见 ROADMAP.md）
- 创建日期：2026-08-07
- 决策记录：架构形态 = 独立服务型 + MoviePilot 薄客户端；使用范围 = 纯内网自用（鉴权从简，默认不启用）
- 上游依赖：`CharlesPikachu/musicdl`（PyPI `musicdl>=2.13.4,<3.0`，锁定主版本防大版本不兼容升级），仅作为 Python 库复用，不 fork 源码
- 维护口径：平台接口适配由 musicdl 作者持续维护，本服务只需例行 `pip install -U musicdl` 升级并回归 M1 主链路，无需自行分析底层平台接口

## 1. 背景与目标

现有 MoviePilot 在影视资源自动化方面能力完善，但**无法搜索和下载音乐或有声读物**。`musicdl` 项目提供了覆盖国内外 56 个平台的音乐/有声读物搜索与下载能力（统一封装为 `MusicClient` 的 `search()` / `download()` / `parseplaylist()` 接口），但形态是"命令行工具 + Python 库"，缺少可供其他系统长期调用的服务化封装。

本方案目标是：基于 `musicdl` 的底层能力，构建一套**独立部署、长期运行的搜索下载服务**，同时向上暴露两类接入方式：
- 作为 **MoviePilot 插件**（薄客户端），补齐 MoviePilot 的音乐/有声读物短板；
- 作为 **MCP 服务**，供各类 Agent（Claude、Trae 等）通过工具调用。

约束：纯内网自用，不做多用户/配额/复杂鉴权；优先跑通最小可用闭环，增强能力按需迭代。

## 2. 总体架构

整个系统由**一个核心服务 + 两类客户端**构成：

```
┌─────────────────────────────────────────────────────────────┐
│  MediaMusicService（独立部署，长期运行，Docker / 裸机均可）    │
│  ─────────────────────────────────────────────────────────  │
│  FastAPI (REST + SSE)                                       │
│    ├── /api/v1/search      统一搜索接口                      │
│    ├── /api/v1/playlist    歌单解析                          │
│    ├── /api/v1/downloads   下载任务（提交/查询/取消）         │
│    └── /api/v1/sources     可用源与能力清单                  │
│  核心引擎层（对 musicdl 的薄封装）                            │
│    ├── ClientRegistry   动态加载 MusicClientBuilder 的源     │
│    ├── SearchService    聚合多源搜索 + 结果标准化             │
│    └── DownloadManager  任务队列 + 线程池 + 落盘 + 元数据     │
│  存储：下载目录（宿主挂载）+ SQLite（任务/历史，极简）         │
└─────────────────────────────────────────────────────────────┘
              ▲ REST/SSE（局域网，可选 API Key）
   ┌──────────┴──────────┐
   │                     │
┌──┴──────────────┐  ┌───┴───────────────┐
│ MoviePilot 插件  │  │ Agent（Claude/Trae）│
│ （薄客户端）      │  │ （MCP 适配器）       │
│ _PluginBase     │  │ FastMCP stdio/SSE   │
│ get_form/get_page│ │ search/download/    │
│ 只做表单 + 调 API │  │ playlist 工具       │
└─────────────────┘  └───────────────────┘
```

### 关键取舍

1. **服务不强依赖 musicdl 自带的 MCP 示例**（`mcp/server_local.py`）。该示例只是个"能跑的 demo"（只封装 search/download、默认单源、结果直接落盘不可控、无任务管理）。本方案直接复用 `musicdl` 作为 Python 库，把 `MusicClient` 封装在自己的引擎层，MoviePilot 插件和 MCP 端走同一个 REST 服务，业务逻辑只有一份。
2. **下载是异步任务而非同步阻塞**。musicdl 的 `download()` 是长耗时操作，服务收到下载请求后返回 `task_id`，客户端轮询 REST 或订阅 SSE 获取进度，避免 HTTP 长连接超时。
3. **鉴权从简**。默认不启用任何认证；预留可选的 `X-API-Key` Header 中间件（配置项为空即关闭），防止同网段误触，不为公网暴露设计。

## 3. 核心服务设计

### 3.1 复用 musicdl 的方式

- 通过 PyPI 依赖 `musicdl>=2.13.4`，不 fork 代码，避免后续升级冲突。
- 用 `MusicClientBuilder.REGISTERED_MODULES` 枚举所有源，通过 `musicdl.musicdl.MusicClient(music_sources=[...], ...)` 实例化。
- 引擎层只做两件事：
  1. 把 musicdl 返回的 `SongInfo` dict 标准化为统一的 `Track` schema（字段：歌名、歌手、专辑、时长、音质/码率、文件格式、来源客户端、下载 URL、封面 URL、附加元数据）；
  2. 把 musicdl 的同步下载包装为带状态机的异步任务执行。

### 3.2 模块拆分

每个模块职责单一、可独立测试：

| 模块 | 职责 | 关键接口 |
|---|---|---|
| `registry.py` | 源注册与能力描述，维护每个源的元数据（分类、是否支持歌单、是否需要 cookies、是否需代理） | `list_sources()` |
| `search.py` | 关键词搜索，支持多源并发聚合、结果去重、按音质/来源排序 | `search(keyword, sources, limit)` |
| `playlist.py` | 歌单 URL 解析，仅对 musicdl 官方支持歌单的 22 个源开放 | `parse_playlist(url, source)` |
| `download.py` | 下载任务管理：内存任务队列、线程池执行、进度上报、失败重试、取消 | `submit() / status() / cancel()` |
| `storage.py` | 下载目录组织（默认 `来源/歌手/歌名` 规则，可配置）、SQLite 极简任务与历史记录表 | — |
| `config.py` | 单一 `config.yaml`：监听地址/端口、下载根目录、各源 cookies（QQ/网易云/TIDAL/夸克等）、线程数、可选 API Key | — |

### 3.3 错误处理与边界

- **单源失败不拖垮整体**：聚合搜索时捕获每个源的异常，降级返回其他可用源的结果，并在响应中附带 `failed_sources` 供客户端提示。
- **cookies 缺失前置暴露**：需要登录态的源（如 QQ 音乐 VIP、TIDAL、夸克网盘系）在 `/api/v1/sources` 接口里标记 `available: false` 并给出原因，而不是等搜索时才报错。
- **启动探活（可选）**：服务启动时可对配置的源做轻量探活，把当前网络环境下不可用的源（如海外平台）前置标记出来。
- **musicdl 协议差异**：下载同时覆盖 HTTP 直链与 HLS 流（后者依赖 musicdl 的 `HLSDownloader` 及外部工具 N_m3u8DL-RE），部署时需在镜像/环境中备齐依赖。

## 4. 客户端设计

### 4.1 MoviePilot 插件（薄客户端）

- 目录结构遵循 MoviePilot 插件规范：`app/plugins/MediaMusic/`（含继承 `_PluginBase` 的主类）。
- `get_form()`：提供服务地址、可选 API Key、默认搜索源、下载完成后是否通知、文件整理规则等配置表单。
- `get_page()`：Vuetify 前端页面，提供"搜索框 → 结果列表 → 勾选 → 提交下载"交互，以及下载任务列表与进度展示。
- **所有逻辑只做一件事**：调用核心服务的 REST API 并渲染结果，不直接依赖 musicdl，不自己实现搜索/下载逻辑。
- 与 MoviePilot 集成：通过其事件机制，在下载完成后触发媒体库整理（或将下载目录纳入 MoviePilot 的整理流程），让音乐像影视一样入库。

### 4.2 MCP 适配器

- 不直接使用 musicdl 自带的 `server_local.py`，而是新写一个对核心服务的薄封装（基于 FastMCP）。
- 暴露的工具（与核心服务能力一一对应）：
  - `search_tracks(keyword, sources?, limit?)` → 标准化 `Track` 列表；
  - `parse_playlist(url, source?)` → 歌单内 Track 列表；
  - `submit_download(tracks)` → 返回 `task_id`；
  - `get_download_status(task_id)` → 任务进度与结果。
- 传输方式：默认 `stdio`（本地 Agent 直接拉起），同时支持 SSE（远程 Agent 连接内网服务）。
- 收益：MoviePilot 插件与 Agent 看到**同一套数据结构与行为**，一次调试，两端受益。

## 5. 里程碑与验证方式

按最小闭环优先，分四个里程碑：

### M1：核心服务主链路跑通（最高优先级）
- 服务可启动，可配置 5 个默认源（咪咕、网易云、QQ、酷我、千千）。
- REST 搜索、提交下载、查询状态、文件落盘到指定目录全链路打通。
- **验证方式**：`curl /api/v1/search?keyword=周杰伦` 返回标准化结果；提交下载后目标目录出现带歌词和 ID3 标签的音频文件。

### M2：MCP 适配器可用
- Agent 可通过 MCP 工具完成"搜索 → 下载 → 查询状态"。
- **验证方式**：在对话中让 Agent 搜索一首歌并成功下载。

### M3：部署与文档完善
- README、配置说明、部署说明（含外部依赖如 N_m3u8DL-RE 的安装指引）齐备，服务可在他机按文档独立部署。

### M4：可选增强（后续迭代，按需追加）
- MoviePilot 薄客户端插件（搜索/下载/进度展示，与媒体库整理联动）；
- 歌单批量下载；
- 有声读物源（喜马拉雅、懒人听书、荔枝FM、蜻蜓FM）启用与验证；
- WhisperLRC 语音转歌词（依赖 faster-whisper）；
- 夸克网盘无损源 cookies 配置（Mitu/Buguyy/Yinyuedao/Gequbao）；
- 海外源代理支持（Spotify、YouTube Music 等）。

## 6. 范围外事项（本期不做）

- 多用户、权限隔离、配额管理；
- 公网暴露所需的完整鉴权与 HTTPS 体系；
- 引入 Celery/Redis 等重量级任务队列与消息中间件（内存队列足够）；
- 对 musicdl 源码的修改与维护（只消费其 PyPI 版本）。

## 7. 风险与说明

- **合规风险**：musicdl 项目声明仅供学习研究、禁止商用、禁止绕过付费墙/DRM。本服务定位个人内网使用，需在 README 与配置中保留该免责说明，不提供任何破解付费内容的能力。
- **源稳定性**：第三方下载站与聚合源存活率波动大，设计上已通过"单源失败降级 + 可用性前置标记"缓解，但不保证所有源长期可用。
- **外部依赖**：HLS 下载、Apple Music 解密等依赖外部命令行工具（N_m3u8DL-RE、Node.js 等），部署文档需明确环境要求。
