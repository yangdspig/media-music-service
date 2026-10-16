# Web 试听与移动端优化实施计划

> 使用 executing-plans 在当前会话顺序实施；用户已批准设计。

**目标：** 完成概览源状态、多首榜单试听、手机源列表收敛与底部榜单入口。

**架构：** 独立 preview 后端签发曲目临时 URL 并代理音频 Range；独立前端播放控制器管理一个 audio 元素与试听队列，ChartsView 负责接入 TrackList。响应式折叠组件复用于概览与搜索。

**技术栈：** FastAPI、httpx、Vue 3、浏览器 HTMLAudioElement、Node 内置测试、pytest、Playwright。

**约束：** 小于 960px 默认折叠；状态圆点 12px 绿/红；API Key 不进入媒体 URL；不新增播放器依赖；不改变现有下载选择行为；保留用户 config.yaml 和未跟踪文件。

## 任务 1：音频接口

文件：新增 `app/preview.py`、`tests/test_preview.py`，修改 `app/main.py`、`docs/API.md`。

- [x] 用 TestClient 和 httpx MockTransport 编写失败测试：鉴权、缺失曲目、临时凭据篡改/过期、Range 206/416、上游错误与关闭连接。
- [x] 运行 `.venv/bin/python -m pytest tests/test_preview.py -q` 确认接口缺失导致失败。
- [x] 实现 `POST /api/v1/tracks/{track_id}/preview` 返回 `{url, expires_at}`；已缓存曲目使用 HMAC 限定曲目的短期令牌。`GET /api/v1/preview/{token}` 代理已缓存地址并转发 Range。
- [x] 验证 HTTP(S) URL、音频格式、上游状态，使用 raw 的 default_download_headers/cookies；流结束或断开关闭连接。
- [x] 重跑接口测试。

## 任务 2：播放队列与页面

文件：新增 `web/src/utils/preview-player.js`、`web/src/components/TrackPreviewPlayer.vue`、`web/tests/preview-player.test.js`；修改 `TrackList.vue`、`ChartsView.vue`、`web/package.json`。

- [x] 编写播放控制器失败测试：单首/队列顺序、暂停恢复、停止归零、进度定位、切歌、自然结束、过期重试、快速切歌竞态及清理。
- [x] `node --test web/tests/preview-player.test.js` 确认模块缺失。
- [x] 控制器接口 `createPreviewPlayer({audio, resolveUrl, onChange})`：`playTracks(tracks)`、`playAt(index)`、`toggle()`、`stop()`、`seek(seconds)`、`dispose()`；状态包含 queue/index/status/currentTime/duration/error。
- [x] 播放器组件渲染状态、控制按钮、range 与队列跳转；ChartsView 用 ref 暴露的 `playTracks()` 接收 TrackList 的 `preview` 事件。
- [x] TrackList 默认不启用试听；榜单传入 previewable 后显示可点击歌名、试听已选、试听全部。手机列表避免 label 内嵌交互按钮。
- [x] 切榜单/平台或卸载时停止和释放播放器；前端错误区分授权失败、音频失败与自动播放受限。
- [x] 重跑控制器测试并构建。

## 任务 3：响应式调整

文件：新增 `MobileDisclosure.vue`，修改 `DashboardView.vue`、`SearchView.vue`、`AppShell.vue`、`style.css`。

- [x] 通用响应式折叠：手机提供带 aria-expanded 的标题按钮与摘要；桌面始终显示内容。
- [x] 概览状态使用 12px 绿/红圆点及可读状态标签；摘要显示可用/异常数量。
- [x] 搜索源保留原 Set 选择逻辑，手机折叠摘要显示已选数及前两个源名。
- [x] 底部导航过滤项将 albums 替换为 charts。
- [x] 构建并使用 390px/1440px 浏览器检查折叠与导航。

## 任务 4：整体复核

- [x] 使用浏览器拦截 API 并提供 WAV 音频，验证单曲、多选、全部、暂停、播放、停止、切歌、进度定位、移动端歌名点击不改变选择。
- [x] 完整 pytest、Node 播放测试、生产构建、git diff --check。
- [x] 复核未覆盖的网络/格式限制，记录验证结果与变更文件。

## 验证结果（2026-10-16）

- 完整后端测试：228 passed（新增 12 项试听测试）。
- 播放控制器：8 项测试通过，覆盖队列、定位、暂停恢复、停止、竞态、错误重试与卸载。
- 生产构建通过；git diff --check 通过。
- Chromium 390×844 / 1440×1000，使用拦截的 API 和可解码 WAV 验证全部试听操作、自然播完接续、榜单/路由清理、源折叠与选择保留、导航及页面无横向溢出。未对第三方音源进行逐平台实播测试。
- 独立审查发现并修复等待音源响应头时断开未取消、响应头/体发送失败时上游连接未关闭两个问题，均有红绿回归测试。
- 浏览器验证脚本：`/tmp/media-music-web-check.cjs`；截图：`/tmp/media-mobile-dashboard.png`、`/tmp/media-mobile-player.png`、`/tmp/media-desktop-player.png`。
- 镜像 `media-music-service:web-preview-20261016` 已构建，ID 为 `b9bbaa22edff`，架构 linux/amd64；归档与 SHA-256 校验文件位于 `artifacts/`。镜像内健康检查、前端资源、试听鉴权与 Range 响应、归档 SHA-256 校验通过。
- 原有 config.yaml 与未跟踪中文清单未改动，不纳入本次提交；镜像归档按现有忽略规则留在本地。
