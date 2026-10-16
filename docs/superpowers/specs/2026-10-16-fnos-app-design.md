# 飞牛音乐助手应用设计

日期：2026-10-16。用户已确认采用推荐方案并直接实施。

## 目标与范围

生成可在飞牛应用中心手动安装的 `MUSICASSISTANT.fpk`，桌面显示“音乐助手”，点击后在飞牛窗口内打开现有 MediaMusicService。使用蓝色音符图标，与现有 Web 音乐图标风格一致，同时提供网页图标。

FPK 仅管理入口代理，连接 NAS 本机 `127.0.0.1:8765`。音乐后端、配置和数据继续由现有 Docker 服务管理；停止或卸载入口不会停止后端或删除音乐数据。无需端口向导。

## 方案选择

1. **飞牛统一网关与 Unix Socket 代理（采用）**：复用 `~/github/nasctl/fnos-fpk` 已验证架构，支持 HTTPS 桌面同源嵌入，需要后端和前端支持网关子路径。
2. 端口快捷方式：改动较少，但 HTTPS 桌面嵌入 HTTP 网页存在混合内容问题，域名入口还需要额外暴露 8765。
3. FPK 自行部署整套服务：安装包含更多功能，但会重复现有 Docker 生命周期与数据目录配置，超出本次入口需求。

## 请求路径

浏览器 → 飞牛网关 `/app/MUSICASSISTANT/` → 应用目录 `app.sock` → Python 标准库代理 → `127.0.0.1:8765`。

代理按字节双向转发，不修改 HTTP 请求或响应，保留 keep-alive、多请求、上传、音频 Range 和流式响应。上游无法连接时返回 502。建立连接后移除连接超时，避免空闲连接或长任务在十秒后被中断。

后端只对完整路径段 `/app/MUSICASSISTANT` 识别网关前缀，以 ASGI `root_path` 交给 FastAPI/Starlette 路由。根路径 API、Web 和 MCP 调用仍可使用原地址。网关入口缺少末尾斜杠时，返回相对地址重定向，沿用浏览器原协议和主机。

## Web 兼容

- Vite 使用相对资源地址，同一个构建同时用于根路径与飞牛入口。
- 保留现有 HTML5 路由。HTML 明确声明 `<base href="/">`，后端返回网页时按请求的 `root_path` 设置 base，保证深层链接刷新、懒加载、图标和导航地址正确。
- Router 和 Axios 从 `document.baseURI` 读取部署根地址，API 请求始终经过当前入口。
- 试听签发接口返回带当前网关前缀的音频 URL，继续使用既有临时签名，不改变鉴权规则。
- 提取静态托管与网关适配到 `app/webapp.py`，避免继续扩展业务路由文件。

## 包结构与生命周期

`fnos-fpk/fpk/` 包含 manifest、四个 PNG 图标、入口 JSON、权限/资源 JSON、完整生命周期脚本及 `app/proxy.py`。应用名固定为 `MUSICASSISTANT`，版本 `1.0.0`，`platform="all"`、`ctl_stop=true`、`checkport=false`、`service_port=8765`。入口 ID 为 `MUSICASSISTANT.main`。

优先使用飞牛 python312，其次系统 python3。启动必须等到 Socket 可用才报告成功；停止时只结束入口代理；状态检查同时确认进程与 Socket。缺少 Python 时在应用中心错误日志中给出说明。

构建脚本使用 `FNPACK` 指定工具或本地 `fnos-fpk/tools/fnpack`，输出到 `artifacts/` 并生成 SHA-256。工具和 FPK 产物不入库。包内图标为 64 与 256 像素 PNG，各不超过 1 MiB；保留 SVG 源文件。

## 交付与验证

交付 FPK、对应服务镜像归档、构建脚本及安装说明。镜像必须重新构建并重建服务容器，旧镜像不支持网关路径。

先增加根路径/网关路径、鉴权、深层资源和试听前缀的回归测试，再实现适配。代理使用真实 Socket 验证 keep-alive、双向转发和上游不可达；生命周期脚本做启动、重复启动、状态与停止检查。执行后端全量测试、前端测试与生产构建、fnpack 构建及产物检查，并使用浏览器检查入口登录、导航及资源请求。

本地验证可以证明打包与路径行为；真实飞牛桌面注册、登录网关和设备窗口展示仍需安装后验收。没有用户额外指示时不替换正在运行的容器或安装应用。

## 参考

- 本地参考项目最终设计：`~/github/nasctl/fnos-fpk/docs/superpowers/specs/2026-09-04-ptctl-fpk-design.md`。
- [Vite 相对资源基址](https://vite.dev/guide/build#relative-base)。
- [Vue Router history 模式与部署子目录](https://router.vuejs.org/guide/essentials/history-mode)。

## 实际验证记录

- 改动前后端基线：228 passed；完成后完整回归：252 passed（包含 19 项 Web 入口测试和 5 项真实 Socket/生命周期测试）。
- 前端 `npm test` 与 `npm run build` 通过。
- Chromium 验证根路径和 `/app/MUSICASSISTANT` 两个入口：登录、API 基址、导航、刷新、含点歌单深链接、favicon 与 iframe 均通过，没有页面异常或静态资源失败。
- fnpack 1.2.3 构建通过；包内容、JSON、Bash、四个 PNG 尺寸及体积已检查。独立只读代码审查未发现需修复的问题。
- 最终镜像 `media-music-service:fnos-20261016` 在无外网、无宿主机挂载的临时容器中验证，源码摘要与交付源码一致，根路径与网关路径的网页、健康接口、图标、404 和音频 Range 均通过。
- 交付路径：`artifacts/MUSICASSISTANT.fpk`、`artifacts/media-music-service-fnos-20261016-linux-amd64.tar.gz`，各附 SHA-256 文件。
- 没有安装 FPK 或替换当前运行的服务；真实飞牛桌面与网关登录留待手动安装后验收。
