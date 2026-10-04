# 飞牛音乐歌单同步设计

日期：2026-10-04
状态：待评审（已按评审意见修订一轮：补原子操作端点）

## 背景与问题

服务已具备"榜单浏览 → 批量下载 → 归档入库"链路（榜单目录 2i 第一期已上线），但下载入
库的歌曲在飞牛音乐 App 里只是散落在曲库中，缺少**歌单**这一组织维度。目标：批量下载
一批歌曲时，允许直接把它们追加到飞牛音乐的某个既有歌单，或新建歌单管理——典型场景：
通过榜单功能拉一批歌后，新建"榜单-巅峰榜流行指数-2026-10"歌单。同时也要支持脱离下
载流程的原子管理：把曲库里任意已入库曲目加进既有歌单、查看歌单内容。

## 关键事实（2026-10-04 本机实证）

以下来自用户实机 HAR（fnos.example.com:5667，62 请求）+ 针对生产 NAS 的只读/临时
写探测，以及 fnos_music_ext / fn-music-bridge / FnMusicEnhance 三个逆向项目交叉佐证：

- **API 基座**：飞牛音乐后端经 nginx 暴露在 `{base_url}/music/api/v1/...`，响应信封
  `{code, msg, data}`，`code=0` 成功；未登录/凭证失效返回 `code=99999`（INVALID TOKEN）。
- **认证**：应用级独立账号体系。`POST /music/api/v1/user/password-login`，body
  `{"username", "password": sha256_hex(明文), "deviceId"}` → `data.userToken`；之后每个
  请求带 cookie `music-token=<userToken>`（fn-music-bridge api.go 佐证字段名；
  password-login 端点本身未在本机实测，列入 E2E 验证项）。
- **authx 签名头本机不校验**（2026-10-04 探测矩阵 6 项全过）：重放旧 authx、同一 authx
  换 payload、伪造 sign、完全不带 authx，读写端点（track/list、playlist/create、
  playlist/delete）均 `code:0`。**本设计不实现 authx**；已验证算法存档备未来收紧时启用：
  `authx = nonce=<6位>&timestamp=<毫秒>&sign=md5("{prefix}_{path}_{nonce}_{ts}_{md5(payload)}_{key}")`，
  GET payload 为原始 query string，POST JSON 为原始 body（HAR 14/15 命中，唯一 MISS 为
  multipart 上传，其 payload 固定 `{}`）；prefix/key 默认值见 fn-music-bridge signer.go。
- **曲目 guid 纯 API 精确解析**：`GET /music/api/v1/track/list?page=1&size=200&sort=createdAt,desc`
  每条曲目带 `guid` 与 **`audioSpec.path`（宿主机绝对路径**，如
  `/vol1/1000/Media/Music/王菲/我也不想这样.flac`），另有 title/artists/duration/size 可
  辅助校验。按宿主路径精确匹配，与读 music.db 等效但零耦合（**本设计不挂载不读取
  music.db**）。
- **歌单写契约**（HAR 实测）：
  - 建单 `POST /music/api/v1/playlist/create`，body `{"name": "..."}`（`coverId` 可选，
    缺省 null）→ `data.guid`
  - 加曲 `POST /music/api/v1/playlist/add-track`，body `{"guid": ..., "trackGUIDs": [...]}`，
    批量 → `code:0, data:null`
  - 删单 `POST /music/api/v1/playlist/delete`（探测用，本设计不暴露）
  - 列表 `GET /music/api/v1/playlist/list` → `data.list[{guid,name,coverId,...}]`
  - 单内曲目 `GET /music/api/v1/track/playlist-detail/list?playlistGUID=...&page=1&size=300`
    → `data.list`（追加前去重依据）
- **m3u 死路**（社区口径，官方无文档）：飞牛音乐不自动扫描收编媒体库 m3u，放弃。

## 目标

- 批量下载（`submit_download`）时可传 `playlist` 歌单名：下载+自动归档完成后，把入库
  曲目同步进飞牛音乐歌单（不存在则新建，存在则按 guid 去重追加）
- 原子管理端点与 MCP 工具（脱离下载流程，覆盖"已有歌曲 → 既有歌单"等事后管理）：
  列出飞牛歌单；查看歌单内曲目；按下载任务或路径清单建/补歌单；把任意已入库曲目
  （按容器路径或 fnos guid）追加到既有歌单
- username/password 自动登录，token 持久化，失效自动重登兜底（QQ 保活同一哲学）

## 非目标

- 不读不写 music.db，不实现 authx（均已论证无必要）
- 不做歌单封面设置、歌单删除/改名、歌单内曲目移除（API 已知，按需后续）
- 不做按标题/艺人模糊搜歌加单：fnos 搜索接口未取证，且容器路径/guid "指哪打哪"已
  覆盖本期场景，后续需要再立项
- 不接 `download_album` 流程（专辑歌单化后续同模式另立项）
- 不做多用户：歌单建在配置的单个音乐应用账号下
- 第一期不做 token 定时体检；采用惰性重登（99999 触发）

## 架构

### 新模块 `app/fnos.py`

纯函数+httpx 客户端类，模式同 `app/qqauth.py`（状态持久化）与 `app/qq_meta.py`（httpx 直连）：

```
class FnosClient:
    def __init__(self, base_url, username, password, path_map, scan_wait_s=120, verify_tls=False)

    # —— 认证状态机 ——
    ensure_token()        # 有持久化 token 直接用；没有走 login()
    login()               # password-login（sha256 hex + deviceId），持久化 state
    request(method, path, **kw)  # 带 music-token cookie 调 API；code==99999 → login() 后重试一次

    # —— 歌单 ——
    list_playlists() -> list[dict]           # GET playlist/list
    create_playlist(name) -> str             # POST playlist/create → guid
    playlist_tracks(guid) -> list[dict]      # GET track/playlist-detail/list 分页 → 完整曲目对象
    add_tracks(playlist_guid, track_guids)   # POST playlist/add-track（批量）

    # —— guid 解析 ——
    resolve_guids(container_paths) -> dict[path, str|None]
        # 容器路径 →(path_map)→ 宿主路径；track/list?sort=createdAt,desc 分页扫描，
        # 全部命中或页尽为止；未命中在 scan_wait_s 内轮询重试（等飞牛 watcher 扫描）
        # （新入库曲目倒序首页即中；老曲目会翻较多页，属一次性成本）

    # —— 编排 ——
    sync_playlist(name, container_paths) -> dict
        # ensure 语义：resolve → 未命中清单；name→guid（list_playlists，无则 create_playlist）；
        # playlist_tracks 取已有按 guid 去重；add_tracks 追加；返回
        # {status: ok/partial/failed, playlist_guid, playlist_name,
        #  added: n, already: n, unresolved: [paths], error: str|None}
        # （unresolved 非空 → partial；认证/网络失败 → failed 附 error）
    append_tracks(name, container_paths=[], guids=[]) -> dict
        # 严格语义：name 必须已存在，无则抛 FnosPlaylistNotFound（→ 404，
        # 防止打错字静默建新单）；resolve paths 并与 guids 合并（guids 免解析直达）；
        # 去重追加；返回结构同 sync_playlist
    playlist_detail(name) -> dict
        # name→guid（无则 FnosPlaylistNotFound）；playlist_tracks 全量；返回
        # {playlist_guid, playlist_name, count, tracks: [{guid,title,artists,...}]}
```

### token 持久化（仿 `app/qqauth.py`）

- 状态文件 `data/fnos_music_state.json`：`{userToken, deviceId, username, login_at}`；
  原子写（tmp+replace）。deviceId 首次生成 uuid4 后持久复用。
- 读取优先级：状态文件 > 触发 login；不回写 config.yaml；改配置 username/password 后
  下次 99999 时按新凭证重登并重置状态（比对 state.username 与配置，不符直接重登）。
- 惰性重登：仅在 `code==99999` 时 login() 并重试一次；仍失败抛 FnosAuthError。

### 配置（`config.yaml` 新增段）

```yaml
fnos_music:
  base_url: "https://192.168.254.112:5667"   # 飞牛 nginx 入口（HAR 实测端口 5667，按部署改）
  username: "音乐应用账号"
  password: "音乐应用密码"                      # 内网明文，与 cookies 同级；不入库不回写
  verify_tls: false                           # 自签证书
  scan_wait_s: 120                            # 等飞牛扫描新入库文件的最长秒数
  path_map:                                   # 容器内库根 → 飞牛侧宿主路径（audioSpec.path 前缀）
    "/library": "/vol1/1000/Media/Music"
    "/singles": "/vol1/1000/Media/Singles"
```

未配置 `fnos_music` 段：功能整体禁用，相关端点 400「未配置 fnos_music」，下载主链路零影响。

### 集成点

**1. `submit_download` 加 `playlist` 参数（主场景）**

- `POST /api/v1/downloads` body 新增 `playlist: str?`；MCP `submit_download` 同步加参。
- 约束：`playlist` 必须搭配 `library`（不入库的曲目飞牛音乐管不到；单传 playlist 报 400）。
- 时机：任务下载完成且自动归档完成后，取本任务**成功入库**曲目的容器内落盘路径集合，
  调 `sync_playlist(playlist, paths)`；结果写入任务 `errors`/`message` 与新字段
  `playlist_result`（added/already/unresolved）。
- 失败隔离：歌单同步失败（飞牛不可达/认证失败/扫描超时）只记任务 errors 与新字段，
  不影响下载与归档结果。

**2. 独立端点（原子管理）**

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/fnos/playlists` | 飞牛歌单列表（guid/name/曲目数） |
| GET | `/api/v1/fnos/playlists/{name}/tracks` | 歌单内曲目（guid/title/artists/...）；歌单不存在 → 404 |
| POST | `/api/v1/fnos/playlists` | 建/补歌单（ensure 语义）：body `{name, task_id?, paths?}`，按任务成功入库曲目和/或容器路径清单建单（不存在则建）并追加，幂等去重；task_id/paths 均缺 → 只建空歌单 |
| POST | `/api/v1/fnos/playlists/{name}/tracks` | 严格追加到既有歌单：body `{paths?, guids?}`（至少其一，否则 400）；歌单不存在 → 404（不静默建单）；解析失败的路径记 `unresolved`（status=partial），其余正常追加 |

通用：未配置 fnos_music → 400；飞牛侧失败（不可达 / code!=0 非 99999）→ 502 附说明。
歌单一律按 name 定位（用户把手），同名取列表首个，与 sync_playlist 语义一致。

**3. MCP（`mcp_adapter.py`）**

- `submit_download` 加 `playlist?` 参数（docstring 写明需搭配 library）
- `list_fnos_playlists()` → 飞牛歌单列表
- `get_fnos_playlist_tracks(name)` → 歌单内曲目
- `create_fnos_playlist(name, task_id?, paths?)` → 建/补歌单结果
- `add_fnos_playlist_tracks(name, paths?, guids?)` → 严格追加结果

### 数据流（榜单场景）

```
get_chart_tracks(qq/4) → tracks 落缓存
submit_download(tracks, library="singles", playlist="榜单-巅峰榜-2026-10")
  → 下载 → 归档到 /singles/{艺人}/{曲名}
  → path_map 转宿主路径 → 轮询 track/list（createdAt 倒序，新曲目首页即中）
  → playlist/list 查无"榜单-巅峰榜-2026-10" → playlist/create → add-track
  → 飞牛音乐 App 出现歌单
```

### 数据流（原子追加场景）

```
add_fnos_playlist_tracks("我的收藏", paths=["/singles/阿桑/叶子.flac"])
  → path_map 转宿主路径 → track/list 分页解析 guid（老曲目多翻几页）
  → playlist/list 找到"我的收藏"（不存在 → 404，不静默建单）
  → playlist-detail/list 取已有去重 → add-track → 返回 added/already/unresolved
```

## 错误处理

- 未配置 `fnos_music` → 端点 400「未配置 fnos_music」；`submit_download` 传 playlist → 400
- 单传 `playlist` 不带 `library` → 400
- 严格追加 / 歌单详情：歌单名不存在 → 404；`paths`/`guids` 均缺 → 400
- 飞牛不可达 / `code!=0`（非 99999）→ 独立端点 502 附说明；`submit_download` 场景记任务
  errors + `playlist_result.status="failed"`
- 认证：`code==99999` → 自动重登重试一次；重登失败 → 同上按失败处理并附「检查
  fnos_music 账号密码」
- 扫描超时：`scan_wait_s` 内未解析到的路径记入 `unresolved`，其余曲目正常加单
  （`status="partial"`），调用方可稍后重试（幂等去重）

## 测试

- `tests/test_fnos.py`：httpx 层 mock，覆盖——password-login 请求体（sha256/deviceId）、
  token 持久化读写与优先级、99999→重登→重试（仅一次）、path_map 转换、resolve_guids
  分页/命中/未命中、sync_playlist 建单/追加/去重/部分成功、append_tracks 严格语义
  （404/路径+guid 混合/partial）、playlist_detail、错误映射
- 端点集成：四个端点的 400/404/502 与成功路径（fnos 编排层 mock）；
  `submit_download` playlist 参数校验（400 两例）与成功路径
- 主链路手动 E2E（NAS 实机）：配好 `fnos_music` → `submit_download` 两首单曲带
  `library="singles", playlist="测试-歌单同步"` → 飞牛 App 验证歌单与曲目 →
  原子端点验证（列歌单/看详情/单曲追加既有歌单/404 路径）→ 改错密码触发重登失败
  路径 → 飞牛 App 手动删测试歌单

## 风险

- **API 为逆向口径，无官方承诺**：trim.music 升级可能改端点/信封/认证（如启用 authx
  校验）→ 客户端集中在 `app/fnos.py` 单点适配；authx 算法已存档可直接启用；失败不
  影响下载主链路
- **password-login 未本机实测**（多项目佐证）：E2E 首要验证项；若字段名不符按实机
  响应调整
- **token 服务端寿命未知**：惰性重登已兜底；状态文件损坏时自动重登重建
- **歌单写操作幂等性依赖去重前置查询**：`playlist-detail/list` 分页拉全后按 guid 去重，
  并发同时加单极小概率重复（自用场景可接受）
- **老曲目解析翻页成本**：原子追加指向早期入库曲目时 track/list 需翻较多页（200/页），
  属一次性延迟；已知 guid 时走 `guids` 入参可免解析

## 里程碑

- **第一期（本设计）**：`app/fnos.py` + token 状态机 + `submit_download` playlist 参数 +
  REST 四端点 + MCP 五处 + 单测 + NAS 实机 E2E + README/API/MCP/ROADMAP 文档
- **后续（另行立项）**：`download_album` 流程 playlist 参数（专辑歌单）；按标题/艺人
  模糊搜歌加单；歌单封面；歌单删除/改名；歌单内曲目移除；token 定时体检
