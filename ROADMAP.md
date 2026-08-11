# 开发规划（Roadmap）

> 对应设计文档：[docs/design.md](docs/design.md)。里程碑按"最小闭环优先"排序，M4 为可选增强，按需迭代。

## 当前状态

| 里程碑 | 内容 | 状态 |
|---|---|---|
| M1 | 核心服务主链路（REST 搜索/下载/状态/落盘） | ✅ 已完成并远程验证（192.168.254.112） |
| M2 | MCP 适配器（Agent 可搜歌下载查进度） | ✅ 已完成（stdio/http 双传输，E2E 验证通过） |
| M3 | 部署与文档（Docker、README、API/MCP 文档） | ✅ 已完成 |
| M4 | 可选增强（见下） | ⬜ 待启动，欢迎认领 |

## M4 可选增强（按建议优先级排序）

1. **专辑级闭环：MCP 专辑维度能力**（源自 music-album-archiver 实践复盘）
   - 背景：实测"给一个专辑名 → 自动搜索资料/下载/整理入库"场景，现有 MCP 只有单曲级工具，专辑元数据确认、逐曲消歧、批量编排全靠 Agent 手工绕路，出错率最高的恰是专辑元数据环节
   - **第一期已完成**（2026-08-10）：`search_albums` / `get_album_info`（iTunes Search/Lookup API 主干，storefront 链 CN→HK→TW→US→JP 兜底，繁体曲目表自动转简体匹配）；`download_album`（服务端编排逐曲搜索 → 打分消歧（阈值 0.6，低于阈值记 unmatched）→ 按序号命名落盘 → `cover.jpg` + `manifest.json`（含逐曲 score/candidates/失败原因），替代解析私有 `download_results.pkl`）；REST 三端点 + MCP 三工具，E2E 验证通过
   - **第二期已完成**（2026-08-10）：`archive_album` 服务端归档（REST + MCP）：以 manifest.json 为契约，硬链接（CIFS/跨设备回退复制）入库 `{library_root}/{艺人}/{专辑}/`，断链后写 tag（TRACKNUMBER n/N、多 Disc CD1/CD2 + DISCNUMBER d/D）、嵌封面/歌词、`lyrics/`、`cover.jpg`、`album_info.txt`，幂等跳过；新增 `library_root`/`archive_comment` 配置与 compose 媒体库卷挂载示例
   - **后续待做**：网易云/QQ 网页接口补充中文专辑与简介（iTunes 覆盖不足时）；`get_artist_info` + 艺人 `artist.jpg` 头像；WAV→FLAC 转换
   - **已完成补充**（2026-08-10）：匹配音质偏好——同分段（与最高分差 ≤0.1）候选优先无损（`quality_tier`：无损 3 / 320k 2 / 其他 1），没有合格无损才选 MP3；manifest 的 match 增加 `ext`/`quality`/`quality_tier` 字段
   - **已完成补充**（2026-08-10）：罗马音专辑名修复——iTunes 对部分老中文专辑只存罗马音专辑名（如 "Kou Shi Xin Fei"），归档按"显式参数 > manifest display_* > 自动推断（国内源候选多数表决 + CJK 保护）> iTunes 原名"解析显示名；`download_album`/`archive_album` 新增 `album_title`/`artist` 覆盖参数；旧 manifest 重跑归档即可自动纠正
   - 风险：网易云/QQ 网页接口可能变动或限流，需多源交叉验证；消歧可能误中 Live/翻唱版本，manifest 必须带置信信息供复核

2. **单曲体积上限控制（无损防超大文件）** ✅ 已完成（2026-08-11）
   - 背景：无损音频体积差异大（普通 flac 约 25-60MB，Hi-Res 24bit/192kHz 可超 200MB/首），需要一道上限防止下载到超大文件
   - 已实现：`config.yaml` 新增 `max_size_mb` 全局默认；`POST /api/v1/downloads` 与 `POST /api/v1/albums/{id}/download` 请求体可传 `max_size_mb` 覆盖（>0 优先于配置，0/空不限）；MCP `submit_download`/`download_album` 同步加参
   - 生效点：专辑在 `match_track` 候选阶段过滤超限候选（manifest 的 match 记录 `oversized_filtered`，size_bytes 未知的放行），全部超限记 `unmatched` 并注明"体积超限"；单曲提交前校验，部分超限跳过并记入任务 `errors`，全部超限返回 400

2b. **命名库根与单曲归档** ✅ 已完成（2026-08-11）
   - 背景：专辑与单曲需要分库存放（如 library 放专辑、singles 放单曲）
   - 已实现：`config.yaml` 新增 `extra_library_roots` 白名单式命名附加库根（调用方传库名不传裸路径，支持配置任意多个库）；`GET /api/v1/libraries` + MCP `list_libraries`；`POST /api/v1/tracks/archive` + MCP `archive_tracks`（结构 `{库根}/{艺人}/{曲名.ext}`，写 tag/嵌封面歌词、sidecar .lrc、不写曲目序号，幂等跳过）；`submit_download` 传 `library` 下载完成后自动归档（单曲一步到位）；`archive_album` 增加 `library` 参数保持对称

2c. **手动放置文件的归档**（2026-08-11 记录的需求）
   - 背景：用户可能手动把歌曲文件拷进 `downloads/` 目录（非本服务下载的产物），目前没有接口可整理它们——`archive_album` 要 manifest.json、`archive_tracks` 要内存中的下载任务，两者都不覆盖这个场景
   - 现状变通：专辑形态可手写 manifest.json 后用 `manifest_path` 调 `archive_album`，但繁琐
   - 方向：新增 `POST /api/v1/tracks/archive_dir`（+ MCP 工具）：指定 `downloads/` 下子目录 + 目标库，扫描目录内音频文件，**从文件已有 tag（或文件名）解析艺人/曲名**，按单曲结构 `{库根}/{艺人}/{曲名.ext}` 归档；写 tag/嵌歌词逻辑复用 `archive_tracks`
   - 要点：手动文件通常自带平台 tag，解析即可入库；tag 缺失时的命名兜底策略（文件名解析/留原样）需在实现时定夺

2d. **已有专辑的曲目修复**（2026-08-11 记录的需求）
   - 背景：专辑下载/归档后可能出现个别曲目缺失或下错版本（体积超限被剔除、Remix/翻唱误匹配、候选池太浅、搜索关键词干扰等），目前只能手工补：容器内跑脚本调 `match_track` + `dl.submit` + `_write_tags`，再手动清临时目录，繁琐且易错（2026-08-11 巫启贤《红尘来去一场梦》、谭咏麟《难舍难分》就是这么补的）
   - 方向：新增 `POST /api/v1/albums/repair`（+ MCP 工具）：输入专辑目录（或 iTunes collection_id + 目标库根），扫描库内现有曲目，与曲目表比对找出缺失/可疑条目（unmatched、failed、oversized_relaxed、版本标记不符），按现有匹配打分逻辑重新搜索下载缺失曲目，补齐 tag/封面/歌词后入库
   - 要点：复用 `match_track` 的放宽与版本惩罚逻辑；"可疑曲目"的判定与替换策略（只补缺失 vs 允许替换错版本）需在实现时定夺；保持幂等，已齐全且无异常的专辑应为 no-op

3. **MoviePilot 薄客户端插件**
   - 目标：在 MoviePilot 内完成"搜索 → 勾选 → 下载 → 入库整理"闭环
   - 要点：继承 `_PluginBase`，只做表单与 REST 调用，不直接依赖 musicdl
   - 参考：设计文档 §4.1

3. **歌单批量下载**
   - 现状：`/api/v1/playlist` 已可解析歌单（42 首网易云歌单实测通过），但为同步阻塞接口，大歌单会慢
   - 方向：歌单解析异步化 + 一键批量提交下载任务

4. **有声读物源启用与验证**
   - 候选：喜马拉雅、懒人听书、荔枝FM、蜻蜓FM（musicdl 已内置客户端）
   - 工作：按源逐一验证搜索/下载链路，补充 cookies 配置说明

5. **夸克网盘无损源**
   - 候选：Mitu / Buguyy / Yinyuedao / Gequbao
   - 前置：在 `config.yaml` 配置夸克 cookies

6. **海外源代理支持**
   - 候选：Spotify、YouTube Music、Apple Music、TIDAL
   - 前置：代理配置 + Node.js（YouTube）+ N_m3u8DL-RE（HLS）

7. **WhisperLRC 语音转歌词**
   - 依赖 faster-whisper，为无歌词音轨生成 LRC

## 已知技术债 / 限制

- 歌单解析为同步阻塞，大歌单耗时长（客户端需容忍长超时，见 docs/API.md 已知限制）
- 下载任务持久化在内存 + SQLite，服务重启后运行中任务状态会丢失
- `cancel` 仅对 pending 态任务有效，运行中任务无法中断（musicdl 下载为阻塞调用）
- 任务队列未做并发上限之外的背压控制（内存队列，纯内网够用）

## 如何认领

直接在 Issue 中说明要认领的条目，或提交 PR 关联对应条目。提交前请先读 [CONTRIBUTING.md](CONTRIBUTING.md)。
