# Docker 部署指南

本文档面向**在另一台 Linux 机器**上构建并运行 MediaMusicService 的场景。

## 一、需要拷贝的文件清单

把 `media-music-service` 目录下这些文件/目录打包拷到目标机器即可（其余如 `downloads/`、`data/`、`__pycache__/` 无需拷贝）：

```
media-music-service/
├── web/                  # 前端源码与 package-lock.json（Docker 前端构建必需；无需拷 node_modules/dist）
├── app/                  # 核心服务代码（必拷，含 __init__.py 及全部 .py）
│   ├── __init__.py
│   ├── config.py
│   ├── schemas.py
│   ├── registry.py
│   ├── search.py
│   ├── playlist.py
│   ├── download.py
│   ├── storage.py
│   ├── itunes.py
│   ├── album.py
│   ├── archive.py
│   └── main.py
├── mcp_adapter.py        # MCP 适配器（必拷）
├── config.yaml           # 配置文件（必拷，可在目标机器上再改）
├── config.example.yaml   # 镜像内默认配置（必拷，不含部署凭证）
├── requirements.txt      # Python 依赖（必拷）
├── constraints.txt       # Docker 依赖版本约束（必拷）
├── Dockerfile            # 镜像定义（必拷）
├── docker-compose.yml    # 编排（推荐拷）
├── .dockerignore         # 构建瘦身（推荐拷）
└── README.md             # 说明（可选）
```

## 二、目标机器前置条件

- Linux（x86_64；ARM 需自行调整 Dockerfile 中 Node.js / N_m3u8DL-RE 的下载架构）
- Docker 20.10+，docker compose 插件（`docker compose version` 可用）
- 能访问外网拉取：PyPI、nodejs.org、GitHub Releases（构建期一次性）

## 三、构建与启动

镜像包含后端 Python 代码、依赖及前端 `web/dist`；运行一个核心服务即可通过 **8765** 访问页面和 API，无需另启 Vite 或前端容器。Docker 构建使用 `config.example.yaml`，部署凭证从宿主机 `config.yaml` 挂载。Python 版本约束和前端 `package-lock.json` 固定当前依赖。

```bash
cd media-music-service

# 1. 按需修改 config.yaml（下载目录、cookies、API Key 等）
vi config.yaml

# 2. 构建镜像
docker compose build

# 3. 启动核心 REST 服务
docker compose up -d music-service

# 4.（可选）同时启动 MCP HTTP 适配器，供远程 Agent 连接
docker compose --profile mcp up -d
```

### 导出镜像后在 NAS 加载

在源码目录执行（同时构建前后端，输出镜像归档及 SHA-256）：

```bash
bash scripts/package-image.sh media-music-service:web-p3-20261009-r2
```

如需复用本机已有的 Node 构建镜像：

```bash
WEB_BUILD_IMAGE=node:22-slim bash scripts/package-image.sh media-music-service:web-p3-20261009-r2
```

归档输出到 `artifacts/media-music-service-web-p3-20261009-r2-linux-amd64.tar.gz`（架构后缀取自实际镜像）。把归档和 `.sha256` 拷到 NAS；部署目录为 `/vol1/1000/media-music-service` 的现有安装可执行：

```bash
cd /vol1/1000/media-music-service
sha256sum -c media-music-service-web-p3-20261009-r2-linux-amd64.tar.gz.sha256
docker image load -i media-music-service-web-p3-20261009-r2-linux-amd64.tar.gz
cat > docker-compose.web-p3.yml <<'YAML'
services:
  music-service:
    image: media-music-service:web-p3-20261009-r2
YAML
docker compose -p media-music-service -f docker-compose.yml -f docker-compose.web-p3.yml up -d --no-deps --no-build --pull never music-service
docker compose -p media-music-service -f docker-compose.yml -f docker-compose.web-p3.yml logs --tail=50 music-service
```

覆盖文件仅指定新镜像，沿用原 Compose 的网络、端口、配置和媒体库挂载。后续 `up` 使用相同两个 `-f`；或者把原文件中 `music-service.image` 改为该版本后恢复单文件命令。若需同步升级 MCP，再给覆盖文件的 `mcp-adapter` 指定同一镜像。

部署完成访问 `http://192.168.254.111:8765/`。此版本包含搜索/榜单结果保留、大小排序及字段、统一六源、网易默认榜单和飞牛数量修复。无需新增配置字段；挂载配置中已有的 `default_sources` 优先于镜像初始六源。如果仍是旧五源或自定义来源，在设置 → 下载与归档 → 使用常用六源后保存即可同步搜索页，无需重启。

### 更新应用代码

Dockerfile 将应用代码和前端产物复制进镜像；Compose 挂载的是配置与数据。修改 `app/` 或 `web/` 后，需要构建新镜像并重建容器。`docker compose restart` 适用于读取更新后的挂载配置，不会加载工作区里修改过的应用代码。

使用本仓库包含 `build: .` 的 Compose 配置时，在源码目录运行：

```bash
docker compose up -d --build music-service
```

NAS 部署配置若只有 `image:`，先构建本地镜像或取得已发布的新版本，将 NAS 的 `music-service.image` 指向新镜像，再在 NAS 部署目录运行 `docker compose up -d music-service`。不要在源码目录直接套用默认数据挂载来更新已有 NAS 部署。

若使用 5173 端口的 Vite 前端，它会代理到本机 8765 后端。新页面调用接口返回 404 时，查看 `http://<host>:8765/openapi.json` 是否包含该 API；缺少路径说明当前后端仍未加载对应代码。榜单异步解析入口为 `POST /api/v1/charts/{source}/{chart_id}/parse`。

## 四、验证

```bash
# 健康检查
curl http://127.0.0.1:8765/api/v1/health
# 期望：{"ok":true,"musicdl":"2.13.4"}

# 查看源可用性（哪些源因缺 cookies/网络被标记不可用）
curl http://127.0.0.1:8765/api/v1/sources | jq '.[] | {name,available,note}'

# 搜索冒烟
curl 'http://127.0.0.1:8765/api/v1/search?keyword=周杰伦&limit=3' | jq '.total'
```

## 五、目录与持久化

- 下载文件：宿主机 `./downloads`（compose 里映射到 `/app/downloads`），建议改成你的媒体库路径
- 任务/历史库：宿主机 `./data/music_service.db`
- 配置：宿主机 `./config.yaml` 挂载进两个容器，是**唯一配置源**（核心服务 + MCP 适配器共用）。Web 保存后即时生效字段由核心服务直接应用；监听/路径/线程等字段需重启核心服务，MCP 自身配置或 API Key 变化后需重启 MCP 容器
- **【常见坑】`config.yaml` 里的所有路径（`download_root`/`db_path`/`library_root` 等）必须填容器内路径**——即 compose volumes 冒号**右侧**的挂载点（如 `/app/downloads`、`/app/data/...`、`/library`）。填宿主机路径不会报错，服务会静默在容器临时层建目录：下载显示"成功"但宿主机上看不到文件、数据库重启即丢失
- **媒体库（可选，archive_album 归档目标）**：在 `docker-compose.yml` 的 volumes 里取消注释媒体库挂载行（如 `/vol02/1000-0-ba5fad3f/Music:/library:rw`），并把 `config.yaml` 的 `library_root` 设为 `/library`，重启生效。归档目录结构为 `{library_root}/{艺人}/{专辑}/`，多 Disc 专辑用 `CD1/CD2` 子目录
- **MCP HTTP 适配器（可选）**：启用前在 `config.yaml` 把 `mcp.transport` 改为 `http`、`mcp.service_url` 改为 `http://music-service:8765`，再 `docker compose --profile mcp up -d`。适配器配置全部来自 `config.yaml` 的 `mcp` 段，compose 里无需再设环境变量。**注意：`music-service` 服务名依赖 compose 默认 bridge 网络的 Docker DNS；若两个容器改用 `network_mode: host`，`mcp.service_url` 必须改为 `http://127.0.0.1:8765`，否则报 `Name or service not known`**

## 六、常见问题

### Web 页面

镜像由 Node 构建前端，再由 FastAPI 同源托管；访问 `http://<服务器 IP>:8765/` 即可。升级前端后需重新构建镜像。API Key 与 REST/MCP 使用同一个 `config.yaml` 顶层 `api_key`；未配置时可在登录页验证匿名访问后进入。

已支持概览、搜索、任务、榜单、专辑、歌单解析、媒体库、设置、飞牛歌单和 QQ/网易云扫码登录，PC 使用表格，960px 以下使用卡片和底部导航。媒体库浏览只读取已配置且挂载的库根；若提示目录不存在，请检查卷挂载。

P3 升级无需新增必填配置。镜像需安装更新后的 `requirements.txt`（新增 `qrcode`）；设置页写回需要 `config.yaml` 和所在目录可写。现有单文件 bind mount 可继续使用：常规保存先原子替换，挂载点返回 `EBUSY` 时改为原位写入并刷盘；原位写入期间若服务意外中断，建议用配置备份恢复。`extra_library_roots` 与飞牛 `path_map` 按完整映射替换，支持删除条目。环境变量覆盖的字段在设置页只读，应在部署配置中修改。

扫码请求由后端访问平台，浏览器不需要直接连接登录接口。会话不落库，服务重启后需重新生成二维码；成功才自动保存搜索/下载/解析凭证。飞牛追加可使用当前内存中的已完成单曲任务，服务重启前的任务请改用文件路径。

1. **构建时拉 Node.js / N_m3u8DL-RE 失败**：目标机器需能访问 nodejs.org 和 github.com；离线环境可改为构建前手动下载对应发行包放进镜像（调整 Dockerfile 用 `COPY` 替代 `curl`）。
2. **ARM 机器（如树莓派/部分 NAS）**：把 Dockerfile 中 `linux-x64` 改为 `linux-arm64`，Node.js 同理换 `node-v20.19.0-linux-arm64.tar.xz`。
3. **海外源（Spotify/YouTube Music 等）超时**：属网络环境限制，与镜像无关；可通过 musicdl 的代理配置或在宿主机/网关层解决。
4. **HLS/Apple Music 下载报错**：确认容器内 `N_m3u8DL-RE` 可用：`docker exec media-music-service N_m3u8DL-RE --version`。
5. **防火墙**：只需开放 `8765`（REST）；只有启用 MCP HTTP 适配器时才需 `8766`。
6. **搜索/下载整体卡死或极慢（NAS 常见）**：若机器通过路由广播拿到了 IPv6 地址但 IPv6 出口不通，DNS 返回的 AAAA 记录会让 Python 网络库串行尝试 IPv6 并卡到内核 TCP 超时（约 2 分钟/次），表现为多源搜索超时、封面下载极慢。镜像已内置 IPv4-only 补丁（`app/__init__.py`，DNS 解析层过滤 IPv6 结果）；确认环境 IPv6 正常后可用环境变量 `MUSIC_SERVICE_ENABLE_IPV6=1` 关闭补丁。治本请在路由器/NAS 网络设置里关闭或修复 IPv6。
7. **MCP 适配器报 `Name or service not known`**：`network_mode: host` 下没有 Docker 内嵌 DNS，服务名 `music-service` 无法解析，把 `config.yaml` 的 `mcp.service_url`（或环境变量 `MUSIC_SERVICE_URL`）改为 `http://127.0.0.1:8765`。

## 七、升级 musicdl

进入容器或重建镜像即可（平台接口适配由 musicdl 作者维护）：

```bash
# 方式一：改 requirements.txt 后重建
docker compose build --no-cache && docker compose up -d

# 方式二：容器内临时升级（重启失效，仅调试用）
docker exec media-music-service pip install -U "musicdl>=2.13.4,<3.0"
```
