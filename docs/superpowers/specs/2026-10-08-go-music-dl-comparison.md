# go-music-dl 对比调研与本项目借鉴结论

- 文档状态：调研结论已确认，借鉴点已落入 Web UI 设计文稿（§4.10/§4.11）
- 调研日期：2026-10-08
- 调研对象：[guohuiyuan/go-music-dl](https://github.com/guohuiyuan/go-music-dl)（核心库 [guohuiyuan/music-lib](https://github.com/guohuiyuan/music-lib)）
- 调研方式：README/截图/前端源码（`internal/web/templates/`）+ GitHub API + 本仓库代码核对

## 一、定位差异（根本不同）

| | media-music-service（本项目） | go-music-dl |
|---|---|---|
| 定位 | **面向媒体库的服务**：搜下是入口，核心在"下载→消歧→归档→tag→NAS 生态联动" | **面向终端用户的下载器/播放器**：搜下即终点，带试听、本地播放、视频生成 |
| 形态 | FastAPI 服务 + MCP 适配器（API-first，Web UI 设计中） | Go 三合一：Web(Gin) + TUI(Bubble Tea) + 桌面(Rust/WebView) + Android/iOS |
| 语言/栈 | Python + FastAPI + musicdl(PyPI) | Go + Cobra/Gin/Bubble Tea，平台对接拆为核心库 music-lib |

**结论：互补而非重叠。** 它强在终端体验，本项目强在服务化入库链路。

## 二、能力逐项对比要点

**音源**：go-music-dl 12 平台 + local 本地源（网易/QQ/酷狗/酷我/咪咕/千千/汽水/5sing/Jamendo/JOOX/Bilibili/Apple Music），亮点是扫码登录（网易/QQ(QQ+微信)/酷狗/B站）、汽水加密音频解密、我的歌单读取。本项目依托 musicdl 上游，启用五源 + QQ musickey 自动保活（比手动扫码/粘贴更进一步，但只有 QQ 一家）。

**下载与元数据**：对方换源三段策略（歌名歌手相似度→时长→可播放探测）、FFmpeg 内嵌元数据（默认关）、逐字 LRC；本项目专辑逐曲打分消歧（阈值 0.6 + manifest 置信记录）、mutagen 直写 tag + 飞牛兼容适配、sidecar .lrc + 歌词回填端点、`max_size_mb` 体积上限。

**媒体库管理（最大分水岭）**：本项目独有硬链接归档 `{库根}/{艺人}/{专辑}/`、VA 合集归群星（COMPILATION=1）、命名多库、歌词回填/曲目替换/库内清理/单曲迁移、singles 复用消重。对方只有"本地音乐管理"（扫描索引/播放/换源），无归档流水线。

**生态集成**：本项目有 MCP（23 工具）、飞牛音乐歌单同步、MoviePilot 插件方案（暂缓）。对方唯一出口是 WebDAV 上传，无 MCP/插件机制，REST 无文档。

**工程**：对方 2026-01 创建，star 5.1k，v1.1.1，单人高频发版，CI 多平台构建；许可证 **AGPL-3.0**（网络服务形态也触发开源义务——**只可借鉴设计，不可引入其代码**）。

## 三、镜像体积分析（本仓库 Dockerfile 实测）

本项目镜像 **1.29GB**，`docker history` 逐层分解：

| 层 | 大小 | 内容 |
|---|---|---|
| pip install | 535MB | musicdl + fastapi + mcp/fastmcp 等 Python 依赖树 |
| apt + 外部工具 | 634MB | ffmpeg、Node 20、N_m3u8DL-RE（含 .NET 运行时） |
| python:3.11-slim 基础 | ~120MB | — |

对方 <100MB 的本质是 Go 静态二进制 + Alpine。本项目绑在 Python/musicdl 上做不到 100MB，但**已核实五个启用源（咪咕/网易/QQ/酷我/千千）对 Node/ffmpeg/N_m3u8DL-RE 零引用**（Node 仅 spotify/youtube/moov/ccmixter/streetvoice 使用；ffmpeg/HLS 仅 tidal 等使用——grep musicdl 2.13.11 源码确认）。

**瘦身方案（待实施）**：Dockerfile 改多 target——`slim` 为默认（砍 Node/ffmpeg/N_m3u8DL-RE，mcp/fastmcp 依赖拆到 MCP 容器单独安装），预计 **~350MB**；`full` target 保留完整工具链供海外源（ROADMAP 第 6 条）启用时使用。

## 四、UI 设计借鉴结论（已落入 Web UI 设计文稿 §4.10/§4.11）

对方 UI 源码级分析（Go 模板 + 7300 行单文件原生 JS + APlayer/FontAwesome CDN，无框架），采纳的视觉与交互模式：

1. 翠绿主色 `#10b981` + 渐变文字；顶部三色径向光晕背景（600px 渐隐）；
2. 卡片大圆角 + hover 上浮；搜索框/主按钮全圆角胶囊；
3. 源选择做成可折叠"源卡片网格"（勾选描边主色）；
4. 歌手/专辑名做成可点链接（点歌手→精确搜索、点专辑→专辑页）；
5. 次要控件收进"列表工具"popover；下载完成红点徽标语义；
6. **扫码登录状态机弹窗**（waiting→scanned→success/expired 四态配色、2.2s 轮询防重入、成功自动回填 + 900ms 延时关窗、二维码服务端图优先/canvas 兜底、移动端 `min(280px,86vw)`）——已纳入 Web UI 设置页 Cookies 分组设计，对应后端新增 `POST/GET /api/v1/auth/qr/{source}`。

**明确不借鉴**：播放器/看板娘等娱乐化元素（我们是管理工具）；其单文件原生 JS 无组件化路线（我们坚持 Vue3 组件化）；暗色模式我方保留（对方没有）。

## 五、对本项目的可借鉴清单（按价值排序）

1. **扫码登录**（网易/QQ，备选酷狗）：已纳入 Web UI 方案，QQ 扫码成功重置 qqauth 保活种子；
2. **镜像瘦身**：多 target Dockerfile（见第三节），预计 1.29GB → ~350MB；
3. **换源三段策略**（相似度→时长→可播放探测）：可增强 `match_track` 打分（后续评估）;
4. **下载目录"预设下拉 + 自定义"输入**：设置体验细节（设置页采用）；
5. **下载去重 + 文件存在性校验**（后续评估）；
6. music-lib 细粒度能力接口组合（SongSearcher/LyricProvider 可选实现）：多源抽象范式参考。

**护城河（对方没有、我方独有）**：媒体库归档与 tag 体系、VA 合集处理、MCP、飞牛歌单联动、QQ 凭证自动保活、异步任务模型。
