# MediaMusicService MCP 调用文档

本文档说明如何把 MediaMusicService 作为 **MCP（Model Context Protocol）服务**接入各类 Agent（Trae、Claude Desktop、Cline 等），以及各工具的调用方式。

## 一、MCP 服务概述

- 服务名：`media-music`
- 实现：基于 FastMCP 的**薄客户端**，不直接依赖 musicdl，所有能力通过 HTTP 调用核心 REST 服务
- 工具数：14 个
- 传输方式：**stdio**（默认，本地 Agent 直接拉起，推荐）/ **http**（远程 Agent）

### 与 REST API 的关系

| MCP 工具 | 对应 REST 接口 |
|---|---|
| `list_sources` | `GET /api/v1/sources` |
| `list_libraries` | `GET /api/v1/libraries` |
| `search_tracks` | `GET /api/v1/search` |
| `parse_playlist` | `GET /api/v1/playlist` |
| `submit_download` | `POST /api/v1/downloads` |
| `get_download_status` | `GET /api/v1/downloads/{task_id}` |
| `search_albums` | `GET /api/v1/albums/search` |
| `get_album_info` | `GET /api/v1/albums/{collection_id}` |
| `download_album` | `POST /api/v1/albums/{collection_id}/download` |
| `archive_album` | `POST /api/v1/albums/archive` |
| `archive_tracks` | `POST /api/v1/tracks/archive` |
| `replace_album_track` | `POST /api/v1/library/replace_track` |
| `cleanup_library` | `POST /api/v1/library/cleanup` |
| `migrate_singles` | `POST /api/v1/library/migrate_singles` |

> 核心 REST 服务必须先启动并可达（默认 `http://127.0.0.1:8765`），MCP 适配器只是它的客户端。

## 二、配置

MCP 适配器与核心服务**共用同一份 `config.yaml`**（唯一配置源），读取其中的 `mcp` 段与顶层 `api_key`：

```yaml
mcp:
  transport: "stdio"  # stdio：本地 Agent 直接拉起；http：远程 Agent
  host: "0.0.0.0"     # http 模式监听地址
  port: 8766          # http 模式监听端口
  service_url: "http://127.0.0.1:8765"  # 核心 REST 服务地址；docker 部署改为 http://music-service:8765
```

鉴权复用 `config.yaml` 顶层 `api_key`：核心服务开了鉴权时，适配器自动带同一 key，无需单独配置。

配置文件路径默认取 `mcp_adapter.py` 旁的 `config.yaml`，可用 `MUSIC_SERVICE_CONFIG` 指定。
**环境变量仍可覆盖**（优先级：环境变量 > config.yaml > 默认值），便于无配置文件的 stdio 场景：

| 变量 | 覆盖的配置项 |
|---|---|
| `MUSIC_SERVICE_URL` | `mcp.service_url` |
| `MUSIC_SERVICE_API_KEY` | 顶层 `api_key` |
| `MUSIC_MCP_TRANSPORT` | `mcp.transport` |
| `MUSIC_MCP_HOST` | `mcp.host` |
| `MUSIC_MCP_PORT` | `mcp.port` |

## 三、接入方式

### 方式 A：stdio（本地 Agent，推荐）

```json
{
  "mcpServers": {
    "media-music": {
      "command": "D:\\path\\to\\venv\\Scripts\\python.exe",
      "args": ["D:\\path\\to\\media-music-service\\mcp_adapter.py"],
      "env": { "MUSIC_SERVICE_URL": "http://192.168.254.112:8765" }
    }
  }
}
```

### 方式 B：http（远程 Agent）

先在部署机的 `config.yaml` 中把 `mcp.transport` 改为 `http`、`mcp.service_url` 改为 `http://music-service:8765`（docker 容器间通信），然后启动 MCP HTTP 服务：`docker compose --profile mcp up -d`（监听 8766），接着：

```json
{
  "mcpServers": {
    "media-music": { "url": "http://192.168.254.112:8766/mcp" }
  }
}
```

## 四、工具说明

### list_sources
列出全部音乐源及可用性，拆分为 available / unavailable 两组。典型用途：搜索前先判断哪些源可用、哪些需要补 cookies。

### list_libraries
列出全部归档目标库（命名库根）：默认库 `default` + 服务端 `extra_library_roots` 配置的命名附加库（如单曲库 `singles`）。归档/下载时的 `library` 参数从这里选；留空用默认库。只能传库名（白名单），不接受裸路径。

### search_tracks(keyword, sources?, limit?)
按关键词搜索。返回精简 Track（含 `id`，供下载回传）。

### parse_playlist(url, source?)
解析歌单 URL 为曲目列表（Track 含 `id`）。

### submit_download(tracks, subdir?, library?, max_size_mb?)
提交下载任务（异步）。`tracks` 每项**只需传 `id` 字段**（取自 `search_tracks`/`parse_playlist` 返回项，如 `[{"id": "KuwoMusicClient:594551679"}]`），服务端按搜索缓存自动补全下载上下文（缓存 1 小时，服务重启后失效，未命中会报 400 提示重新搜索）。返回 `task_id`。传 `library` 时下载完成后**自动归档**到该库（单曲结构 `{库根}/{艺人}/{曲名.ext}`，一步到位）；`max_size_mb` 为单文件体积上限（MB），>0 时超限曲目跳过且优先于服务端配置，0/空不限。

### get_download_status(task_id)
查询下载任务进度（status/completed/failed/save_dir/results/errors）。单曲与专辑任务通用；专辑任务完成后 `manifest_path` 指向结构化清单。

### search_albums(keyword, artist?, limit?)
按专辑名搜索专辑（iTunes 优先；覆盖不足时自动回退网易云/QQ）。返回 `collection_id`（供后续两个工具使用；iTunes 专辑为纯数字，中文源专辑带 `netease:`/`qq:` 前缀）、曲目数、发行日期、高清封面 URL、`description`（简介，可为空）、`meta_source`。

### get_album_info(collection_id)
获取专辑详情：官方曲目表（含 disc/序号/时长）、发行日期、封面、简介等。`collection_id` 支持 `netease:`/`qq:` 前缀；iTunes id 在其各 storefront 均无曲目时自动回退中文源整体接管。**下载前建议先调用此工具向用户确认专辑版本**（同名专辑可能有 Single/EP/ deluxe 等多个版本）。

### download_album(collection_id, sources?, subdir?, album_title?, artist?, max_size_mb?)
专辑整单下载（异步）。服务端逐曲搜索匹配消歧（打分含标题/歌手/专辑/时长，低于阈值记 `unmatched` 不强行下载；同分段候选优先无损音质，没有合格无损才选 MP3），按曲目序号命名落盘（`01 曲名.flac`，多 Disc 为 `1-01 曲名.flac`），附 `cover.jpg` 与 `manifest.json`。**singles 库复用**：服务端配置了 `singles` 命名库时，逐曲匹配前先在 singles 库查找同专辑曲目，命中则不搜索不下载（manifest 的 `match.source` 为 `"singles"` 且带 `reused_from` 原路径），归档成功后该曲目自动从 singles 库迁移删除。返回 `task_id`，用 `get_download_status` 轮询。`album_title`/`artist` 用于 iTunes 专辑名是罗马音/拼音时显式指定中文显示名（写入 manifest 供归档使用）。`max_size_mb` 为单文件体积上限（MB）：超限不是硬剔除，优先选不超限且达阈值的候选，无合格不超限候选时才放宽限制选超限最高分（优先保专辑完整与版本正确），并在 manifest 标注 `oversized_relaxed: true` 供复核。

### archive_album(task_id?, manifest_path?, overwrite?, album_title?, artist?, library?)
把专辑下载产物归档进媒体库（同步，秒级）：硬链接（失败回退复制）入库 → 断链后写 tag → 嵌封面歌词 → `cover.jpg` + `album_info.txt`。库内结构 `{库根}/{艺人}/{专辑}/`，多 Disc 用 `CD1/CD2` 子目录。`task_id`（服务未重启时）与 `manifest_path` 二选一；默认幂等跳过已存在文件。前置：服务端已配置 `library_root` 并挂载媒体库卷；`library` 选择目标库（见 `list_libraries`），留空用默认库。专辑名/艺人名按解析链确定：显式参数 > manifest display_* > 自动推断（国内源多数表决，仅在原名为罗马音时生效）> iTunes 原名——**罗马音专辑名一般无需手动传参，归档会自动纠正为中文**。

### archive_tracks(task_id, library?, overwrite?)
把单曲下载任务的产物归档进媒体库（同步）：硬链接/复制入库 → 断链后写 tag → 嵌封面歌词（封面从候选 `cover_url` 下载）。结构 `{库根}/{艺人}/{曲名.ext}`，同名 `.lrc` 放旁边；不写曲目序号。艺人目录无 `artist.*` 时按候选 `artist_img_url` 补一份艺人头像（Navidrome 本地头像约定，幂等，已有不覆盖）。`submit_download` 传了 `library` 时已自动归档，本工具用于事后补归档或换库重归档；默认幂等跳过。任务在内存中才可用（服务重启后需重新下载）。

### replace_album_track(artist, album, track, library?, sources?, force?, max_size_mb?)
重新搜索专辑中指定曲目，用更高音质版本替换（同步，含一次搜索+下载）。新候选音质分档（无损 > 320k > 其他）高于现有文件才替换，否则返回 `action=kept` 不动文件；`force=True` 强制替换。`track` 传序号（如 `"3"`）或曲名，多 Disc 专辑可用 `"D-NN"` 消歧（如 `"2-03"` 指 CD2 第 3 首）。替换后序号/专辑/艺人/日期沿用旧 tag，封面沿用专辑 `cover.*`，歌词更新 `lyrics/`。返回 `action`（replaced/kept/unmatched/failed）与新旧版本信息。

### cleanup_library(artist, album?, tracks?, library?, dry_run?)
清理媒体库中的专辑或曲目文件（同步）。粒度：`tracks` 指定曲目 > `album` 整专辑 > `artist` 整艺人；存放文件的目录变空时自底向上一并清理（空 CDx/ → 无音频残留的专辑目录 → 空艺人目录）。`tracks` 元素为序号（如 `"3"`）、`"D-NN"` 或曲名。**强烈建议先 `dry_run=True` 跑一遍确认将删除的项**（返回 `deleted_files`/`removed_dirs`），确认后再正式执行；部分删除失败时返回 `status=partial` 且 `errors` 非空。

### migrate_singles(library?, target_library?, artist?, dry_run?)
扫描专辑库中只有一个音频文件的专辑目录（单曲专辑），迁移到 singles 库 `{目标根}/{艺人}/{曲名.ext}`（同步）。迁移后清除序号类 tag（保留专辑名/封面/歌词），同名 `.lrc` 一并移动，原专辑目录与空艺人目录自动清理；目标已存在同名文件则跳过。`artist` 可限定单个艺人；**建议先 `dry_run=True` 确认迁移范围**（返回 `migrated` 的 from/to 列表），再正式执行。

## 五、典型调用流程

### 单曲下载（可自动归档入库）

1. `list_sources` 确认可用源；要入库时先 `list_libraries` 确认目标库名（如单曲库 `singles`）；
2. `search_tracks(keyword, sources, limit)` 拿到候选曲目；
3. 从结果中记下目标曲目的 `id`；
4. `submit_download(tracks=[{"id": "…"}], library="singles")` 提交，拿到 `task_id`；传了 `library` 则下载完成后自动归档；
5. 用 `get_download_status(task_id)` 轮询，直到 `status` 为 `success`/`failed`；`message` 中含自动归档结果摘要；
6. 未传 `library` 的事后补归档：`archive_tracks(task_id, library="singles")`。

> 关键约束：`submit_download` 的 `tracks` 只需 `id`，但依赖服务端搜索缓存——**搜索与提交之间不要重启服务**，缓存未命中时报 400，重新搜索一次再提交即可。

### 专辑下载与归档

1. `search_albums(专辑名, artist=艺人)` 找到目标专辑，**有多个版本时用 `get_album_info` 核对曲目表后让用户确认**；
2. `download_album(collection_id)` 提交，拿到 `task_id`；
3. `get_download_status(task_id)` 轮询直到完成；
4. 读取 `manifest_path` 指向的 `manifest.json`：逐曲 `status`（ok/unmatched/failed）、`match.score`（匹配置信分）、失败原因均在其中；`unmatched`/`failed` 的曲目可向用户报告并决定是否单曲补下；
5. `archive_album(task_id)` 归档入库（媒体库结构 `{艺人}/{专辑}/`，多 Disc 自动 `CD1/CD2`）；归档结果里逐曲 `action` 为 `linked/copied/skipped/failed`，有 `failed` 时向用户报告 `errors`。

## 六、故障排查

- **Agent 看不到工具**：检查 `command`/`args` 路径、`fastmcp` 是否已装进对应 Python 环境；
- **调用报连接错误**：确认 `MUSIC_SERVICE_URL` 指向的核心 REST 服务已启动（`curl …/api/v1/health`）；
- **提交下载报 400 提示缓存未命中**：仅传 `id` 时依赖服务端搜索缓存（1 小时有效，重启失效），重新 `search_tracks` 再提交即可；
- **http 模式连不上**：检查 `8766` 端口是否放行、`mcp.service_url` 指向的核心 REST 服务是否可达（docker 部署应为 `http://music-service:8765`）。
