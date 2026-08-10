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
   - `search_albums` / `get_album_info`：专辑名+歌手 → 聚合多平台的规范化结果（官方曲目表含 disc/序号、发行日期、厂牌、高清封面 URL、专辑简介）。musicdl 无专辑级 API，需直连平台接口聚合；建议以 iTunes Search API（官方免 key）为稳定主干，网易云/QQ 网页接口补充中文专辑与简介
   - `download_album`：服务端编排"逐曲搜索 → 专辑/歌手匹配消歧 → 缺歌换源兜底 → 按序号命名落盘 → 结构化清单"
   - `get_artist_info`：艺人头像候选 URL + 简介（网易云/iTunes 艺人接口）
   - 下载产物同步输出 `manifest.json`（曲目映射、封面/歌词路径、失败原因、匹配置信信息），替代解析私有 `download_results.pkl`
   - 边界：硬链接/tag 规范化/嵌封面/校验等归档动作留在客户端 skill 脚本；若容器直挂媒体库可再评估服务端 `archive_album`（需 compose 增加媒体库卷挂载）
   - 风险：网易云/QQ 网页接口可能变动或限流，需多源交叉验证；消歧可能误中 Live/翻唱版本，manifest 必须带置信信息供复核

2. **MoviePilot 薄客户端插件**
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
