# 榜单目录（排行榜浏览 + 全量/挑选下载）设计

日期：2026-09-23
状态：待确认

## 背景与问题

服务已具备"给一个歌单 URL → 解析曲目 → 全量/挑选下载"的完整链路（`/api/v1/playlist` + `submit_download`），但缺少**目录层入口**：用户必须自己到平台 App 里找到榜单/歌单链接才能用。目标是让用户（或 Agent）能直接在服务内浏览 QQ 音乐、网易云音乐的排行榜，拿到曲目列表后选择全量下载或挑选下载。

关键事实（2026-09-23 实测验证）：

- **网易云的排行榜本质是歌单**：`GET https://music.163.com/api/toplist`（免登录，PC UA + Referer）返回全部榜单，每个榜单的 `id` 就是 playlist id，榜单详情直接复用现有 `parse_playlist`（`https://music.163.com/playlist?id={id}`）即可拿到带下载地址的 Track。
- **QQ 的排行榜是独立体系**：榜单列表 `GET https://c.y.qq.com/v8/fcg-bin/fcg_myqq_toplist.fcg`（免登录，Referer: y.qq.com）返回 `data.topList`（id/topTitle/picUrl/listenCount/前三首预览）；榜单详情 `GET https://c.y.qq.com/v8/fcg-bin/fcg_v8_toplist_cp.fcg?topid={id}&tpl=3&page=detail&type=top` 返回 `songlist[].data`（songmid/songname/singer/albumname/interval/pay 等，共约 300 首，含 `date` 更新日期）。
- **榜单详情接口只返回元数据，不含下载地址**，而 `normalize_song` 会跳过无 `download_url` 的条目（`app/search.py:112`）。QQ 榜单曲目需要逐曲解析下载地址——musicdl `QQMusicClient.parseplaylist` 已有现成模式：对每条原始曲目 dict 调 `_parsewithofficialapiv1(search_result=…)`，失败回退 `_parsewiththirdpartapis`（`qq.py:494-505`）。toplist_cp 的 `songlist[].data` 与歌单 songlist 条目同构，可直接套用。
- musicu 网关的 `toplist.ToplistServer/GetAllToplist` 实测被拒（code 500003），**不用**；用旧的 c.y.qq.com fcg 接口。

## 目标

- 浏览 QQ/网易云的官方排行榜目录（榜单名、id、封面、更新信息）
- 查看任一榜单的完整曲目列表（标准化 Track，落缓存可直接提交下载）
- 对榜单曲目全量下载或挑选下载（复用现有 `submit_download`，零改动）

## 非目标

- 第一期不做歌单关键词搜索（榜单接口稳定、范围清晰；歌单搜索作为第二期，见末节）
- 不做榜单定时刷新/订阅/自动下载
- 不改下载管线、不改归档管线
- 不绕付费墙：VIP/付费曲目在榜单里正常列出元数据，下载时按现有源能力自然失败或跳过（musicdl 已有此行为）

## 架构

### 新模块 `app/charts.py`

纯函数 + httpx 直连，不依赖 FastAPI，模式同 `app/qq_meta.py`/`app/netease_meta.py`：

- `list_charts(source: str) -> list[ChartSummary]`
  - `qq`：`fcg_myqq_toplist.fcg` → `ChartSummary(id=str(topid), name=topTitle, cover_url=picUrl, track_count=None, extra={"listen_count": …, "preview": [...]})`
  - `netease`：`/api/toplist` → `ChartSummary(id=str(playlist id), name, cover_url=coverImgUrl, extra={"update_frequency": …})`
- `get_chart_tracks(source: str, chart_id: str, limit: int | None = None) -> list[Track]`
  - `netease`：拼 `https://music.163.com/playlist?id={chart_id}` 调现有 `parse_playlist(url, source="netease")`；`limit` 在返回后截断（网易侧解析是全量的，无法分页，见"已知限制"）
  - `qq`：请求 `fcg_v8_toplist_cp.fcg`（支持 `song_begin`/`song_num` 分页，`limit` 映射为 `song_num=min(limit, 100)` 首页），逐条 `songlist[].data` 复用 QQMusicClient 的逐曲解析模式（`_parsewithofficialapiv1` + thirdpart 回退），产出的 SongInfo dict 过 `normalize_song("QQMusicClient", …)`；`cache_tracks` 落缓存
  - 返回 Track 列表（含 `raw`，已缓存，submit_download 可按 id 提交子集或全量）

### schemas（`app/schemas.py`）

新增 `ChartSummary`：`id / source / name / cover_url? / track_count? / extra(dict)`。Track 复用现有模型不改。

### REST（`app/main.py`）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/charts?source=qq\|netease` | 榜单目录；source 省略返回两平台合并列表 |
| GET | `/api/v1/charts/{source}/{chart_id}?limit=` | 榜单曲目（Track 列表，已缓存） |

错误处理沿用现有惯例：源不支持/参数非法 → 400；上游接口失败 → 502 附错误说明；网易反爬限流（code -462）沿用 `netease_meta.py` 的"视为未命中"策略，返回 502 提示稍后重试。

### MCP（`mcp_adapter.py`）

新增两个薄客户端工具：

- `list_charts(source?)` → 榜单目录
- `get_chart_tracks(source, chart_id, limit?)` → Track 列表

### 下载流程（零改动）

```
list_charts → get_chart_tracks（Track 已缓存）
  ├─ 全量：submit_download(tracks=全部, subdir=f"榜单-{name}-{date}")
  └─ 挑选：submit_download(tracks=子集或仅 id 列表, subdir=…, library?/max_size_mb?)
```

`subdir`、`library`（下载后自动归档）、`max_size_mb` 均沿用现有参数。

## 错误处理

- QQ toplist_cp 返回 `code != 0` → 502；逐曲解析失败的条目跳过并记 warning（与 musicdl parseplaylist 口径一致），不阻断整榜
- 网易 `/api/toplist` 或 playlist detail 限流/失败 → 502 + 说明；不重试（与现有模块一致，避免加剧限流）
- 榜单曲目中无下载地址的（VIP/付费/区域限制）在逐曲解析阶段自然被过滤，结果集即"可下载集"；调用方看到的数量可能少于榜单名义曲目数，属预期行为

## 测试

- `tests/test_charts.py`：参照 `test_cn_meta.py` 的样本录制风格——mock httpx 响应，验证 list_charts 字段映射（QQ topList / 网易 toplist）、QQ 榜单曲目解析（songlist[].data → Track）、limit 分页参数拼接、错误码路径
- 主链路手动 E2E：`list_charts` → 选一个榜单 → `get_chart_tracks` → 挑 2 首 `submit_download` → 查任务完成
- 回归：`pytest` 全绿 + `/api/v1/search` 冒烟不受影响

## 里程碑（开发计划）

- **第一期（本设计）**：榜单目录 + 榜单曲目 + 全量/挑选下载（REST + MCP + 测试 + README/API/MCP 文档更新）
- **第二期（另行立项）**：歌单关键词搜索（`GET /api/v1/playlists/search?keyword&source`：网易 `/api/search/get` type=1000，QQ `client_search_cp` 歌单类型），返回歌单摘要后走现有 `/api/v1/playlist` 解析
- **第三期（候选，与 ROADMAP 既有条目合并）**：歌单/榜单解析异步化——大榜单 300 首逐曲解析为同步阻塞（客户端需容忍长超时），与 ROADMAP "歌单批量下载异步化"是同一个问题，届时一并解决

## 已知限制 / 风险

- 网易榜单详情复用 parse_playlist，全量逐曲解析无法分页，大榜单慢（同步阻塞，与现有歌单接口同一已知限制）；QQ 侧通过 `song_begin`/`song_num` 支持分页
- QQ/网易网页接口可能变动或限流（与 `qq_meta.py`/`netease_meta.py` 同类风险），接口失效时榜单功能降级但不影响搜索/下载主链路
- 榜单曲目解析结果受源可用性影响：QQ 未配置 cookies 时无损/VIP 曲目可能解析不到下载地址而被过滤，结果集偏小属预期
