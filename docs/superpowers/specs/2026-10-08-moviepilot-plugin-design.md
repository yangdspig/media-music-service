# MediaMusicService × MoviePilot V3 插件集成开发方案

> 状态：**方案已完成调研与撰写，用户决定暂缓实施**（2026-10-08，优先推进独立 Web UI；方案与调研结论留档备查，随时可重启）。调研日期 2026-10-08。
> 关联：docs/design.md §4.1（薄客户端既定方向）、ROADMAP M4-3。

## 一、调研结论（事实核对）

**版本现状**：MoviePilot V3 是当前主线（最新 release v3.1.2，2026-10-08 发布；v3.0.0 于 2026-08-27 发布；V2 已于 2026-08-31 停止支持）。**插件直接面向 V3 开发**（`system_version: ">=3.0.0"`），不看 V2 文档。用户本机 docker 环境需先确认是 `jxxghp/moviepilot-v3` 镜像（实施第 0 步验证）。

**插件机制要点**（来源：官方 Plugin_Development.md、宿主源码、MoviePilot-Plugins 仓库）：

- 插件与宿主同进程、同依赖环境运行，**无独立虚拟环境**；调用外部 REST 服务用宿主提供的 `app.sdk.network.AsyncRequestUtils`（HTTPX2 封装），这正是薄客户端的标准做法。
- 插件 ID = 主类名，目录 = 类名小写；V3 插件放 `plugins.v3/<plugin_id>/`，市场索引为 `package.v3.json`；第三方依赖声明在插件目录的 `pyproject.toml`（不提交 uv.lock，本插件零额外依赖，仅需标准库+宿主 SDK）。
- 基类 `app.sdk.plugin._PluginBase` 契约：`init_plugin(config)`（必须幂等可重复调用）、`get_state()`、`get_form()`（Vuetify JSON 配置表单，保存后宿主自动重调 init_plugin）、`get_page()`（详情页 JSON）、`get_api()`（动态路由，前缀 `/api/v1/plugin/<PluginID>/`，`auth:"bear"` 给前端页面用）、`stop_service()`。可选：`get_dashboard()`、`get_service()`、`get_render_mode()`（`"vuetify"` 或 `("vue","dist/assets")` 联邦模式）、`get_sidebar_nav()`（仅 Vue 模式，主界面左侧导航全页入口）、`get_command()`、`post_message()`。
- 页面交互机制：Vuetify JSON 模式下节点挂 `events.click.api` → 前端调用指定插件 API → **自动整页重取**；无自定义 JS 回调。复杂交互（勾选下载、进度轮询）需要 Vue 模块联邦模式（自构建前端产物，共享依赖有严格约束）。
- **重要发现：V3 宿主已内置音乐链**（v3.0.9 起音乐交互、v3.1.0 起 CUE 归档/指纹/TheAudioDB/LRCLIB/标签读写），官方 V3 市场**没有音乐下载/管理类插件**（空白生态位）。宿主提供歌词扩展点 `music_lyrics_candidates`（FAQ 21），后续可对接而非只做孤立页面。

**关键取舍确认**：维持 design.md §4.1 既定方向——插件=薄客户端，只做表单/页面/REST 转发，不依赖 musicdl，业务逻辑零重复。

## 二、总体架构

```
MoviePilot V3 (docker)                      MediaMusicService (docker，已有)
┌─────────────────────────────┐   REST     ┌──────────────────────────────┐
│ 插件 MediaMusicService       │ ─────────► │ FastAPI :8765                 │
│  get_form()   服务地址/密钥  │  X-API-Key │ 28 个端点（搜索/专辑/榜单/库/飞牛）│
│  get_page()   功能页面       │            └──────────────────────────────┘
│  get_api()    转发/聚合接口  │
│  get_dashboard() 任务概览    │
└─────────────────────────────┘
```

- 网络：两容器在同一 docker 网络走容器名（`http://music-service:8765`），或宿主机 IP；地址在插件表单配置。
- 鉴权：插件→服务走我们服务的 `api_key`（表单配置，可空）；宿主前端→插件 API 走 `auth:"bear"`（复用 MP 登录态）。

## 三、插件骨架与仓库组织

插件 ID 定为 `MediaMusicService`，目录 `mediamusicservice/`。

**仓库策略（建议）**：在本仓库新建 `moviepilot-plugin/` 目录承载插件源码与市场索引，按官方市场格式组织，便于今后发布：

```
moviepilot-plugin/
├── package.v3.json                  # 市场索引（name/version/icon/system_version>=3.0.0/history）
├── icons/MediaMusicService.png      # 插件图标（官方约定图标放仓库根 icons/）
└── plugins.v3/
    └── mediamusicservice/
        ├── __init__.py              # 主类 MediaMusicService(_PluginBase)
        ├── client.py                # 对我们 8765 服务的薄封装（AsyncRequestUtils）
        ├── pages.py                 # get_page() 页面 JSON 构造（Vuetify 模式）
        └── README.md
```

- `client.py`：唯一与宿主解耦的模块——所有对我们 REST 的调用集中在此，便于单测与复用（后续 2c/2d 新端点自动可用）。
- 零第三方依赖 → `pyproject.toml` 只需最小声明（甚至可省，依官方 CI 门禁实测决定）。
- 发布路径：本地开发用 `PLUGIN_LOCAL_REPO_PATHS` 指向本目录；稳定后可推到独立 GitHub 仓库，用户把仓库地址加进 `PLUGIN_MARKET` 即可在线安装（官方市场投稿另议，本阶段自建源即可）。

## 四、功能范围与页面设计

按我们已有的 28 个 REST 端点映射为插件的四个功能页 + 配置页 + 仪表盘：

**配置页（get_form）**：服务地址、API Key、默认搜索源（多选）、默认目标库（`list_libraries` 动态填充，存库名）、`max_size_mb` 默认、下载后自动归档开关、飞牛歌单同步开关（检测服务侧 `fnos_music` 是否配置）、通知开关。

**P1 功能页（Vuetify JSON + events.click.api 可覆盖）**：
1. **单曲搜索页**：关键词输入 → `GET /search` 结果表格（歌名/艺人/专辑/音质/源）→ 行内"下载"按钮（`events.click.api` → 插件 API 转发 `POST /downloads`，带默认库/自动归档）。
2. **下载任务页**：`GET /downloads` 任务列表（状态/进度/错误），行内"取消"；页级刷新按钮。配合 `get_dashboard()` 提供"进行中任务数 + 最近完成"仪表板卡片。
3. **榜单页**：`GET /charts` 目录 → 点榜单 → `GET /charts/{source}/{id}` 曲目表 → 行内/整表下载。
4. **专辑页**：`GET /albums/search` → 卡片列表 → 详情（`GET /albums/{id}` 曲目表+简介）→ "下载整张专辑"（`download_album`）→ 完成后"归档入库"（`archive_album`）。

**P2 功能页**：
5. **媒体库管理页**：库列表、歌词回填（`backfill_lyrics`，dry_run 预览→确认执行两步交互）、曲目替换（`replace_track`）、清理（`cleanup`，confirm 语义在 UI 层做二次确认）、单曲迁移（`migrate_singles`）。
6. **飞牛歌单页**：歌单列表/详情/建补/追加/搜索（六个 fnos 端点一一映射）。

**通知集成**：`get_service()` 注册低频轮询任务（如 2 分钟）对比任务状态变化，下载/归档完成时 `post_message()` 推送（标题含专辑/曲名，link 指向插件页）。

## 五、实施分期

### P1：插件骨架 + 单曲/榜单/专辑闭环（Vuetify JSON 模式）

| # | 任务 | 产出 |
|---|---|---|
| 0 | 验证本机 MP 为 V3（插件市场可浏览 V3 插件、`/api/v1/plugin/sidebar_nav` 存在）；若非 V3 则按官方指引换 `moviepilot-v3` 镜像 | 环境确认记录 |
| 1 | 建 `moviepilot-plugin/` 骨架 + `package.v3.json` + 最小 `__init__.py`（官方 §4 骨架改造），本机 docker 配 `PLUGIN_LOCAL_REPO_PATHS` + `PLUGIN_AUTO_RELOAD=true` 挂载联调 | 插件出现在已安装列表 |
| 2 | `client.py`：AsyncRequestUtils 封装我们全部 P1 所需端点；`get_form()` 配置页（地址/密钥/默认源/默认库/通知） | 表单可保存、连通性自检（init_plugin 里探活 `/health`，异常写日志+页面告警） |
| 3 | `get_api()`：P1 所需转发接口（search、downloads 提交/列表/取消、charts、albums 系列），全部 `auth:"bear"` | 插件 API 可 curl 通 |
| 4 | `get_page()`：单曲搜索页 + 下载任务页 + 榜单页 + 专辑页（Vuetify JSON + events.click.api） | 四页可用 |
| 5 | `get_dashboard()` 任务概览卡片 + `get_service()` 轮询 + `post_message()` 完成通知 | 仪表盘+通知生效 |
| 6 | E2E 验证：MP 界面完成"搜歌→下载→自动归档→入库可见"、"榜单→整表下载→归档→飞牛歌单同步"两条链路；补 README | 录屏/截图记录 |

### P2：媒体库管理 + 飞牛歌单页面
- 库管理页（回填/替换/清理/迁移）+ 飞牛歌单页；UI 层实现 dry_run 预览→确认的两步交互与 confirm 二次确认。

### P3（可选增强，另行立项）
- 评估 Vue 联邦模式重写高交互页面（多选批量下载、进度自动刷新、专辑页富展示）——见"方案选择"。
- 对接 V3 内置音乐链：注册歌词 provider（`music_lyrics_candidates`，复用我们服务的歌词回填能力）；评估归档产物接入宿主音乐刮削链。
- 发布到自建/官方插件市场。

## 六、关键技术细节与风险

1. **init_plugin 幂等**：读配置重建 client；连通性探活只做一次轻量 `/health`，失败不抛异常（置状态标志，页面显示告警）。
2. **长任务体验**：我们服务的下载是异步任务，插件页靠手动刷新/仪表盘轮询；Vuetify 模式无自动刷新，`get_dashboard` 的 `refresh` 配置项可让仪表盘卡片定时刷新（官方支持）。
3. **同步阻塞端点**：`parse_playlist` 与榜单曲目解析是同步长耗时，插件 API 转发需设足超时（60s+），P2 起考虑服务端异步化（与 ROADMAP 歌单异步化条目联动）。
4. **路径语义**：插件侧永远传"库名"（library/singles），不传裸路径；路径只在服务侧 config 与飞牛 path_map 存在。
5. **版本兼容**：`system_version: ">=3.0.0"`；只用官方文档化的 SDK 接口（`app.sdk.*`），不 import 宿主内部模块，降低升级脆性。
6. **风险**：
   - Vuetify JSON 模式的 `events`/`onxxx` 表达式语法官方文档不完整，个别交互（如输入框取词提交搜索）可能需要实测 MoviePilot-Frontend 渲染行为，必要时搜索页降级为"配置页存关键词→点按钮"或提前引入 Vue 联邦。
   - V3 插件契约以文档为准（V3 宿主部分源码未完全公开核对），P1-1 骨架落地时先验证 `get_api`/`get_form` 实跑行为。
   - 图标/索引 CI 门禁细节（尺寸、history 排序）以官方 Repository_Guide 为准，发布前核对。

## 七、交付物

- `moviepilot-plugin/` 插件源码 + 市场索引（本仓库内）
- 本机 docker 联调 compose/环境变量说明（`PLUGIN_LOCAL_REPO_PATHS`、`PLUGIN_AUTO_RELOAD`）
- 插件 README（安装/配置/页面说明）
- 主仓库文档更新：ROADMAP M4-3 标记、README/DEPLOY 补插件章节

## 八、方案选择（页面渲染模式）

- **方案 A（推荐）：分期策略**——P1/P2 用 Vuetify JSON 快速落地全部功能（零前端构建、官方最主流形态），P3 再按实际交互痛点评估 Vue 联邦重写个别页面。
- **方案 B：一步到位 Vue 联邦**——直接上 `get_render_mode()=("vue","dist/assets")` + `get_sidebar_nav()` 全页应用，交互体验最好（多选、自动刷新、侧栏入口），但需要引入 Vue3+Vite 前端构建链与联邦共享依赖约束，首版周期显著变长。
