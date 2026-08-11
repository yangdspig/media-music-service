# MediaMusicService

基于 [musicdl](https://github.com/CharlesPikachu/musicdl) 底层能力构建的**音乐/有声读物统一搜索下载服务**，面向纯内网自用场景。

- 对外提供 **REST API**（FastAPI）与 **MCP 工具**（FastMCP）两种接入方式
- 复用 musicdl 覆盖的 54 个平台源（网易云/QQ/酷狗/酷我/咪咕/千千/Spotify/Apple Music/喜马拉雅等）
- 下载为异步任务，支持进度查询与历史追溯

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

### 4. 启动 MCP 适配器（供 Agent 调用）

```bash
# stdio（本地 Agent 直接拉起，推荐）
venv\Scripts\python.exe mcp_adapter.py

# 或 HTTP（远程 Agent 连接内网服务）
set MUSIC_MCP_TRANSPORT=http
set MUSIC_SERVICE_URL=http://127.0.0.1:8765
venv\Scripts\python.exe mcp_adapter.py
```

## 文档

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
| GET | `/api/v1/search?keyword=…&sources=…&limit=…` | 聚合搜索，返回标准化 Track |
| GET | `/api/v1/playlist?url=…&source=…` | 歌单解析（仅支持歌单的源） |
| POST | `/api/v1/downloads` | 提交下载（body：`{"tracks":[…], "subdir":?, "library":?, "max_size_mb":?}`；传 library 则下载后自动归档） |
| GET | `/api/v1/downloads/{task_id}` | 查询任务状态/进度 |
| GET | `/api/v1/downloads` | 任务列表 |
| GET | `/api/v1/history` | 历史记录 |
| GET | `/api/v1/libraries` | 列出归档目标库（默认库 + 命名附加库） |
| GET | `/api/v1/albums/search?keyword=…&artist=…&limit=…` | 专辑搜索（iTunes 元数据） |
| GET | `/api/v1/albums/{collection_id}` | 专辑详情与官方曲目表 |
| POST | `/api/v1/albums/{collection_id}/download` | 专辑整单下载（逐曲消歧 + 序号命名 + manifest.json） |
| POST | `/api/v1/albums/archive` | 专辑归档入库（硬链接/tag/嵌封面，需配置 library_root） |
| POST | `/api/v1/tracks/archive` | 单曲归档入库（`{库根}/{艺人}/{曲名.ext}`） |
| POST | `/api/v1/downloads/{task_id}/cancel` | 取消（仅 pending 态有效） |

> 字段定义与完整示例见 [docs/API.md](docs/API.md)。

## MCP 工具

| 工具 | 说明 |
|---|---|
| `list_sources` | 列出可用/不可用源及原因 |
| `list_libraries` | 列出归档目标库（默认库 + 命名附加库） |
| `search_tracks(keyword, sources?, limit?)` | 搜索，返回含 `raw` 的 Track 列表 |
| `parse_playlist(url, source?)` | 歌单解析 |
| `submit_download(tracks, subdir?, library?, max_size_mb?)` | 提交下载（tracks 须含 `raw`；传 library 下载后自动归档） |
| `get_download_status(task_id)` | 查询进度 |
| `search_albums(keyword, artist?, limit?)` | 专辑搜索（iTunes 元数据） |
| `get_album_info(collection_id)` | 专辑详情与官方曲目表 |
| `download_album(collection_id, sources?, subdir?, …, max_size_mb?)` | 专辑整单下载（产出 manifest.json） |
| `archive_album(task_id?, manifest_path?, overwrite?, …, library?)` | 专辑归档入库（需配置 library_root） |
| `archive_tracks(task_id, library?, overwrite?)` | 单曲归档入库 |

> 接入配置与调用示例见 [docs/MCP.md](docs/MCP.md)。

## 外部依赖（按需）

- **HLS 流下载**（Apple Music 等）：需要 [N_m3u8DL-RE](https://github.com/nilaoda/N_m3u8DL-RE)
- **YouTube 下载**：需要 Node.js
- **无损夸克源**（Mitu/Buguyy/Yinyuedao/Gequbao）：需在 `config.yaml` 配置夸克网盘 cookies

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
