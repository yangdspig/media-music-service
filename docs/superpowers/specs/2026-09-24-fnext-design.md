# fnext：飞牛音乐无侵入增强扩展设计

日期：2026-09-24
状态：已获用户认可（Docker 优先修订版）；今日只落档，不启动开发

## 背景与问题

本服务（media-music-service）已具备成熟的多平台搜索下载、专辑归档入库（tag/嵌封面/歌词 sidecar，已针对飞牛音乐调校）、QQ cookie 自动保活能力，跑在飞牛 NAS 的 Docker 里。参考项目 [javycoder/fnos_music_ext](https://github.com/javycoder/fnos_music_ext)（MIT）验证了另一条路线：通过 Unix Socket inode 接管飞牛官方音乐 App（trim.music）的后端通信，在不修改官方程序与数据库的前提下注入在线搜播、边听边存、推荐等能力。

目标是在**本项目基础上**打造一个同类扩展（包名 `fnext`），让自己的核心资产（搜索引擎、归档管线、保活）直接注入飞牛原生 App：

- 原生飞牛音乐（Web/App/车载）获得在线音乐能力：官方搜索框直接搜到在线曲目并即点即播
- 边听边存：播放在线歌曲时自动缓存；收藏（红心）即走归档管线入库 singles 库
- 推荐体系：向官方推荐页注入每日推荐/热门榜单
- 管理 WebUI：页面调整配置、查看状态、一键启用/停用
- 随时停止扩展，秒级切回官方原生能力

## 目标

1. 接管 `/var/run/trim_music.socket`，默认全量透传，按端点注入在线能力；任何环节异常降级为透传
2. 在线搜索聚合（复用 `app/search` + musicdl），在线播放（302 直链优先，代理流兜底）
3. 边听边存 tee 缓存 + 收藏触发归档入库（复用 `app/archive` 管线）
4. 推荐注入（每日推荐 + 热门榜单，全免登录榜单源）
5. 管理 WebUI（状态/配置/控制/收藏缓存清单）
6. Docker 优先部署（与核心服务同镜像，compose profile 启用）

## 非目标

- 多用户收藏隔离（纯内网单用户，在线收藏全局共享一份）
- LLM 兜底推荐、真·个性化每日推荐（需网易登录，留扩展点）
- lxmusic 式 Node 桥用户源脚本
- fnOS 应用中心 fpk 打包（E5 可选）
- 修改官方程序、数据库、Nginx 配置（只读探测官方接口）
- 影响现有 REST/MCP 核心服务的任何行为（fnext 是独立进程的独立入口）

## 总体架构

```
飞牛音乐客户端(Web/App/车载) → 飞牛 Nginx → /var/run/trim_music.socket
                                             │ (fnext 启用时监听于此)
        ┌────────────────────────────────────┴───────────────────────┐
        │  fnext 容器（与核心服务同镜像，compose profile `fnext`）      │
        │  asyncio 单进程 FastAPI                                     │
        │  ├─ 默认透传 → trim_music_upstream.socket（官方后端）         │
        │  ├─ 搜索拦截 → 本地(透传结果) + 在线引擎(app/search+musicdl)  │
        │  ├─ 播放拦截 → 直链解析/302/206 代理流 + tee 落盘             │
        │  ├─ 收藏拦截 → 透传 + 触发归档管线入库（singles 结构）        │
        │  ├─ 歌词/封面补齐                                            │
        │  ├─ 推荐注入 → 榜单引擎                                      │
        │  └─ WebUI（同进程，端口 8778）+ 控制 API                     │
        └─────────────────────────────────────────────────────────────┘
```

第一原则：**扩展挂了 = 官方直连**。拦截异常一律降级透传；不修改官方程序与数据库。

代码复用方式：fnext 与核心服务同镜像，`import app.*` 进程内复用（搜索引擎、归档管线、QQ 状态读取），不经 REST。核心服务的 MCP/REST 入口完全不受影响。

### 模块拆分（新顶层包 `fnext/`）

| 模块 | 职责 |
|---|---|
| `fnext/__main__.py` | 入口：初始化配置/状态 → 按期望状态接管 → 启动代理+WebUI |
| `fnext/takeover.py` | 接管/还原状态机（socket 改名/建监听/权限复制/状态持久化/SIGTERM 陷阱） |
| `fnext/proxy.py` | UDS 监听与透传、按路径分发到各拦截器，拦截异常回退透传 |
| `fnext/online.py` | 在线搜索引擎（缓存/熔断/自适应超时/早停/跨源去重） |
| `fnext/stream.py` | 在线播放（302/代理流 206/直链刷新/跨源 fallback） |
| `fnext/tee.py` | 边听边存（tee 落盘/完整性校验/滚动缓存 GC/收藏归档触发） |
| `fnext/recommend.py` | 榜单引擎（每日/热门，按天缓存，与本地库去重） |
| `fnext/webui.py` + `fnext/static/` | 管理页面（原生 JS 无构建链）+ 控制/配置 API |
| `fnext/config.py` | `fnext.yaml` 加载/写回/热重载；只读引用 `config.yaml` |
| `fnext/state.py` | 状态文件/收藏映射/缓存清单持久化（`fnext_data/`） |

## 接管与还原（安全设计，最高优先级）

### 状态机

`fnext_data/fnext_state.json` 持久化：`desired`（enabled/disabled，缺省 enabled）、原始 socket 元数据（uid/gid/mode）、接管时间戳。

- **启用**（`fnextctl enable` / WebUI / 容器启动且 desired=enabled）：等待官方 socket 出现（超时重试）→ 记录其 uid/gid/mode → 重命名为 `trim_music_upstream.socket` → 代理在原路径监听并复制原权限属主 → 写状态。
- **停用**（`fnextctl disable` / WebUI）：进程内还原 socket 改回原名 → 探测官方后端应答 → desired=disabled；**进程不退出**，仅保留 WebUI 与健康端点待命（再启用秒级恢复）。
- **容器停止**：入口脚本捕获 SIGTERM → 若处于接管态先还原再退出（覆盖 `compose down`）。
- **容器重启**：入口读 desired；enabled 则重新走启用流程。
- **守护约束**：非本扩展创建/状态不符的 socket 拒绝接管，保留现场并报告；还原只对自己创建的 socket 动手；容器名/路径全局固定，多副本部署拒绝接管。

### 异常矩阵

| 场景 | 行为 |
|---|---|
| `compose down` / 正常停止 | SIGTERM 陷阱先还原再退出 |
| 宿主重启 | `/run` 为 tmpfs，重启即清空 → 官方 App 重建 socket 天然回官方；fnext 容器（`restart: unless-stopped`）起来后按 desired 重新接管 |
| 容器崩溃 | restart 策略拉起，入口按 desired 自愈 |
| 容器持续崩溃/被删 | socket 残留无监听，官方 App 不可用 → 可选宿主机看门狗兜底（见部署章） |
| 官方 App 升级 | 重建官方 socket，重跑 `fnextctl enable` 恢复（文档写明） |
| 拦截逻辑异常 | 降级透传，不影响本地曲库使用 |

### API 契约

E1 第一步：在 NAS 实机抓取官方 App 真实 API 请求/响应录为 fixtures（搜索/播放/收藏/推荐/歌词等端点），并参考 fnos_music_ext `proxy/app.py` 的端点映射（MIT 署名）交叉核对。所有拦截器以 fixtures 为契约测试基准。

## 在线搜索与播放（`fnext/online.py` + `fnext/stream.py`）

### 搜索

- 在线曲目 GUID：`online:{source}:{identifier}`。
- 多源调度（借鉴 fnos_music_ext hardening，纯标准库实现）：每源舱壁（单 worker 线程池，忙即拒）、自适应超时（基准 12s/下限 8s，失败 ×0.6、成功 ×1.25）、熔断（连败 4 次熔断 120s，慢成功 >8s 也计失败）、早停（累计 ≥12 条即返回，已有结果后慢源最多再宽限 2s；被跳过/已出结果的源不计失败）。
- 两级缓存：关键词级分层 TTL（**空结果 10s；有结果但有源出错 30s；全成功 ≤300s；空结果且带错误不缓存**，命中校验曲目缓存存活）+ 曲目级缓存（存 keyword 供直链过期重搜刷新）。
- 与本地结果合并：本地优先；在线跨源去重按 `(title, artist, duration±2s)` 并入代表条的 `_alternatives`（供播放 fallback）。

### 播放

- 首选 302 直链（客户端自拉）；不支持时走代理流：Range/206 透传、`Accept-Encoding: identity`（上游不遵守则 502）、首字节先验证再发响应头、curl_cffi 模拟 Chrome TLS 指纹（无则回退 httpx）。
- 直链生命周期：TTL 内直接用 → 过期探活续命 → 失败按缓存 keyword 重搜同 ID 刷新；流请求 4xx/5xx 时再触发一次刷新重试 + 跨 `_alternatives` fallback（≤3 候选）。
- 歌词：musicdl lyric 字段 + 免登录歌词接口兜底（酷狗 krcs/网易 lv=1/咪咕 lrcUrl/QQ fcg，均 2026-09 实测存活；酷我已失效不用）；封面 CDN 直构 + 占位图兜底，永不 404。
- 音源默认：QQ（VIP cookies + 保活，无损主力）、酷我、咪咕、网易（免登录档）；可配置开关。

### QQ 凭证共享

保活循环只跑在核心服务（现状不变）。fnext 只读挂载核心服务的 `./data`，读取 `qq_auth_state.json` 构造 QQ 客户端凭证（原子写保证读安全）；核心服务停 → QQ 源随凭证过期自然降级，熔断兜底，不影响其他源。

## 边听边存（`fnext/tee.py`）

- 拉通式 tee：代理流边下边写 `.part`，**干净 EOF + Content-Length 校验**才 `os.replace` 转正；中途断流必删 `.part`。强制 identity 编码、content-type 白名单。
- 只缓存完整拉取（无 Range 或 `bytes=0-`）；定长窗口客户端另起后台整轨下载兜底（同 guid 去重，失败冷却 1800s）。
- 两级去向：
  1. **滚动缓存**：keep-N（默认 20 首，可配），按 mtime 淘汰，再播秒开；缓存命中直接本地 Range 服务。
  2. **收藏即入库**：拦截红心请求（透传给官方的同时），把缓存文件走 `app/archive` 归档管线（写 tag/嵌封面/同名 .lrc sidecar）落进 singles 库 `{库根}/{艺人}/{曲名.ext}`；入库成功后删除缓存副本。取消红心不删库（官方收藏列表语义不变）。
- 入库后飞牛扫描：优先依赖 watcher；实测不入册时用已验证的"移出再移回"触发重刮（ROADMAP 2h 结论）。

## 推荐体系（`fnext/recommend.py`）

- 注入两条歌单：**每日推荐**（20 首，按天缓存，排除已收藏）与**热门榜单**（榜单原味不排除）。
- 全免登录源起步：网易 `/api/toplist` + `/api/personalized/newsong`；酷狗 `m.kugou.com/rank/info/?rankid=8888&page=1&json=true`（必须带 page）；酷我 `kbangserver.kuwo.cn/ksong.s?id=93`；QQ toplist（有 cookies，`fcg_myqq_toplist.fcg` + `fcg_v8_toplist_cp.fcg`，与榜单目录设计一致）。
- 与本地库按 `(title, artist)` 去重；候选曲目经在线引擎搜索落地为可播 GUID。
- VIP 标记不硬过滤（榜单曲目匿名常可解析），可播性以解析/探活为准。
- 扩展点（本期不做）：网易扫码登录后的真·每日推荐、LLM 兜底。

## 管理 WebUI（`fnext/webui.py`）

同进程 FastAPI + 静态页（原生 JS，无构建链），端口 8778（可配），纯内网默认免鉴权（可选复用 `api_key`）。四个页面：

1. **状态总览**：接管状态（enabled/disabled/socket 状态）、各源健康（熔断/自适应超时/错误计数）、tee 缓存占用、推荐缓存日期、版本
2. **配置编辑**：音源开关、cookies 粘贴（写 `fnext.yaml` 覆盖层）、tee 容量/keep-N、收藏入库开关、推荐开关；写回热生效
3. **控制**：一键启用/停用扩展（大按钮 + 二次确认）、清搜索缓存、清 tee 缓存、强制刷新 QQ 登录态
4. **收藏与缓存清单**：在线收藏列表、tee 缓存列表（可单曲删除/手动触发入库）

## 配置与状态

- `config.yaml`：**只读引用**（cookies 种子、库结构定义），保持手编注释不被改写。
- `fnext.yaml`：WebUI 可写覆盖层——`webui_port`（默认 8778）、`online_sources`（开关列表）、`search`（超时/缓存 TTL 参数）、`tee`（enabled/keep_n/cache_dir）、`archive_on_favorite`（默认 true）、`recommend`（enabled/源开关）、覆盖 cookies。热重载；改白名单键自动清搜索缓存。
- `fnext_data/`（容器卷）：`fnext_state.json`（期望状态/socket 元数据）、在线收藏映射、tee 缓存清单、推荐按天缓存。tee 缓存文件本身放 `fnext_data/tee_cache/`。
- 媒体库挂载与核心服务一致（`/library`、`/singles`），归档管线零改动复用。

## 部署形态（Docker 优先）

- **与核心服务同一镜像**：同 Dockerfile，`command: python -m fnext`，compose profile `fnext` 按需启用（同 mcp-adapter 模式）。
- compose 要点：
  - 卷：`/run:/run:rw`（接管所需，**安全权衡见风险章**）、`./fnext.yaml:/app/fnext.yaml`、`./fnext_data:/app/fnext_data`、`./config.yaml:/app/config.yaml:ro`、`./data:/app/data:ro`（读 QQ 保活产物）、`./library:/library:rw`、`./singles:/singles:rw`
  - 端口：`8778:8778`（WebUI）
  - `restart: unless-stopped`；容器以 root 运行（需复制 socket 属主权限）
- 入口脚本（entrypoint）：状态机驱动——读 desired → enabled 则等官方 socket→接管→启动代理+WebUI；SIGTERM → 还原 → 退出。
- `fnextctl`（容器内 CLI）：`enable/disable/status/logs`，用法 `docker compose exec fnext fnextctl disable`。
- **可选宿主机看门狗**：一次性安装的 systemd timer + ~30 行脚本，探测 socket 无健康响应且非用户主动停用 → 还原官方直连（fail-open 兜底容器持续崩溃场景）；不装则降级为靠 Docker 自动重启自愈，文档说清。
- 宿主 systemd 模式：仅作文档备选，本期不交付。

## 里程碑

| 阶段 | 内容 | 验收 |
|---|---|---|
| E1 | API 契约 fixtures + 接管状态机 + 全量透传 + 搜索注入 + 在线播放（302 直链）+ fnextctl + compose 部署 | App 搜"晴天"出在线结果并播放；disable 后完全官方原生； fixtures 契约测试通过 |
| E2 | 代理流 206 + 边听边存（tee/缓存命中/滚动 GC）+ 收藏触发归档入库 + 歌词封面补齐 | 再播秒开；红心后 singles 库出现带 tag/封面/.lrc 的文件且飞牛可见 |
| E3 | 推荐注入（每日/热门） | App 推荐页出现两条歌单且曲目可播 |
| E4 | 管理 WebUI 四页 | 页面改配置热生效；页面一键停用/启用；状态页数据准确 |
| E5（可选） | 网易扫码登录、fpk 打包进应用中心、宿主 systemd 模式交付 | — |

每个里程碑独立可验收、独立可停用回退。

## 测试策略

- **契约测试**：录制真实官方 API 响应为 fixtures（`tests/fnext/fixtures/`），拦截器的请求构造/响应解析以此为准；官方 App 升级后重录比对。
- **单元测试**：搜索合并去重/早停/熔断/自适应超时；tee 完整性（断流删 .part、EOF 校验、滚动 GC）；接管还原状态机（用临时目录假 socket，覆盖接管一半崩溃、socket 被官方重建、状态文件丢失等分支）；配置覆盖层读写。
- **实机验收脚本**（抄 fnos_music_ext `scripts/e2e_check.sh` 思路）：enable → 搜索 → 播放 → tee 命中 → 收藏入库 → 推荐 → WebUI → disable → 验证官方原生，全链路 checklist。

## 风险与合规

- **API 契约依赖**：官方升级可能变更接口 → 透传兜底 + 契约测试 + 文档化"升级后重跑 enable"。
- **`/run` 挂载安全**：容器随之可访问 `/run/docker.sock` 与其他应用 socket，等于获得宿主 Docker 控制权。自用 NAS 可接受，但必须：只挂给 fnext 这一个容器、代码只对 `trim_music*.socket` 动手、README 明示。替代方案（文件级挂载/符号链接）经分析不可行（bind mount 钉死 inode，接管改名失效）。
- **合规**：沿用项目现有免责口径——个人内网学习研究用，仅中继用户有权访问的内容，不绕付费墙/DRM；边听边存产物为本地临时缓存，入库行为由用户主动收藏触发。
- **源稳定性**：平台接口波动由熔断/自适应超时/多源 fallback 缓解，不保证长期可用。

## 借鉴与署名

机制设计（多源调度/分层缓存/tee 完整性/榜单接口清单/接管守卫）参考 [javycoder/fnos_music_ext](https://github.com/javycoder/fnos_music_ext)（MIT）。如直接引用其代码片段（如端点映射），在 README/NOTICE 保留署名与许可证。

## 默认决策记录（用户已确认）

1. 同仓 `fnext/` 包而非新仓；进程独立、代码 `import app.*` 复用
2. 单用户，不做多用户收藏隔离
3. 播放首选 302 直链、代理流兜底
4. 收藏即入库（红心 = 进 singles 库）；滚动缓存 keep 20
5. 推荐只做免登录榜单，LLM/个性化每日推荐不做
6. WebUI 无构建链、独立端口 8778
7. 配置双文件（`fnext.yaml` 覆盖层可写，`config.yaml` 只读引用）
8. **Docker 优先**（compose profile，与核心服务同镜像）；宿主 systemd 仅文档备选
9. E1→E4 顺序，WebUI 最后（fnextctl 从 E1 兜底"随时停用"）
10. "停用"为进程内状态机（还原 socket、保留 WebUI 待命），非停容器
