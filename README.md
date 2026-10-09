# MediaMusicService

基于 [musicdl](https://github.com/CharlesPikachu/musicdl) 底层能力构建的**音乐/有声读物统一搜索下载服务**，面向纯内网自用场景。

- 对外提供 **REST API**（FastAPI）与 **MCP 工具**（FastMCP）两种接入方式
- 复用 musicdl 覆盖的 54 个平台源（网易云/QQ/酷狗/酷我/咪咕/千千/Spotify/Apple Music/喜马拉雅等）
- 下载为异步任务，支持进度查询与历史追溯
- 响应式 Web 管理界面：概览、搜索下载、任务中心、歌单解析、榜单、专辑、媒体库、系统配置和飞牛歌单；支持 QQ / 网易云扫码登录

## 合规声明

本服务仅供**个人学习与研究**使用，禁止商用。musicdl 本身遵循 PolyForm-Noncommercial 协议：不托管、不分发任何版权内容，不绕过付费墙/DRM。请在遵守各音乐平台条款与当地法律的前提下使用，下载内容须为你有权访问的资源。

## 架构

```
Agent (Claude/Trae) ──MCP stdio/http──> mcp_adapter.py ──┐
                                                         ├─> FastAPI 核心服务 ──> musicdl ──> 各音乐平台
MoviePilot 插件（可选） ─────────REST────────────────────┘        │
                                                              下载目录 + SQLite
```

## 快速开始

### 1. 环境准备

```bash
python -m venv venv
# Windows
venv\Scripts\python.exe -m pip install -r requirements.txt
# Linux/macOS
venv/bin/pip install -r requirements.txt
```

### 2. 配置

编辑 `config.yaml`（所有项均可选，见文件内注释）：
- `download_root`：下载根目录（相对路径锚定到项目根，也可配绝对路径，建议指向媒体库）
- `api_key`：留空则不启用鉴权（纯内网推荐）；非空则客户端需带 `X-API-Key` 头
- `sources`：按需填各平台 cookies（QQ VIP、TIDAL、夸克网盘等），不配则对应源自动标记不可用

### 3. 启动核心服务

```bash
# Windows
venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8765 --app-dir .
# Linux/macOS
venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8765 --app-dir .
```

健康检查：`curl http://127.0.0.1:8765/api/v1/health`

### Web 界面

Docker 构建会自动打包前端，启动后访问 `http://<host>:8765/`。本地开发先启动上述核心服务，再运行：

```bash
cd web
npm ci
npm run dev -- --host 0.0.0.0
```

访问 `http://<host>:5173/`，开发代理将 `/api` 请求转发到本机 8765 端口。登录密钥取自 `config.yaml` 的 `api_key`；未启用鉴权时点“服务未启用鉴权？直接进入”，页面会先验证匿名访问。需要本地生产预览时运行 `npm run build` 后重启核心服务，即可由 8765 端口托管 `web/dist`。

开发代理连接的 8765 后端也需要运行当前源码。若该端口由 Docker 提供，源码改动需要构建新镜像并重建容器；`docker restart` 只会重新启动原镜像。前端已更新、后端镜像未更新时，新 API 会返回 404，可通过 `http://<host>:8765/openapi.json` 检查后端是否包含对应接口。

专辑下载完成后，可在专辑页或任务详情核对逐曲匹配分数、文件大小并归档；清理、单曲迁移与歌词回填均先预览再确认。Web 榜单默认网易云、QQ 在后；后台解析显示逐曲进度、当前曲目、可下载与跳过数量及耗时，可停止解析、一键全选全部结果，并显示文件大小。切换页面后继续解析，搜索和榜单的结果、勾选状态保留在当前浏览器会话中；刷新网页或退出登录后重置。歌单页仍使用同步解析接口，大列表可能耗时数分钟。后端内存任务及其清单读取入口会在服务重启后失效，历史保留在 SQLite。

搜索结果支持按文件大小升序或降序排列，大小未知的曲目排在最后。默认搜索来源统一读取设置中的 `default_sources`，初始为 QQ、酷狗、网易云、千千、咪咕、酷我六源；其他来源作为未选候选。已有部署继续使用挂载配置，可在设置的“下载与归档”中点击“使用常用六源”并保存；保存后搜索页同步勾选，临时手动选择则在导航时保留。飞牛歌单卡片读取详情接口的总曲目数，数量读取失败时显示未知。

系统配置页显示已保存的配置，并区分即时生效与需重启的项目；API Key 修改后需重新登录。QQ / 网易云扫码凭证自动保存到该来源的搜索、下载和解析配置，页面仅显示掩码；二维码会话保留在内存，关闭弹窗或过期后停止轮询。QQ 使用 QQ App 扫码，网易云使用网易云音乐 App。真实手机扫码与登录后下载仍需在部署环境验收。

飞牛歌单页支持歌单列表、详情、四组搜索、创建空歌单或创建时导入任务，以及按曲目 guid / 已完成单曲任务 / 容器文件路径追加。追加会去重并显示未匹配文件；任务必须仍在当前服务内存中且有成功入库曲目。曲目尚未被飞牛扫描时，可检查路径映射后重试。

### 4. 启动 MCP 适配器（供 Agent 调用）

```bash
# stdio（本地 Agent 直接拉起，推荐）
venv\Scripts\python.exe mcp_adapter.py

# 或 HTTP（远程 Agent 连接内网服务）：把 config.yaml 的 mcp.transport 改为 http
# （也可用环境变量覆盖：MUSIC_MCP_TRANSPORT=http MUSIC_SERVICE_URL=http://127.0.0.1:8765）
venv\Scripts\python.exe mcp_adapter.py
```

## 文档

前后端可一起打包进 Docker 镜像，由 8765 端口提供网页和 API。执行 `bash scripts/package-image.sh <镜像标签>` 可构建并导出 `artifacts/` 下的镜像归档及校验文件，在 NAS 上使用 `docker image load` 加载即可；镜像使用无凭证默认配置，继续挂载部署目录中的 `config.yaml`。详见 [Docker 部署指南](DEPLOY.md)。

- [设计方案](docs/design.md)：背景目标、总体架构、关键取舍、里程碑、范围外事项
- [开发规划](ROADMAP.md)：里程碑状态与 M4 待办方向
- [贡献指南](CONTRIBUTING.md)：核心原则、开发环境、自测要求
- [REST API 接口文档](docs/API.md)：全部端点、字段、示例、错误码、已知限制
- [MCP 调用文档](docs/MCP.md)：接入配置（stdio/http）、工具说明、典型调用流程
- [Docker 部署指南](DEPLOY.md)：拷贝清单、构建、启动、验证、排障

## REST API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/health` | 健康检查（含 musicdl 版本） |
| GET | `/api/v1/sources` | 列出全部源及能力/可用性 |
| GET | `/api/v1/system/status` | 系统概览聚合状态 |
| GET / PUT | `/api/v1/config` | 脱敏读取与保存配置（逐项标注热生效或需重启） |
| GET | `/api/v1/config/editor` | 已保存与运行配置、重启待生效项、环境变量覆盖项 |
| POST / GET / DELETE | `/api/v1/auth/qr/{source}` | 创建、轮询、取消 QQ / 网易云扫码登录会话 |
| GET | `/api/v1/search?keyword=…&sources=…&limit=…` | 聚合搜索，返回标准化 Track |
| GET | `/api/v1/playlist?url=…&source=…` | 歌单解析（仅支持歌单的源） |
| GET | `/api/v1/charts?source=…` | 榜单目录（QQ/网易云排行榜；source 省略返回合并列表） |
| GET | `/api/v1/charts/{source}/{chart_id}?limit=…` | 榜单曲目（已缓存，可直接全量/挑选提交下载） |
| POST | `/api/v1/charts/{source}/{chart_id}/parse?limit=…` | 后台解析榜单，立即返回任务与进度 |
| GET | `/api/v1/chart-parses/{task_id}` | 榜单解析进度（已处理/总数、可下载/跳过、当前曲目、耗时） |
| GET | `/api/v1/chart-parses/{task_id}/tracks` | 榜单解析成功后的曲目结果 |
| DELETE | `/api/v1/chart-parses/{task_id}` | 停止榜单解析 |
| POST | `/api/v1/downloads` | 提交下载（body：`{"tracks":[…], "subdir":?, "library":?, "max_size_mb":?, "playlist":?}`；传 library 则下载后自动归档；传 playlist（需配 fnos_music + library）则归档后自动同步飞牛歌单） |
| GET | `/api/v1/downloads/{task_id}` | 查询任务状态/进度 |
| GET | `/api/v1/downloads/{task_id}/manifest` | 读取当前专辑任务的逐曲匹配清单 |
| GET | `/api/v1/downloads` | 任务列表 |
| GET | `/api/v1/history` | 历史记录 |
| GET | `/api/v1/libraries` | 列出归档目标库（默认库 + 命名附加库） |
| GET | `/api/v1/library/entries?library=…&path=…` | 分页浏览命名库内一层目录（不跟随符号链接） |
| GET | `/api/v1/albums/search?keyword=…&artist=…&limit=…` | 专辑搜索（iTunes 元数据） |
| GET | `/api/v1/albums/{collection_id}` | 专辑详情与官方曲目表 |
| POST | `/api/v1/albums/{collection_id}/download` | 专辑整单下载（逐曲消歧 + 序号命名 + manifest.json） |
| POST | `/api/v1/albums/archive` | 专辑归档入库（硬链接/tag/嵌封面，需配置 library_root） |
| POST | `/api/v1/tracks/archive` | 单曲归档入库（`{库根}/{艺人}/{曲名.ext}`） |
| POST | `/api/v1/library/replace_track` | 指定曲目重搜替换 |
| POST | `/api/v1/library/cleanup` | 清理曲目、专辑或艺人目录（支持预览与确认） |
| POST | `/api/v1/library/migrate_singles` | 将单曲专辑迁移到单曲库（支持预览） |
| POST | `/api/v1/library/backfill_lyrics` | 回填缺失歌词（默认预览） |
| GET | `/api/v1/fnos/playlists` | 飞牛歌单列表（需配置 fnos_music） |
| GET | `/api/v1/fnos/playlists/{name}/tracks` | 飞牛歌单内曲目（不存在 404） |
| POST | `/api/v1/fnos/playlists` | 建/补飞牛歌单（body：`{"name":…, "task_id":?, "paths":?}`，ensure 语义幂等去重） |
| POST | `/api/v1/fnos/playlists/{name}/tracks` | 严格追加到既有飞牛歌单（body：`{"paths":?, "guids":?}`；歌单不存在 404） |
| GET | `/api/v1/fnos/search?q=…` | 飞牛库模糊搜索（track/album/artist/playlist 四组 top-5） |
| GET | `/api/v1/fnos/search/tracks?q=…&limit=…` | 飞牛曲目全量搜索（带 guid，供追加歌单挑选） |
| POST | `/api/v1/downloads/{task_id}/cancel` | 取消（仅 pending 态有效） |

> 字段定义与完整示例见 [docs/API.md](docs/API.md)。

## MCP 工具

| 工具 | 说明 |
|---|---|
| `list_sources` | 列出可用/不可用源及原因 |
| `list_libraries` | 列出归档目标库（默认库 + 命名附加库） |
| `search_tracks(keyword, sources?, limit?)` | 搜索，返回含 `raw` 的 Track 列表 |
| `parse_playlist(url, source?)` | 歌单解析 |
| `list_charts(source?)` | 榜单目录（QQ/网易云排行榜） |
| `get_chart_tracks(source, chart_id, limit?)` | 榜单曲目（可直接全量/挑选提交下载） |
| `submit_download(tracks, subdir?, library?, max_size_mb?, playlist?)` | 提交下载（tracks 只传 `id`；传 library 下载后自动归档；传 playlist 归档后自动同步飞牛歌单） |
| `get_download_status(task_id)` | 查询进度 |
| `search_albums(keyword, artist?, limit?)` | 专辑搜索（iTunes 元数据） |
| `get_album_info(collection_id)` | 专辑详情与官方曲目表 |
| `download_album(collection_id, sources?, subdir?, …, max_size_mb?)` | 专辑整单下载（产出 manifest.json） |
| `archive_album(task_id?, manifest_path?, overwrite?, …, library?)` | 专辑归档入库（需配置 library_root） |
| `archive_tracks(task_id, library?, overwrite?)` | 单曲归档入库 |
| `list_fnos_playlists()` | 飞牛歌单列表 |
| `get_fnos_playlist_tracks(name)` | 飞牛歌单内曲目 |
| `create_fnos_playlist(name, task_id?, paths?)` | 建/补飞牛歌单（ensure 语义，幂等去重） |
| `add_fnos_playlist_tracks(name, paths?, guids?)` | 严格追加到既有飞牛歌单（404 防打错字） |
| `search_fnos(q)` | 飞牛库模糊搜索（四组 top-5） |
| `search_fnos_tracks(q, limit?)` | 飞牛曲目全量搜索（挑 guid 用） |

> 接入配置与调用示例见 [docs/MCP.md](docs/MCP.md)。

## 外部依赖（按需）

- **HLS 流下载**（Apple Music 等）：需要 [N_m3u8DL-RE](https://github.com/nilaoda/N_m3u8DL-RE)
- **YouTube 下载**：需要 Node.js
- **无损夸克源**（Mitu/Buguyy/Yinyuedao/Gequbao）：需在 `config.yaml` 配置夸克网盘 cookies
- **飞牛音乐歌单同步**：需在 `config.yaml` 配置 `fnos_music` 段（飞牛音乐应用账号密码 + `path_map` 容器库根→宿主路径映射）；纯 API 客户端，不挂载不读取 music.db

## 升级 musicdl

平台接口适配由 musicdl 作者维护。定期升级并回归主链路即可：

```bash
venv\Scripts\python.exe -m pip install -U "musicdl>=2.13.4,<3.0"
# 验证：curl 'http://127.0.0.1:8765/api/v1/search?keyword=周杰伦&limit=1' 有结果
```

## 目录说明

- `app/`：核心服务（config/registry/search/playlist/download/storage/main）
- `mcp_adapter.py`：MCP 薄客户端
- `config.yaml`：唯一配置文件
- `downloads/`：默认下载根目录
- `data/`：SQLite 任务/历史库
