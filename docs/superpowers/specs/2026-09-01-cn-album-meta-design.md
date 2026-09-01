# 中文专辑元数据补充（网易云/QQ 网页接口）设计

> 对应 ROADMAP M4-1「后续待做」第一项：网易云/QQ 网页接口补充中文专辑与简介（iTunes 覆盖不足时）。
> 方案：来源编排器 + collection_id 命名空间（已与用户确认，网易云与 QQ 同期实现）。

## 背景与目标

- 现状：专辑元数据仅来自 iTunes（`app/itunes.py`）。iTunes 不提供专辑简介；部分中文专辑无收录、只有罗马音名或繁体曲目表；`archive.py` 的 `album_info.txt` 目前写死"简介暂缺"。
- 目标：
  1. **简介补充**：无论专辑来自哪个来源，尽量补上中文专辑简介（`description`）。
  2. **覆盖补充**：iTunes 搜索无结果 / 各 storefront 均无曲目时，回退网易云、QQ 取专辑元数据（含曲目表），保证专辑闭环仍可走通。
  3. **显示名补充**：iTunes 罗马音专辑名/艺人名可被中文源的正确中文名替换（与现有罗马音修复规则一致）。

非目标（YAGNI）：歌词/封面不从中文源取（现有链路够用）；不做 `get_artist_info`；来源优先级不做配置项（固定链条）。

## 已验证的接口（2026-09-01 实测）

### 网易云（`app/netease_meta.py`）

- 搜索：`POST https://music.163.com/api/search/get`，表单 `s={关键词}&type=10&limit=N`，需 `Referer: https://music.163.com` + UA。
  返回 `result.albums[]`：`id`、`name`、`publishTime`(ms)、`picUrl`、`artist.name`、`size`（曲目数）。
- 详情：`GET https://music.163.com/api/v1/album/{id}`，需移动端 UA + `Cookie: os=ios; appver=...`（PC UA/不带 cookie 实测返回空对象或 -462）。
  返回顶层 `songs[]`（`name`/`no` 序号/`cd` 碟号(字符串)/`dt` 毫秒时长/`ar[]` 艺人）+ `album`（`name`/`publishTime`/`description`/`picUrl`/`artist`/`company`）。
- **风险**：有反爬限流（实测连发后返回 `code: -462`）。必须 best-effort：失败/限流即降级，不得拖垮主链路。

### QQ 音乐（`app/qq_meta.py`）

- 搜索：`GET https://c.y.qq.com/soso/fcgi-bin/client_search_cp?t=8&w={关键词}&format=json&n=N`，需 `Referer: https://y.qq.com`。
  返回 `data.album.list[]`：`albumMID`、`albumName`、`singerName`、`publicTime`、`albumPic`、`song_count`。
- 详情：`POST https://u.y.qq.com/cgi-bin/musicu.fcg`，两个 module：
  - `music.musichallAlbum.AlbumInfoServer / GetAlbumDetail`（param `albumMid`）→ `req_1.data.basicInfo`（`albumName`/`publishDate`/`desc`）、`req_1.data.singer.singerList`（艺人）、`company`。
  - `music.musichallAlbum.AlbumSongList / GetAlbumSongList`（param `albumMid`/`albumID:0`（实测可省略真实 albumID）/`begin:0`/`num:100`/`order:2`）→ `req_1.data.songList[].songInfo`（`title`/`interval`(秒)/`index_album` 序号；多碟时 `belongCD` 可用，缺省视为 1）。
- 封面：搜索返回的 `albumPic` 尺寸段替换为 `T002R800x800M000{albumMid}.jpg`（与 `itunes._hi_res_cover` 同思路）。

## 架构

```
REST /albums/search ─┐
REST /albums/{id} ───┼─► app/meta.py（编排层）──► itunes.py（首选，现状不动）
REST /albums/{id}/download ─┘        ├──► netease_meta.py（回退/补充）
                                     └──► qq_meta.py（回退/补充）
```

### collection_id 命名空间

- 无前缀（纯数字）= iTunes，保持向后兼容；`netease:{id}`、`qq:{albumMID}` 为中文源专辑。
- `meta.search_albums()` 返回的 `AlbumSummary.collection_id` 带前缀；`meta.get_album()` 按前缀路由到对应客户端。前缀与数字 id 不可能冲突（iTunes collectionId 为纯数字）。

### 行为规则

1. **`search_albums(keyword, artist, limit)`**：
   - 先查 iTunes。若结果非空，且（关键词含 CJK 而全部结果标题不含 CJK）→ 视为覆盖不足，继续查网易云 → QQ，将首个非空中文源的结果**追加**在 iTunes 结果之后，总数上限仍为 limit（iTunes 已占满 limit 则不追加）。
   - iTunes 结果为空 → 依次回退网易云 → QQ，取第一个非空来源（上限 limit）。
   - 中文源查询失败仅跳过该源，不影响已有结果。

2. **`get_album(collection_id)`**：
   - `netease:` / `qq:` 前缀 → 直接路由对应客户端取详情（含曲目表、简介、封面）。
   - 无前缀（iTunes）：
     a. 走现有 storefront 链取曲目表；若所有 storefront 都无曲目（现抛 `LookupError` 的场景）→ 用 iTunes 摘要的「专辑名+艺人」在网易云 → QQ 搜索最相似专辑（标题归一化相似度最高者，≥0.6 才接受，否则维持 `LookupError`），命中则以中文源数据整体接管，`meta_source` 记为对应来源。
     b. iTunes 命中曲目表时：曲目表/发行日期/封面以 iTunes 为准，再同步（best-effort）用「专辑名+艺人」查网易云（失败再 QQ）取同专辑的 `description` 与中文显示名，合并进 `AlbumInfo`，命中时 `meta_source` 记为 `itunes+netease` 或 `itunes+qq`。
   - 简介匹配门槛：候选专辑标题与艺人的归一化相似度均 ≥0.6（相似度函数直接 import `album.py` 的 `_normalize`/`_sim` 包内复用，不另抽公共模块；`album.py` 不 import `meta.py`，无循环依赖），不满足则放弃补充，不报错。
   - **罗马音放宽**：iTunes 标题为罗马音时与中文名相似度天然为 0（如 "Kou Shi Xin Fei" vs "口是心非"），此时放宽为「发行日期前 10 位 + 曲目数」与候选精确一致即接受（中文源搜索本身已带艺人关键词收敛结果集）。

3. **显示名替换**：仅在原值不含 CJK 而中文源值含 CJK 时替换 title/artists（与 `infer_display_names` 的 CJK 保护同一规则，直接复用 `_CJK_RE`）。

### 数据模型变更（`app/schemas.py`）

- `AlbumSummary` 增加：
  - `description: Optional[str]`（专辑简介，可能为空）
  - `meta_source: str = "itunes"`（`itunes` / `netease` / `qq` / `itunes+netease` / `itunes+qq`）
- `AlbumInfo` 继承之；`AlbumTrack` 不变。
- `collection_id` 字段描述更新为「iTunes collectionId 或带前缀的中文源 id（netease:xxx / qq:xxx）」。

### 下游联动

- `app/album.py`：
  - `submit_album_download` / `_run_album` 的 `album_dict` 中 `meta_source` 由硬编码 `"itunes"` 改为取 `album.meta_source`（`album.py:401`）。
  - manifest 的 `album` 自动带上 `description`（`model_dump` 自然包含），旧 manifest 无此字段，向后兼容。
  - `download_album` REST 端点（`main.py`）与 MCP 工具无需改签名——`collection_id` 前缀透传即可。
- `app/archive.py` `_write_album_info`：
  - 有 `description` 时写入简介段；无则不再输出"简介暂缺"占位行（直接省略）。
  - 「iTunes 原名」括号标注改为按 `meta_source` 判断：仅 iTunes 系来源才标注。

### 错误处理

- 中文源一切失败（网络/限流/解析）→ 记 warning 日志并降级，主链路行为与今天一致。
- 三源全部失败才抛错：search → 502；get_album → 404（保持现有语义）。
- 超时：每源 10s；`get_album` 的简介补充只允许尝试一个中文源成功后即停（命中网易云就不查 QQ）。

### 测试（tests/，pytest + httpx mock）

- `test_cn_meta.py`（新）：
  - netease/qq 搜索与详情的解析（用 2026-09-01 实测的响应结构做 fixture，离线 mock）。
  - 回退链：iTunes 空 → netease；iTunes 空 + netease 空 → qq；全空 → LookupError/空列表。
  - 简介合并：iTunes 命中 + netease 有简介 → `meta_source="itunes+netease"`；相似度不达标不合并。
  - CJK 显示名替换规则。
  - 中文源超时/限流（-462）→ 降级不抛错。
- 既有 `test_libops.py` 回归。

## 文件清单

| 文件 | 变更 |
|---|---|
| `app/netease_meta.py` | 新增：搜索 + 详情客户端 |
| `app/qq_meta.py` | 新增：搜索 + 详情客户端（musicu.fcg 双 module） |
| `app/meta.py` | 新增：编排层（回退链、简介合并、id 前缀路由） |
| `app/itunes.py` | 不动（仅被 meta.py 调用） |
| `app/schemas.py` | `AlbumSummary` 加 `description`/`meta_source` |
| `app/main.py` | `/albums/search`、`/albums/{id}` 改调 `meta.py`（其余不动） |
| `app/album.py` | manifest `meta_source` 取真实来源 |
| `app/archive.py` | `_write_album_info` 写简介、去占位行、原名标注按 meta_source |
| `docs/API.md`、`docs/MCP.md` | 补充字段与 id 前缀说明 |
| `ROADMAP.md` | 标记该待做项完成 |
| `tests/test_cn_meta.py` | 新增 |
