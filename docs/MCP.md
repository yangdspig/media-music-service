# MediaMusicService MCP 调用文档

本文档说明如何把 MediaMusicService 作为 **MCP（Model Context Protocol）服务**接入各类 Agent（Trae、Claude Desktop、Cline 等），以及各工具的调用方式。

## 一、MCP 服务概述

- 服务名：`media-music`
- 实现：基于 FastMCP 的**薄客户端**，不直接依赖 musicdl，所有能力通过 HTTP 调用核心 REST 服务
- 工具数：9 个
- 传输方式：**stdio**（默认，本地 Agent 直接拉起，推荐）/ **http**（远程 Agent）

### 与 REST API 的关系

| MCP 工具 | 对应 REST 接口 |
|---|---|
| `list_sources` | `GET /api/v1/sources` |
| `search_tracks` | `GET /api/v1/search` |
| `parse_playlist` | `GET /api/v1/playlist` |
| `submit_download` | `POST /api/v1/downloads` |
| `get_download_status` | `GET /api/v1/downloads/{task_id}` |
| `search_albums` | `GET /api/v1/albums/search` |
| `get_album_info` | `GET /api/v1/albums/{collection_id}` |
| `download_album` | `POST /api/v1/albums/{collection_id}/download` |
| `archive_album` | `POST /api/v1/albums/archive` |

> 核心 REST 服务必须先启动并可达（默认 `http://127.0.0.1:8765`），MCP 适配器只是它的客户端。

## 二、环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `MUSIC_SERVICE_URL` | `http://127.0.0.1:8765` | 核心 REST 服务地址 |
| `MUSIC_SERVICE_API_KEY` | 空 | 若核心服务启用了 `api_key`，此处需一致 |
| `MUSIC_MCP_TRANSPORT` | `stdio` | `stdio` 或 `http` |
| `MUSIC_MCP_HOST` | `0.0.0.0` | http 模式监听地址 |
| `MUSIC_MCP_PORT` | `8766` | http 模式监听端口 |

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

先在部署机上启动 MCP HTTP 服务：`docker compose --profile mcp up -d`（监听 8766），然后：

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

### search_tracks(keyword, sources?, limit?)
按关键词搜索。返回精简 Track + `raw`（供下载回传）。

### parse_playlist(url, source?)
解析歌单 URL 为曲目列表（Track 含 `raw`）。

### submit_download(tracks, subdir?)
提交下载任务（异步）。`tracks` 元素**必须含 `raw` 字段**（取自 `search_tracks`/`parse_playlist` 返回项）。返回 `task_id`。

### get_download_status(task_id)
查询下载任务进度（status/completed/failed/save_dir/results/errors）。单曲与专辑任务通用；专辑任务完成后 `manifest_path` 指向结构化清单。

### search_albums(keyword, artist?, limit?)
按专辑名搜索专辑（iTunes 官方元数据）。返回 `collection_id`（供后续两个工具使用）、曲目数、发行日期、高清封面 URL。

### get_album_info(collection_id)
获取专辑详情：官方曲目表（含 disc/序号/时长）、发行日期、封面等。**下载前建议先调用此工具向用户确认专辑版本**（同名专辑可能有 Single/EP/ deluxe 等多个版本）。

### download_album(collection_id, sources?, subdir?, album_title?, artist?)
专辑整单下载（异步）。服务端逐曲搜索匹配消歧（打分含标题/歌手/专辑/时长，低于阈值记 `unmatched` 不强行下载；同分段候选优先无损音质，没有合格无损才选 MP3），按曲目序号命名落盘（`01 曲名.flac`，多 Disc 为 `1-01 曲名.flac`），附 `cover.jpg` 与 `manifest.json`。返回 `task_id`，用 `get_download_status` 轮询。`album_title`/`artist` 用于 iTunes 专辑名是罗马音/拼音时显式指定中文显示名（写入 manifest 供归档使用）。

### archive_album(task_id?, manifest_path?, overwrite?, album_title?, artist?)
把专辑下载产物归档进媒体库（同步，秒级）：硬链接（失败回退复制）入库 → 断链后写 tag → 嵌封面歌词 → `cover.jpg` + `album_info.txt`。库内结构 `{library_root}/{艺人}/{专辑}/`，多 Disc 用 `CD1/CD2` 子目录。`task_id`（服务未重启时）与 `manifest_path` 二选一；默认幂等跳过已存在文件。前置：服务端已配置 `library_root` 并挂载媒体库卷。专辑名/艺人名按解析链确定：显式参数 > manifest display_* > 自动推断（国内源多数表决，仅在原名为罗马音时生效）> iTunes 原名——**罗马音专辑名一般无需手动传参，归档会自动纠正为中文**。

## 五、典型调用流程

### 单曲下载

1. `list_sources` 确认可用源；
2. `search_tracks(keyword, sources, limit)` 拿到候选曲目；
3. 从结果中**保留含 `raw` 的完整 Track 对象**；
4. `submit_download(tracks=[…])` 提交，拿到 `task_id`；
5. 用 `get_download_status(task_id)` 轮询，直到 `status` 为 `success`/`failed`。

> 关键约束：`submit_download` 的 `tracks` 元素必须带 `raw` 字段，否则服务端无法还原 musicdl 的下载上下文。

### 专辑下载与归档

1. `search_albums(专辑名, artist=艺人)` 找到目标专辑，**有多个版本时用 `get_album_info` 核对曲目表后让用户确认**；
2. `download_album(collection_id)` 提交，拿到 `task_id`；
3. `get_download_status(task_id)` 轮询直到完成；
4. 读取 `manifest_path` 指向的 `manifest.json`：逐曲 `status`（ok/unmatched/failed）、`match.score`（匹配置信分）、失败原因均在其中；`unmatched`/`failed` 的曲目可向用户报告并决定是否单曲补下；
5. `archive_album(task_id)` 归档入库（媒体库结构 `{艺人}/{专辑}/`，多 Disc 自动 `CD1/CD2`）；归档结果里逐曲 `action` 为 `linked/copied/skipped/failed`，有 `failed` 时向用户报告 `errors`。

## 六、故障排查

- **Agent 看不到工具**：检查 `command`/`args` 路径、`fastmcp` 是否已装进对应 Python 环境；
- **调用报连接错误**：确认 `MUSIC_SERVICE_URL` 指向的核心 REST 服务已启动（`curl …/api/v1/health`）；
- **下载失败提示缺 raw**：确认传入的是 `search_tracks`/`parse_playlist` 的原样返回项；
- **http 模式连不上**：检查 `8766` 端口是否放行、MCP 容器内 `MUSIC_SERVICE_URL` 是否可达核心服务。
