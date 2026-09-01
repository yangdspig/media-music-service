# 合集（Various Artists）专辑归档设计

> 2026-09-01 需求：像《仙剑奇侠传》这类多艺人合集/原声带，不适合按 `{库根}/{艺人}/{专辑}/` 归到单一艺人目录下。
> 已与用户确认：方案 A（自动检测 + 固定「群星」目录 + 显式覆盖参数），**去掉**"逐曲艺人分散"自动判定，只认 VA 名单。

## 背景与现状问题

当前归档（`app/archive.py`）对合集专辑机械可用但有两个缺陷：

1. **目录名不一致**：iTunes 专辑艺人是 "Various Artists"，网易云/QQ 是 "群星"，同类合集散落在不同顶级目录。
2. **tag 丢失逐曲艺人**：`_write_tags` 把 ARTIST 与 ALBUMARTIST 都写成专辑级艺人（`archive.py:249`），合集每首歌的真实演唱者（如《仙剑》原声带的胡歌/JS/阿桑）被抹掉；且没有 COMPILATION 标记，Navidrome 无法按合集分组。

## 目标

- 合集专辑统一归档到 `{库根}/群星/{专辑}/`；
- tag：ARTIST=逐曲艺人，ALBUMARTIST=群星，COMPILATION=1（Navidrome 合集分组）；
- `album_info.txt` 曲目表带逐曲艺人；
- 显式覆盖参数应对名单未收录的情况。

非目标（YAGNI）：

- 不做"逐曲艺人分散度"自动判定（只认 VA 名单，误判率最低）；
- 不加配置项（目录名固定"群星"，VA 名单写死在代码里）；
- singles 单曲归档/迁移逻辑不变；
- 合集专辑不下载艺人头像（"群星"不是具体艺人）。

## 检测规则

VA 名单（匹配前归一化：`t2s` → 小写 → 去首尾空白）：

```python
_VA_NAMES = {"various artists", "va", "群星", "华语群星", "合辑"}
```

判定时机与输入：**归档时**（`archive_album`），对两个值分别归一化后与名单比对，任一命中即为合集：

1. `_resolve_names` 解析出的显示艺人（显式参数 > display_* > 推断 > 原名）；
2. manifest `album.artists[0]` 原始值。

显式覆盖：`ArchiveRequest` 新增 `compilation: Optional[bool] = None`——`None` 按名单自动判定，`True`/`False` 强制走/不走合集逻辑（最高优先级）。MCP `archive_album` 工具同步加参。

优先级细则：`compilation` 参数 > 显式 `artist` 参数 > 名单自动判定。即：显式传了 `artist` 而未传 `compilation` 时，视为用户明确要归到该艺人目录下，跳过名单自动判定（按普通专辑处理）。

老 manifest 无需任何改动：判定只看艺人字段，重跑归档（`overwrite`）即可让旧合集自动迁入群星目录。

## 归档行为（判定为合集时）

| 项 | 普通专辑 | 合集专辑 |
|---|---|---|
| 目录 | `{库根}/{显示艺人}/{专辑}/` | `{库根}/群星/{专辑}/` |
| ARTIST tag | 显示艺人 | **逐曲艺人**（manifest entry 的 `artists` 用 " / " 连接；缺失时回退专辑显示艺人） |
| ALBUMARTIST tag | 显示艺人 | 群星 |
| COMPILATION tag | 不写 | `1`（FLAC 写 Vorbis `COMPILATION`；MP3 写自定义 `TCMP` 文本帧——mutagen 无内置 TCMP，用 `TextFrame` 子类实现） |
| 艺人头像 | 有则写 `artist.*` | 跳过 |
| `album_info.txt` 艺人行 | `艺人：{显示艺人}` | `艺人：群星（合集）` |
| `album_info.txt` 曲目表行 | `01. 曲名` | `01. 曲名 - 逐曲艺人`（该曲无艺人信息时保持原样） |

其余环节（硬链接/断链写 tag、嵌封面歌词、lyrics/ 目录、幂等跳过、归档后清理、singles 复用迁移）完全不变。

## 联动修改

- `app/archive.py`：
  - `_write_tags` 签名加两个可选参数：`track_artist: str | None = None`（None 时 ARTIST=artist，保持现有行为）、`compilation: bool = False`；FLAC 分支加 `COMPILATION`，MP3 分支加 `TCMP`（含白名单 keep 集合加 "COMPILATION"）。
  - `archive_album` 主流程：判定合集 → 目录根用「群星」→ 逐曲传 `track_artist` → 跳过 `_save_artist_image` → `_write_album_info` 传入合集标记。
  - `_write_album_info` 加 `compilation: bool = False` 参数，控制艺人行与曲目表行格式。
- `app/schemas.py`：`ArchiveRequest` 加 `compilation: Optional[bool]` 字段。
- `app/libops.py` `replace_album_track`：替换曲目时沿用**旧文件的** ARTIST 与 COMPILATION tag（其 docstring 本就写"艺人沿用旧 tag"，当前实现却用 artist 参数重写——顺带对齐；覆盖合集曲目在 `群星/` 目录下被替换的场景）。`cleanup_library`/`migrate_singles` 无需改动（"群星"就是普通顶级目录名，按目录操作天然兼容）。
- `mcp_adapter.py`：`archive_album` 加 `compilation` 参数与 docstring 说明。
- 文档：`docs/API.md`（archive 请求体 + 合集行为说明）、`docs/MCP.md`（archive_album 工具）、`ROADMAP.md` 新增 **2f** 条目记录本需求（实现后标记完成）。

## 错误处理与边界

- 合集判定不产生新的失败路径；`_write_tags` 对非 flac/mp3 依旧 `tag_unsupported`。
- 逐曲艺人缺失（entry 无 artists）时 ARTIST 回退专辑显示艺人，不报错。
- 幂等不变：目标已存在且未 `overwrite` 仍跳过；`overwrite=True` 重归档可纠正旧归档位置——但旧位置的 `{艺人}/{专辑}/` 目录需用户用 `cleanup_library` 清理（归档不负责删除旧位置，避免误删）。

## 测试（tests/，pytest，沿用现有模式）

- VA 名单检测：Various Artists / VA / 群星 / 华语群星 / 大小写与繁体（"羣星"经 t2s 命中"群星"）。
- 合集归档端到端（tmp_path 假库 + 假 manifest + 真实 flac 文件可用 mutagen 最小文件或 fake 字节 + `tag_unsupported` 路径）：目录落在 `群星/`、逐曲 ARTIST、COMPILATION、无 artist.* 头像、album_info.txt 格式。
- 显式覆盖：`compilation=True` 强制走群星目录；`compilation=False` 对"群星"艺人的专辑强制普通归档。
- `replace_album_track` 沿用旧 ARTIST/COMPILATION。
- 回归：既有 `test_libops.py`、`test_cn_meta.py` 不变。

## 文件清单

| 文件 | 变更 |
|---|---|
| `app/archive.py` | VA 检测 + 合集归档分支 + `_write_tags`/`_write_album_info` 参数扩展 |
| `app/schemas.py` | `ArchiveRequest.compilation` |
| `app/libops.py` | `replace_album_track` 沿用旧 ARTIST/COMPILATION |
| `mcp_adapter.py` | `archive_album` 加参 |
| `docs/API.md`、`docs/MCP.md`、`ROADMAP.md` | 行为说明与状态 |
| `tests/test_archive_va.py` | 新增 |
