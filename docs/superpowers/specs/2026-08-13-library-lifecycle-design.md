# 媒体库生命周期管理四场景设计

- 创建日期：2026-08-13
- 状态：设计待评审
- 背景：用户提出四个媒体库运维场景（见下），均在现有 M4 专辑/单曲分库体系（`library_root` + `extra_library_roots` 命名库）上扩展

## 需求（用户原文）

1. 下载专辑时，优先从 singles 库中查找同专辑对应曲目的音频文件，存在则不重复下载，直接使用该文件，并在归档专辑时将该音频文件迁移到专辑库中；
2. 允许重新对某专辑下指定的曲目进行搜索，并使用规格更高、更好的版本进行替代；
3. 允许对媒体库中的专辑或者曲目文件进行清理，如果存放这些文件的目录为空，则一并清理掉，不留空目录；
4. 扫描专辑库中的单曲（只有一首歌的专辑目录），允许将它们转移到 singles 库中。

## 现状关键点

- 库体系：`app/libraries.py` 白名单命名库（`default` = `library_root`，附加库如 `singles` 配在 `extra_library_roots`）。
- 专辑下载：`app/album.py::_run_album` 逐曲 `match_track` 搜索消歧 → 按序号命名落盘 → 产 `manifest.json`。
- 专辑归档：`app/archive.py::archive_album` 以 manifest 为契约，硬链接入库（CIFS 回退复制）→ 断链 → `_write_tags` 重写 tag → 归档后 `cleanup_task_dir` 清下载产物。
- 单曲归档：`archive_tracks` → `{库根}/{艺人}/{曲名.ext}`，不写序号类 tag，ALBUM 用候选专辑名。
- 打分/音质：`album.score_candidate` / `pick_best` / `quality_tier`（无损 3 / 320k 2 / 其他 1）、`_sim`/`_normalize`/`t2s`/`_safe_name` 均可复用。
- 项目无单元测试框架，验证方式为脚本级 + E2E（curl/MCP）。

## 总体方案

新增 `app/libops.py` 模块承载库运维能力（场景 2/3/4 + 场景 1 的 singles 查找）；场景 1 对 `album.py`/`archive.py` 做小改动。REST 新增 3 个端点，MCP 新增 3 个工具 + `download_album` 加参。所有库内删除/移动操作只作用于白名单库根之内（解析后 `is_relative_to` 校验），与 `cleanup.py` 同一安全红线。

### 场景 1：专辑下载复用 singles 库

- `libops.find_in_singles(expected: AlbumTrack, album_title: str, singles_root: str) -> Path | None`：
  扫描 `{singles_root}/{艺人}/*.音频`，先用 `_normalize` 对曲名做粗筛（`_sim >= 0.85`），命中候选再读 mutagen tag 校验：tag TITLE 相似度、`ALBUM` 与专辑名 `_sim >= 0.6`（tag 缺 ALBUM 时中性放行）、时长差 ≤ 15s（两侧都有时长时）。艺人目录先按 `_artist_sim` 过滤以缩小扫描面。
- `album._run_album` 匹配循环前：若 `extra_library_roots` 配置了 `singles` 库，逐曲先查 singles；命中则硬链接（失败回退复制）到 `save_dir` 的序号文件名（含同名 `.lrc`），`entry.status = "ok"`，`entry.match = {"source": "singles", "reused_from": 源文件绝对路径, "title"/"artists"/"album"/"ext": 读自 tag}`，跳过搜索下载。
- `archive.archive_album`：曲目归档 action ∈ {linked, copied, skipped, tag_unsupported} 且 `match.reused_from` 存在时，删除 singles 源文件及同名 `.lrc`，随后清理为空的艺人目录（仅当目录已空）。manifest 中 `reused_from` 供复核。
- 不做显式开关：配置了 `singles` 库即生效（即用户语义）；未配置则行为与现状完全一致。
- 硬链接安全：save_dir 副本与 singles 源共享 inode；归档时 `_break_link_if_needed` 已保证入库副本独立，删 singles 源不影响库内文件。

### 场景 2：重搜替换专辑指定曲目（replace_album_track）

- REST `POST /api/v1/library/replace_track` + MCP `replace_album_track`。
- 请求：`library`（默认 default）、`artist`、`album`、`track`（曲目序号 int，或曲名 str）、`sources?`、`force?`（默认 false）。
- 流程：
  1. 定位专辑目录 `{root}/{artist}/{album}/`（名称按 `_safe_name` 规范化匹配实际目录），按 `NN - ` 前缀（含 `CDx/` 子目录）或曲名 `_sim` 定位现有音频文件；
  2. 读现有文件 tag（TITLE/ARTIST/ALBUM/DATE/TRACKNUMBER/DISCNUMBER）与时长，按 ext + bitrate 计算现有音质 tier（无损 ext → 3；bitrate ≥ 320k → 2；否则 1）；
  3. 构造 `AlbumTrack` 调 `album.match_track` 重新聚合搜索消歧（沿用阈值/体积规则）；
  4. 新候选 `quality_tier > 现有 tier` 或 `force=true` 时才替换：`dl.download_songs` 下载到 `download_root/replace_<ts>/` 临时目录 → 核验落盘 → 移入专辑目录（`NN - 曲名.新ext`，ext 变化时删旧文件）→ `_write_tags` 重写（序号/专辑/艺人/日期沿用旧 tag，封面用专辑目录 `cover.*`，歌词用新下载 `.lrc` 并同步更新 `lyrics/`）→ 清临时目录；
  5. 未更优则 `action = "kept"`，不下载（先比规格再下载，省流量）。
- 响应：`{status, action: replaced/kept/unmatched/failed, old: {file, ext, tier}, new: {source, ext, quality, tier, score}?, error?}`。
- 与 ROADMAP 2d（专辑曲目修复）的关系：本场景是其"替换指定曲目"子集，2d 的批量扫描修复不在本期范围。

### 场景 3：库内容清理（cleanup_library）

- REST `POST /api/v1/library/cleanup` + MCP `cleanup_library`。
- 请求：`library`、`artist`、`album?`、`tracks?`（序号 int 或曲名 str 列表）、`dry_run`（默认 false）。
- 行为：
  - 指定 `tracks` → 删除匹配音频文件 + `lyrics/` 内同名 `.lrc`；
  - 只到 `album` → 删除整个专辑目录；
  - 只到 `artist` → 删除整个艺人目录；
  - 删除曲目后自底向上清理：空的 `CDx/` → 专辑目录（已无音频文件时连同 cover/album_info/lyrics 一起删）→ 空的艺人目录。
- 安全：目标解析后必须严格位于库根之内；目录/文件不存在返回 404 风格错误；`dry_run=true` 只列将要删除/清理的项。
- 响应：`{status, deleted_files: [...], removed_dirs: [...], errors: [...]}`。

### 场景 4：专辑库单曲迁移到 singles 库（migrate_singles）

- REST `POST /api/v1/library/migrate_singles` + MCP `migrate_singles`。
- 请求：`library`（源库，默认 default）、`target_library`（默认 singles）、`artist?`（限定单个艺人）、`dry_run`（默认 false）。
- 行为：扫描源库 `{艺人}/{专辑}/`，音频文件总数 == 1 的专辑目录视为单曲专辑，迁移到 `{target_root}/{艺人}/{曲名.ext}`：
  - 曲名取 tag TITLE，兜底用文件名去 `NN - ` 前缀；
  - 移动方式 `os.rename`，跨设备回退 copy+unlink；
  - 断链（目标 nlink > 1 时 copy+replace，防改到下载目录源文件）后重写 tag：移除 TRACKNUMBER/TRACKTOTAL/DISCNUMBER/DISCTOTAL，保留 ALBUM/ARTIST/DATE/封面/歌词，COMMENT 仍为 `archive_comment`；
  - `lyrics/` 中对应 `.lrc` 移到目标文件旁；
  - 迁移后删除该专辑目录（含残留元数据）与为空的艺人目录；
  - 目标已存在同名文件 → 跳过并记入报告。
- 响应：`{status, migrated: [{from, to}], skipped: [...], errors: [...]}`。

## 改动清单

| 文件 | 改动 |
|---|---|
| `app/libops.py`（新） | `find_in_singles`、`file_quality_tier`、`replace_album_track`、`cleanup_library`、`migrate_singles`、库内路径安全校验、空目录清理 |
| `app/album.py` | `_run_album` 匹配循环前接入 singles 复用 |
| `app/archive.py` | `archive_album` 成功后迁移（删除）singles 源文件 + 清空目录 |
| `app/schemas.py` | `ReplaceTrackRequest/Result`、`CleanupRequest/Result`、`MigrateSinglesRequest/Result` |
| `app/main.py` | 3 个新端点 |
| `mcp_adapter.py` | 3 个新工具 |
| `docs/API.md`、`docs/MCP.md`、`ROADMAP.md` | 文档同步 |

## 错误处理与边界

- 库名一律走 `resolve_library_root` 白名单校验；所有删除/移动前做 `is_relative_to` 库根校验。
- 场景 1 复用判定保守：曲名/专辑/时长任一硬冲突则不命中，宁可重新下载也不错拿。
- 场景 2 单曲失败不中断服务，错误进响应；临时目录固定前缀 `replace_`，位于下载根内，可被现有定期清理覆盖。
- 场景 3/4 的目录清理只删"空目录"或"整个指定专辑/艺人目录"，不做模式匹配通配删除。

## 验证方式（项目无测试框架，沿用 E2E 惯例）

1. 场景 1：singles 库放一首某专辑曲目 → `download_album` 该专辑 → manifest 中该曲 `match.source == "singles"` → `archive_album` 后专辑库有该曲、singles 库对应文件与空艺人目录已消失。
2. 场景 2：构造含 mp3 曲目的专辑目录 → `replace_track` 搜到 flac → 文件被替换且 tag/序号/封面正确；已是 flac 时返回 `kept`。
3. 场景 3：删除专辑中一首 → 文件与同名 lrc 消失、目录保留；删除专辑全部曲目/整专辑 → 专辑目录与空艺人目录消失；`dry_run` 不实际删除。
4. 场景 4：构造单曲专辑目录 → 迁移后 singles 库出现 `{艺人}/{曲名}.ext` 且无序号 tag，原专辑目录与空艺人目录消失。
5. 回归：`/api/v1/health`、`search`、单曲下载归档链路不受影响；未配置 singles 库时专辑下载行为不变。
