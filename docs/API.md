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
| `artist_img_url` | string \| null | 艺人头像 URL（源提供时；归档用于在艺人目录写 `artist.*`） |
| `lyric` | string \| null | 歌词文本（若有） |
| `raw` | object | musicdl 原始 `SongInfo` dict（服务端下载上下文） |

> 说明：`raw` 字段体积较大且各源结构不一。搜索/歌单结果会在服务端缓存 1 小时，
> **提交下载时只需回传 `id`**，服务端按缓存自动补全 `raw`（缓存在服务重启后失效，需重新搜索）；
> 含 `raw` 的完整 Track 直传也兼容（此时以传入值为准，不查缓存）。

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
| `results` | object[] | 成功项（含 `source`/`title`/`artists`/`album`/`ext`/`cover_url`/`file`/`save_dir`；`file` 为实际落盘音频文件名，未落盘为 null） |
| `errors` | string[] | 错误信息列表 |
| `manifest_path` | string \| null | 专辑任务产出的 `manifest.json` 路径（单曲任务为 null） |
| `library` | string \| null | 目标库名；单曲任务传入时下载完成后自动归档到该库 |

### AlbumSummary（专辑摘要）

`GET /api/v1/albums/search` 的返回元素。

| 字段 | 类型 | 说明 |
|---|---|---|
| `collection_id` | string | 专辑 id：iTunes collectionId（纯数字），或中文源的 `netease:xxx` / `qq:xxx` 前缀 id；`get_album_info`/`download_album` 的入参 |
| `title` | string | 专辑名 |
| `artists` | string[] | 艺人列表 |
| `release_date` | string \| null | 发行日期（ISO） |
| `track_count` | int | 曲目数 |
| `cover_url` | string \| null | 高清封面 URL（600x600） |
| `genre` | string \| null | 流派 |
| `description` | string \| null | 专辑简介（来自网易云/QQ 补充，可能为空） |
| `meta_source` | string | 元数据来源：`itunes` / `netease` / `qq` / `itunes+netease` / `itunes+qq` |

### AlbumInfo（专辑详情）

继承 AlbumSummary 全部字段，另含：

| 字段 | 类型 | 说明 |
|---|---|---|
| `tracks` | AlbumTrack[] | 官方曲目表，按 disc/track 排序；元素含 `disc`/`track`/`title`/`artists`/`duration_s` |
| `storefront` | string \| null | 实际命中曲目表的 iTunes storefront（如 `CN`/`HK`/`US`） |

> 专辑元数据来源为 iTunes 官方 Search/Lookup API（免 key）。不同 storefront 曲库与语言不同：CN 命中时为简体中文，HK/TW 为繁体（服务端下载编排时已自动转简体匹配），US/JP 可能为罗马音。

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

### GET /api/v1/libraries

列出全部归档目标库（命名库根）：默认库 `default`（`library_root`）+ `extra_library_roots` 配置的命名附加库。归档/下载时传 `library` 参数选择目标库，留空用默认库。

**响应 200**

```json
[
  {"name": "default", "root": "/library", "default": true},
  {"name": "singles", "root": "/singles", "default": false}
]
```

> 设计约束：调用方只能传**库名**（白名单），不接受裸路径，防止任意路径写入。命名库在 `config.yaml` 的 `extra_library_roots` 中配置（如 `singles: "/singles"`），Docker 部署需同时挂载对应卷。

---

### POST /api/v1/downloads

提交下载任务（**异步**，立即返回 `task_id`）。

**请求体**

| 字段 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `tracks` | 是 | — | 待下载曲目列表；每项**只需 `id`**（服务端按搜索缓存补全），含 `raw` 的完整 Track 也兼容 |
| `subdir` | 否 | 自动组织 | 下载根目录下的子目录 |
| `library` | 否 | — | 目标库名（见 `/libraries`）；传入则下载完成后**自动归档**到该库（单曲结构 `{库根}/{艺人}/{曲名.ext}`） |
| `max_size_mb` | 否 | 配置文件 | 单文件体积上限（MB）；>0 生效且优先于 `config.yaml` 的 `max_size_mb`，0/空不限。超限曲目跳过并记入 `errors`，全部超限返回 400 |

**响应 200**：`DownloadTask`；**400**：`tracks` 为空 / 未知库名 / 全部曲目体积超限 / 仅传 `id` 但缓存未命中（需重新搜索）

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

### GET /api/v1/albums/search

按专辑名搜索专辑。iTunes 优先；iTunes 无结果或中文覆盖不足（关键词含中文而结果无中文）时自动回退网易云 → QQ，首个非空中文源的结果追加在 iTunes 结果之后（总数不超 limit）。

**查询参数**

| 参数 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `keyword` | 是 | — | 专辑名 |
| `artist` | 否 | — | 艺人名（叠加可提高准确度） |
| `limit` | 否 | 10 | 返回条数上限 |

**响应 200**：`AlbumSummary[]`；**502**：元数据接口异常

---

### GET /api/v1/albums/{collection_id}

获取专辑详情与官方曲目表。`collection_id` 按前缀路由：无前缀走 iTunes（storefront 链 CN→HK→TW→US→JP 兜底取首个有曲目的），`netease:`/`qq:` 前缀直接取对应中文源。iTunes 各 storefront 均无曲目时，自动用「专辑名+艺人」在网易云/QQ 找同专辑整体接管（含曲目表）；iTunes 命中时也会尽量合并中文源的专辑简介（`description`）与中文显示名（罗马音名按 CJK 规则替换），命中后 `meta_source` 为 `itunes+netease`/`itunes+qq`。

**响应 200**：`AlbumInfo`；**404**：各来源均无该专辑曲目；**502**：元数据接口异常

---

### POST /api/v1/albums/{collection_id}/download

专辑整单下载（**异步**，立即返回 `task_id`）。服务端编排：逐曲在音乐源中搜索 → 打分消歧（标题/歌手/专辑/时长，阈值 0.6，低于阈值记 `unmatched` 不强行下载）→ **音质择优**（与最高分相差 ≤0.1 的同分段候选中优先无损 flac 等，没有合格无损才选 MP3；分数明显更高的候选不受音质影响）→ 按曲目序号命名落盘 → 下载封面 → 产出 `manifest.json`。

**请求体**：`{"sources": ["可选，源名列表"], "subdir": "可选，默认'{艺人} - {专辑}'", "album_title": "可选，显示用专辑名覆盖", "artist": "可选，显示用艺人名覆盖", "max_size_mb": "可选，单文件体积上限（MB）；>0 优先于配置，0/空不限。超限不是硬剔除：优先选不超限的合格候选，无合格不超限候选时才放宽限制选超限最高分（保专辑完整性），manifest 标注 oversized_relaxed"}`

> `album_title`/`artist` 用于应对 iTunes 罗马音专辑名（如 "Kou Shi Xin Fei"）：传入中文名后写入 manifest 的 `album.display_title`/`display_artist`，归档时作为目录名与 ALBUM/ARTIST tag 使用。

> **singles 库复用**：配置了 `singles` 命名库（`extra_library_roots`）时，逐曲匹配前会先在 singles 库（`{singles根}/{艺人}/{曲名.ext}`）查找同专辑曲目——保守匹配（曲名与文件名相似度 ≥0.85，有 tag TITLE/ALBUM/时长时交叉校验），命中则**不搜索不下载**，直接硬链接（失败回退复制）进下载目录，manifest 的 `match.source` 为 `"singles"` 且带 `reused_from` 原路径。归档成功后该曲目从 singles 库迁移（删除源文件与同名 `.lrc`，并清理空艺人目录），见 `POST /api/v1/albums/archive`。

**响应 200**：`DownloadTask`（进度用 `GET /api/v1/downloads/{task_id}` 查询）；**400**：曲目表为空；**404/502**：同专辑详情接口

**落盘产物**（`save_dir` 下）：

- 音频文件按 `{track:02d} {曲名}.{ext}` 命名（多 Disc 专辑为 `{disc}-{track:02d} {曲名}.{ext}`），含 `.lrc` 歌词；
- `cover.jpg`（高清封面）；
- `manifest.json`：结构化清单（见下），替代解析 musicdl 私有的 `download_results.pkl`。

**manifest.json 格式**

```json
{
  "task_id": "…", "created_at": 1755000000.0,
  "album": { "collection_id": "…", "title": "…", "artists": ["…"], "release_date": "…",
             "track_count": 10, "cover_url": "…", "genre": "…", "storefront": "HK",
             "meta_source": "itunes" },
  "cover": "cover.jpg",
  "tracks": [
    { "disc": 1, "track": 1, "title": "…", "artists": ["…"], "duration_s": 234.3,
      "status": "ok | unmatched | failed",
      "match": { "source": "…", "track_id": "…", "title": "…", "artists": ["…"],
                 "album": "…", "ext": "flac", "quality": "lossless", "quality_tier": 3,
                 "score": 0.92, "candidates": 5, "oversized_filtered": 0 },
      "file": "01 曲名.flac", "ext": "flac", "size_bytes": 30000000, "error": null }
  ],
  "summary": { "total": 10, "ok": 9, "unmatched": 1, "failed": 0 }
}
```

> `match.score` 与 `match.candidates` 供人工/Agent 复核置信度；`unmatched`/`failed` 的 `error` 写明原因（无候选、低于阈值、下载未落盘等）。
> 复用 singles 库命中的曲目，`match.source` 为 `"singles"` 且另含 `reused_from`（singles 库内原路径，归档成功后该源文件被删除）；此类曲目没有 `score`/`candidates` 字段。

---

### POST /api/v1/albums/archive

把专辑下载产物归档进媒体库（**同步**，秒级返回）。以 `manifest.json` 为输入契约：硬链接（CIFS/跨设备自动回退复制）入库 → 断链后写 tag → 嵌封面/歌词 → 生成 `cover.jpg` 与 `album_info.txt`。

**专辑名/艺人名解析链**（应对 iTunes 罗马音专辑名，如 "Kou Shi Xin Fei"）：显式参数 > manifest 的 `display_title`/`display_artist` > **自动推断** > iTunes 原名（转简体）。自动推断对所有 ok 曲目候选的专辑名/艺人名做多数表决，仅在占比过半、原名不含中文且表决结果含中文时生效（原名已对就不动，防"范特西PLUS"式再版名噪音）。因此**旧 manifest 重跑一次归档即可自动纠正拼音目录名，无需重新下载**。

**前置**：`config.yaml` 配置 `library_root` 且容器已挂载媒体库卷，否则返回 400。

**请求体**

| 字段 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `task_id` | 二选一 | — | 专辑下载任务 ID（任务在内存中时可用） |
| `manifest_path` | 二选一 | — | manifest.json 绝对路径（服务重启后用这个） |
| `overwrite` | 否 | false | 目标已存在时是否覆盖重建；默认跳过（幂等） |
| `album_title` | 否 | — | 显示用专辑名覆盖（最高优先级，影响目录名与 ALBUM tag） |
| `artist` | 否 | — | 显示用艺人名覆盖（最高优先级，影响目录名与 ARTIST tag） |
| `library` | 否 | 默认库 | 目标库名（见 `/api/v1/libraries`）；未知库名返回 400 |

**响应 200**：`ArchiveResult`

```json
{
  "status": "success | partial | failed",
  "library_dir": "/library/周杰伦/范特西 - Single",
  "summary": {"linked": 3},
  "tracks": [
    {"disc": 1, "track": 1, "title": "蜗牛", "target": "01 - 蜗牛.flac",
     "action": "linked | copied | skipped | failed | tag_unsupported", "error": null}
  ],
  "errors": []
}
```

**库内目录结构**（对齐 Navidrome 约定）：

```
{library_root}/{艺人}/{专辑}/
├── 01 - 曲名.flac          # tag：ARTIST/ALBUMARTIST/ALBUM/TITLE/DATE/TRACKNUMBER n/N/COMMENT，嵌封面歌词
├── cover.jpg               # 多 Disc 时每个 CDx 子目录也有一份
├── album_info.txt
├── lyrics/                 # sidecar .lrc 平铺
└── CD1/, CD2/ ...          # 仅多 Disc 专辑，内含 NN - 曲名.ext 与 cover.jpg
```

> 设计约束：归档**不会修改下载目录的源文件**（硬链接文件改 tag 前先断链）；`COMMENT` 统一写为 `archive_comment` 配置值（默认 `yangds整理`），覆盖平台水印；仅 flac/mp3 写 tag，其他格式文件照入库但记 `tag_unsupported`。

---

### POST /api/v1/tracks/archive

把**单曲下载任务**的产物归档进媒体库（**同步**，秒级返回）。输入为单曲下载任务 ID（其 `results` 含实际落盘文件名）；`submit_download` 传了 `library` 时已自动归档，本接口用于事后补归档或换库重归档。

**请求体**

| 字段 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `task_id` | 是 | — | 单曲下载任务 ID（任务在内存中；服务重启后需重新下载） |
| `library` | 否 | 默认库 | 目标库名（见 `/api/v1/libraries`） |
| `overwrite` | 否 | false | 目标已存在时是否覆盖重建；默认跳过（幂等） |

**响应 200**：`ArchiveResult`（同专辑归档）；**400**：任务不存在 / 未知库名

**单曲库内结构**：`{库根}/{艺人}/{曲名.ext}`，同名 `.lrc` 放旁边并嵌入 tag；不写曲目序号（无 TRACKNUMBER/DISCNUMBER），`ALBUM` 用候选专辑名，`DATE` 跳过；封面从候选 `cover_url` 下载嵌入。

---

### POST /api/v1/library/replace_track

专辑指定曲目重搜替换（**同步**，含一次搜索+下载，耗时数十秒）。在库内专辑目录中定位曲目，沿用专辑匹配打分逻辑重新搜索，新候选音质分档（无损 3 / ≥320k 2 / 其他 1）**高于现有文件**或 `force=true` 时才替换，否则保留原文件。替换后曲目序号/专辑/艺人/日期沿用旧 tag，封面沿用专辑目录 `cover.*`，新下载歌词更新 `lyrics/` 并嵌入 tag；临时下载目录用完即清。

**请求体**

| 字段 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `library` | 否 | 默认库 | 库名（见 `/api/v1/libraries`） |
| `artist` | 是 | — | 艺人名（对应库内一级目录） |
| `album` | 是 | — | 专辑名（对应库内二级目录） |
| `track` | 是 | — | 曲目序号（如 `3`）或曲名；多 Disc 专辑可用 `"D-NN"` 形式消歧（如 `"2-03"` 指 CD2 的第 3 首，无该 CD 子目录时退回主目录匹配） |
| `sources` | 否 | 默认五源 | 参与搜索的源名列表 |
| `force` | 否 | false | 新候选音质不高于现有版本也强制替换 |
| `max_size_mb` | 否 | 配置文件 | 单文件体积上限（MB）；>0 优先于配置，0/空不限 |

**响应 200**

```json
{
  "status": "success",
  "action": "replaced | kept | unmatched | failed",
  "old": {"file": "周杰伦/范特西/01 - 爱在西元前.mp3", "ext": "mp3", "tier": 2},
  "new": {"source": "…", "title": "…", "ext": "flac", "quality": "lossless",
          "tier": 3, "score": 0.92, "file": "周杰伦/范特西/01 - 爱在西元前.flac"},
  "error": null
}
```

> `action` 语义：`replaced` 已替换；`kept` 新候选音质不高于现有（未动文件）；`unmatched` 搜索未命中合格候选；`failed` 下载未落盘。后三种 `old` 文件均保持不变，`error` 写明原因。

**400**：未知库名 / 专辑目录不存在 / 曲目未命中 / 目录名解析后越出库根（如 `..`；`album="."` 不拦截，按整艺人目录处理）

---

### POST /api/v1/library/cleanup

清理媒体库中的专辑/曲目文件（**同步**）。粒度：`tracks` 指定曲目 > `album` 整专辑 > `artist` 整艺人。删除曲目后**空目录自底向上一并清理**（不留空目录）：空 `CDx/` → 无音频残留的专辑目录（连同 `cover.jpg`/`album_info.txt`/`lyrics/`）→ 空艺人目录。删除文件时同名 `.lrc`（`lyrics/` 内与文件旁的）一并删除。

**请求体**

| 字段 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `library` | 否 | 默认库 | 库名（见 `/api/v1/libraries`） |
| `artist` | 是 | — | 艺人名（对应库内一级目录） |
| `album` | 否 | — | 专辑名（对应库内二级目录）；留空则清理整个艺人目录 |
| `tracks` | 否 | — | 要清理的曲目：序号（如 `3`）、`"D-NN"`（多碟消歧，同 replace_track）或曲名（相似度 ≥0.7 取最高分）；留空则清理整个专辑 |
| `dry_run` | 否 | false | 只报告将删除的项，不实际删除（**建议先跑一遍确认范围**） |

**响应 200**

```json
{
  "status": "success | partial",
  "dry_run": false,
  "deleted_files": ["/library/周杰伦/范特西/01 - 爱在西元前.flac"],
  "removed_dirs": ["/library/周杰伦/范特西"],
  "errors": []
}
```

> 部分文件删除失败时 `status` 为 `partial` 且 `errors` 非空；整专辑/整艺人删除与 `dry_run` 时 `removed_dirs` 报告将清理的目录。

**400**：未知库名 / 艺人或专辑目录不存在 / 曲目未命中 / 目录名解析后越出库根（如 `..`；`album="."` 不拦截，按整艺人目录处理）

---

### POST /api/v1/library/migrate_singles

把专辑库中**只有一个音频文件**的专辑目录（单曲专辑）迁移到 singles 库（**同步**）。目标结构 `{目标库根}/{艺人}/{曲名.ext}`（曲名取 tag TITLE，缺失时去文件名序号前缀）。迁移后重写 tag：清除序号类（TRACKNUMBER/TRACKTOTAL/DISCNUMBER/DISCTOTAL，仅 flac/mp3），保留 ALBUM/ARTIST/DATE/封面/歌词；`lyrics/` 或文件旁的同名 `.lrc` 一并移到目标旁；原专辑目录整目录删除，空艺人目录一并清理；目标已存在同名文件则跳过（记 `skipped`）。**艺人头像同步**：目标艺人目录无 `artist.*` 时从源艺人目录复制一份；若源艺人目录迁移后只剩头像（该艺人在专辑库已无专辑），头像直接搬到目标并清掉源艺人目录。

> 防护：若目标库根位于源库之内（如 singles 目录挂在专辑库下，或同一宿主目录经两个挂载点暴露为 `/library/singles` 与 `/singles`），该子树会被自动跳过（路径包含 + inode 比对双重判定），防止 singles 库内容被误判为单曲专辑"自我搬迁"。

**请求体**

| 字段 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `library` | 否 | 默认库 | 源库名（见 `/api/v1/libraries`） |
| `target_library` | 否 | `singles` | 目标库名 |
| `artist` | 否 | — | 限定单个艺人；留空扫描整个源库 |
| `dry_run` | 否 | false | 只报告将迁移的项，不实际迁移（**建议先跑一遍确认范围**） |

**响应 200**

```json
{
  "status": "success | partial | failed",
  "dry_run": false,
  "migrated": [{"from": "/library/周杰伦/范特西 - Single/01 - 蜗牛.flac",
                "to": "/singles/周杰伦/蜗牛.flac"}],
  "skipped": [{"from": "…", "to": "…", "reason": "目标已存在"}],
  "errors": []
}
```

> 全部失败时 `status` 为 `failed`，部分失败为 `partial`；`dry_run` 时 `migrated` 报告将迁移的项（不动文件系统）。

**400**：未知库名 / 指定艺人目录不存在

---

## 错误码

| 状态码 | 场景 |
|---|---|
| 200 | 成功 |
| 400 | 参数错误（如 `tracks` 为空、歌单解析失败、未知库名、全部曲目体积超限） |
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
7. **专辑元数据为 iTunes 单源**：小众/独立专辑可能查不到（404）；各 storefront 语言不一（HK/TW 繁体、US/JP 可能罗马音），匹配消歧时已做繁转简，但罗马音艺人名（如 `Jay Chou` vs `周杰伦`）会拉低歌手维度得分，可能导致冷门专辑 `unmatched`——此为有意保守策略，看 `manifest.json` 复核后可改走单曲补下。
8. **专辑下载耗时**：逐曲串行匹配（每曲一次聚合搜索），一张 10 首专辑全程约 5–10 分钟，属预期；任务为异步，客户端轮询即可。
