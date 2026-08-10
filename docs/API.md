# MediaMusicService REST API 接口文档

- 服务名：`MediaMusicService`
- 版本：`0.1.0`
- 默认地址：`http://<host>:8765`
- 数据格式：请求/响应均为 `application/json; charset=utf-8`
- 交互式文档：服务运行时可访问 `http://<host>:8765/docs`（Swagger UI）或 `/redoc`

## 目录

- [通用约定](#通用约定)
- [数据模型](#数据模型)
- [接口列表](#接口列表)
- [错误码](#错误码)
- [注意事项与已知限制](#注意事项与已知限制)

---

## 通用约定

### 鉴权（可选）

- 当 `config.yaml` 中 `api_key` 非空时启用，所有 `/api/v1/*` 接口需在请求头携带：
  ```
  X-API-Key: <你的api_key>
  ```
- `api_key` 为空（默认）则不启用任何鉴权，纯内网环境推荐此方式。
- 鉴权失败返回 `401`。

### 字符编码

- 全链路 UTF-8。URL 中的中文关键词需做 URL Encode（如 `周杰伦` → `%E5%91%A8%E6%9D%B0%E4%BC%A6`）。

### 字段命名

- 统一蛇形命名（snake_case），如 `task_id`、`save_dir`、`size_bytes`。

---

## 数据模型

### Track（标准化音轨）

`search` / `playlist` 的返回元素，也是 `submit_download` 的输入元素。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string | 全局唯一标识，格式 `{source}:{identifier}`，如 `MiguMusicClient:600929000000096577` |
| `source` | string | 来源客户端名，如 `NeteaseMusicClient` |
| `title` | string | 歌曲/节目名 |
| `artists` | string[] | 歌手/主播列表 |
| `album` | string \| null | 专辑名 |
| `duration_s` | number \| null | 时长（秒） |
| `quality` | string \| null | 音质/码率描述，如 `lossless`、`320k` |
| `ext` | string \| null | 文件格式，如 `flac` / `mp3` / `m4a` |
| `size_bytes` | int \| null | 文件大小（字节） |
| `cover_url` | string \| null | 封面图 URL |
| `lyric` | string \| null | 歌词文本（若有） |
| `raw` | object | musicdl 原始 `SongInfo` dict，**提交下载时必须原样回传** |

> 说明：`raw` 字段体积较大且各源结构不一，客户端展示时可忽略，但**下载时必须带上**，服务端据此还原 musicdl 的 `SongInfo` 对象。

### SourceInfo（源信息）

| 字段 | 类型 | 说明 |
|---|---|---|
| `name` | string | 源客户端名 |
| `category` | string | `china` / `global` / `audiobook` / `aggregator` / `thirdparty` |
| `supports_search` | bool | 恒为 `true` |
| `supports_download` | bool | 恒为 `true` |
| `supports_playlist` | bool | 是否支持歌单解析 |
| `needs_cookies` | bool | 是否需要登录 cookies（含夸克网盘） |
| `available` | bool | 当前是否可用（考虑配置与网络） |
| `note` | string | 不可用原因说明 |

### DownloadTask（下载任务）

| 字段 | 类型 | 说明 |
|---|---|---|
| `task_id` | string | 任务 ID（12 位 hex） |
| `status` | string | `pending` / `running` / `success` / `failed` / `canceled` |
| `total` | int | 曲目总数 |
| `completed` | int | 已完成数 |
| `failed` | int | 失败数 |
| `current` | string \| null | 当前正在下载的来源 |
| `message` | string | 状态描述 |
| `save_dir` | string | 实际落盘目录 |
| `results` | object[] | 成功项（含 `source`/`title`/`artists`/`save_dir`） |
| `errors` | string[] | 错误信息列表 |

---

## 接口列表

### GET /api/v1/health

健康检查，无需鉴权。

**响应 200**
```json
{ "ok": true, "musicdl": "2.13.4" }
```

---

### GET /api/v1/sources

列出全部音乐源及其能力与当前可用性。

**响应 200**：`SourceInfo[]`

---

### GET /api/v1/search

按关键词聚合搜索（多源并发，单源失败不拖垮整体）。

**查询参数**

| 参数 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `keyword` | 是 | — | 搜索词（歌名/歌手/专辑） |
| `sources` | 否 | 默认五源 | 逗号分隔源名，如 `NeteaseMusicClient,QQMusicClient` |
| `limit` | 否 | 20 | 返回条数上限 |

**响应 200**：`SearchResponse`（含 `keyword`/`total`/`tracks`/`failed_sources`）

---

### GET /api/v1/playlist

解析歌单 URL，返回歌单内全部曲目（仅 `supports_playlist=true` 的源可用）。

**查询参数**：`url`（必填）、`source`（可选）

**响应 200**：`Track[]`；**400**：解析失败

**重要提示**：该接口为**同步阻塞**，耗时随歌单规模线性增长（实测 42 首约 50 秒）。客户端**超时建议 ≥ 10 分钟**，建议请求头加 `Connection: close` 避免长连接被中间设备挂起。大歌单建议改用 MCP 异步方式。

---

### POST /api/v1/downloads

提交下载任务（**异步**，立即返回 `task_id`）。

**请求体**：`{"tracks": [...], "subdir": "可选"}`，`tracks` 元素为含 `raw` 的完整 Track。

**响应 200**：`DownloadTask`；**400**：`tracks` 为空

---

### GET /api/v1/downloads/{task_id}

查询单个任务状态与进度。**响应 200**：`DownloadTask`；**404**：任务不存在。

---

### GET /api/v1/downloads

任务列表（内存中，按时间倒序）。参数：`limit`（默认 20）。

---

### POST /api/v1/downloads/{task_id}/cancel

取消任务。**仅对 `pending` 状态有效**；`running` 任务因 musicdl 中断能力有限，无法可靠取消。

---

### GET /api/v1/history

历史任务记录（SQLite 持久化）。参数：`limit`（默认 50）。

---

## 错误码

| 状态码 | 场景 |
|---|---|
| 200 | 成功 |
| 400 | 参数错误（如 `tracks` 为空、歌单解析失败） |
| 401 | 鉴权失败 |
| 404 | 资源不存在 |
| 422 | 请求体/查询参数格式错误 |
| 500 | 服务端内部错误 |

---

## 注意事项与已知限制

1. **下载落盘目录**：实际文件由 musicdl 按其规则组织在 `save_dir` 下（含 `.lrc` 歌词），并非严格按 `Track.title` 命名。
2. **单源失败降级**：聚合搜索时单个源异常不会导致整体失败，会在 `failed_sources` 中体现。
3. **下载中断能力有限**：musicdl 不支持可靠的下载中取消，故 `cancel` 仅对 `pending` 有效。
4. **歌单解析为同步接口**：大歌单耗时较长，客户端需设大超时。
5. **cookies 依赖源**：`QQMusicClient`、`TIDALMusicClient`、`MOOVMusicClient`、`AppleMusicClient`、`FMAMusicClient` 需登录 cookies；`MituMusicClient`、`BuguyyMusicClient`、`YinyuedaoMusicClient`、`GequbaoMusicClient` 的无损音质需夸克网盘 cookies。未配置时 `/sources` 会标记 `available=false`。
6. **外部工具**：HLS 下载依赖 `N_m3u8DL-RE`，YouTube 下载依赖 `Node.js`（Docker 镜像已内置）。
