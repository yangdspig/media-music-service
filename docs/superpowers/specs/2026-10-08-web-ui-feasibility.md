# 独立 Web 前端可行性与开发方案

> 状态：**可行性已确认，方案 A（Vue 3 + Vuetify 3）经用户选定**（2026-10-08）；原型设计文稿见 [2026-10-08-web-ui-design.md](2026-10-08-web-ui-design.md)，HTML 原型见 2026-10-08-web-ui-prototype.html，待原型确认后按 P1→P3 动工。调研日期 2026-10-08。
> 目标：为 MediaMusicService 提供独立 Web UI，覆盖配置修改与全部业务功能的前端操作，适配 PC 与手机。

## 一、可行性结论：**高度可行，后端欠账很少**

**有利条件（已核实代码）**

1. **业务能力已 100% API 化**：28 个 REST 端点覆盖搜索/专辑/榜单/下载任务/媒体库/飞牛歌单全部业务，Web 前端是纯消费者，不需要为它发明新业务逻辑。
2. **配置消费方式天然支持热更新**：`app/config.py` 的全局 `settings` 被各模块**每次调用时现读**（`registry.py` 每次构建客户端现读 cookies/default_sources/num_threads；`cleanup.py:109` 清理循环每轮现读 interval；`qqauth.py:333` 保活循环现读 interval；`main.py` 鉴权每请求现读 api_key）——修改 `settings` 对象字段即对大多数配置即时生效，无需重启机制大改。
3. **配置持久化通道现成**：`docker-compose.yml` 已把 `config.yaml` bind-mount 进容器（`./config.yaml:/app/config.yaml`），Web 写回即持久化到宿主机。
4. **静态托管零成本**：FastAPI `StaticFiles(html=True)` 可把 SPA 构建产物挂在 `/`，与 `/api/v1/*` 同端口同源，无 CORS 问题；开发期用 Vite dev server 代理 `/api` 到 8765。
5. **鉴权模型可直接复用**：现有可选 `X-API-Key` 头；Web 端做一个极简"输入密钥"登录页，密钥存 localStorage，axios 拦截器统一带头。未启用 api_key 时直接放行。

**需要补的后端缺口（唯一实质新工作）**

| 缺口 | 说明 | 工作量 |
|---|---|---|
| 配置读写 API | 新增 `GET /api/v1/config`（脱敏返回）+ `PUT /api/v1/config`（校验→写回 yaml→热应用）。回写需保留注释 → 引入 `ruamel.yaml`（round-trip 模式）；脱敏字段（sources.*_cookies、fnos_music.password、api_key）返回掩码，PUT 收到掩码原样视为"未修改" | 中 |
| 热应用边界 | 热生效：api_key、default_sources、sources cookies、max_size_mb、archive_comment、cleanup/auth_refresh 子项、fnos_music（需给 `fnos.py` 惰性单例加一个重置钩子）。**标记"需重启"**：download_root、db_path、library_root/extra_library_roots、num_threads（运行中任务/线程池/DB 连接已持有旧值）、mcp 段（独立进程）——PUT 响应里逐字段告知 | 小 |
| 系统状态端点 | 新增 `GET /api/v1/system/status`：聚合源可用性、运行中任务数、下载目录占用、QQ 保活状态（下次刷新时间/凭证有效期）、飞牛连通性——供仪表盘一页展示 | 小 |
| 静态托管 | `main.py` 挂 `StaticFiles`，SPA history 路由回退 index.html；Dockerfile 加 node 构建阶段（镜像里已有 Node 20.19 可复用，但建议独立 builder stage 保持镜像干净） | 小 |
| 任务进度推送（可选） | 现状只能轮询 `GET /downloads`。P1 用前端轮询即可；后续可加 SSE 端点提升体验 | 可选 |

**固有限制（UI 再好看也改不了，需在界面上如实呈现）**：歌单解析同步阻塞（长 loading + 超时提示）；运行中任务不可取消（cancel 按钮仅 pending 态可用，置灰+提示）；任务重启后状态丢失（历史页查 `GET /history`）。

## 二、总体架构

```
浏览器（PC / 手机）
   │  同源 HTTP（/api/v1/* 走 API，其余路径回退 SPA）
   ▼
FastAPI :8765
   ├── /api/v1/**           现有 28 端点 + 新增 config/system 端点
   └── /*                   StaticFiles 托管 web/dist（SPA）
```

- 前端独立目录 `web/`（Vue/React 工程，Vite 构建），构建产物不进 git，Dockerfile 多阶段构建。
- 不改任何现有业务模块的行为；新增代码集中在 `app/webconfig.py`（配置读写）+ `main.py` 少量挂载。
- MCP 适配器不受影响。

## 三、页面规划（PC/移动端同一套响应式布局）

导航：PC 侧边栏抽屉，手机底部 Tab + 汉堡抽屉（Vuetify `v-navigation-drawer` + `v-bottom-navigation` 响应式切换）。

| 页面 | 功能 | 依赖端点 |
|---|---|---|
| 仪表盘 | 源健康、运行中任务、下载目录占用、QQ 保活/飞牛连通状态卡 | system/status（新增） |
| 搜索下载 | 关键词+源筛选 → 结果表格（手机端转卡片列表）→ 勾选/单行下载（选库/自动归档/体积上限/飞牛歌单） | search、downloads |
| 榜单 | 榜单目录 → 曲目表 → 全量/勾选下载 | charts |
| 专辑 | 搜索 → 详情（曲目表/简介/封面）→ 整张下载 → 归档（含 VA 标记、覆盖参数） | albums/* |
| 歌单解析 | 粘贴 URL → 曲目表 → 勾选下载（长耗时 loading 提示） | playlist |
| 任务中心 | 运行中任务（轮询刷新/取消）、历史记录 | downloads、history |
| 媒体库 | 库列表、歌词回填（dry_run 预览→执行两步）、曲目替换、清理（confirm 二次确认对话框）、单曲迁移 | libraries、library/* |
| 飞牛歌单 | 列表/详情/建补/追加/搜索（未配置 fnos_music 时显示引导页） | fnos/* |
| 设置 | 全配置分组编辑：基础（线程数/体积上限/归档注释）、源与 cookies、库根（标"需重启"）、清理策略、QQ 保活、飞牛音乐、API Key、MCP 段（只读+提示改文件重启）；保存按钮区分"热生效/需重启"提示 | config（新增） |

## 四、实施分期

### P1：后端补能 + 前端骨架 + 核心闭环
1. `app/webconfig.py`：GET/PUT config（ruamel 回写、脱敏、热应用、重启标记）+ `GET /system/status`；补 pytest（脱敏不泄密、掩码回传不改值、热应用生效断言）
2. `web/` 工程初始化（Vite + 选定框架 + axios 封装 + 登录/鉴权拦截 + 响应式布局骨架 + 路由）
3. 仪表盘 + 搜索下载页 + 任务中心页（含轮询）——最小可用闭环
4. `main.py` 挂 StaticFiles；Dockerfile 加前端构建 stage；compose 不变（同端口）

### P2：业务页面补全
5. 榜单页 + 专辑页 + 歌单解析页
6. 媒体库管理页（dry_run→确认两步交互、confirm 对话框）

### P3：设置页 + 飞牛 + 打磨
7. 设置页（配置编辑，分组表单+热生效提示）
8. 飞牛歌单页
9. 移动端走查打磨（触控目标尺寸、表格→卡片降级、横屏）、E2E（PC + 手机视口）、README/DEPLOY/API 文档更新

### 后续可选
- SSE 任务进度推送替代轮询
- PWA manifest（手机"加到主屏"近似原生体验，成本极低）

## 五、关键技术决策点

1. **框架选型**（见方案选择）：推荐 Vue3 + Vuetify 3——Material Design 响应式开箱即用、组件密度适合管理界面、与 MoviePilot 生态技术栈一致（若日后重启 MP 插件 Vue 联邦页面，技能与组件经验直接复用）。
2. **配置回写保注释**：`ruamel.yaml` round-trip（新增一个轻依赖），不丢 config.yaml 现有的大量中文注释——这是"Web 改配置"能否被接受的关键体验点。
3. **脱敏与回传**：GET 返回 `••••` 掩码；PUT 携带掩码值视为未修改。api_key 支持"留空=关闭鉴权"。
4. **移动适配策略**：一套响应式代码，不做独立移动站；数据密度高的表格在小屏降级为卡片列表。
5. **不做用户体系**：维持纯内网定位，登录即"输入 API Key"，与现有鉴权模型一致。

## 六、风险

- ruamel.yaml 对极端手写 yaml（锚点/多文档）回写可能改变格式——config.yaml 结构简单，风险低，P1 用现有文件做回归验证。
- 前端工作量占大头（约 9 个页面），分期交付控制节奏；手机端表格交互需逐个页面走查。
- 热应用覆盖面以 P1 实测为准，个别漏网的配置项保守标"需重启"，不冒险。

## 七、方案选择（前端技术栈）

- **方案 A（推荐）**：Vue 3 + Vuetify 3 + Vite。Material 响应式开箱即用，管理界面组件全（data-table/stepper/dialog/bottom-nav），与 MoviePilot 前端同栈，后续 MP 插件可复用经验。
- **方案 B**：React 18 + Ant Design 5 + Vite。国内后台主流生态，PC 端表格/表单体验成熟；移动端需另配 antd-mobile 或自适应处理，移动体验略弱于 A。
- **方案 C**：无构建轻量方案（FastAPI 模板 + Vue3 global build + 原生 CSS，免 Node 工具链）。零构建、维护简单，但组件体系与工程化能力弱，页面多了以后维护成本高，移动端体验需手工打磨。
