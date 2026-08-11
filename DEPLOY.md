# Docker 部署指南

本文档面向**在另一台 Linux 机器**上构建并运行 MediaMusicService 的场景。

## 一、需要拷贝的文件清单

把 `media-music-service` 目录下这些文件/目录打包拷到目标机器即可（其余如 `downloads/`、`data/`、`__pycache__/` 无需拷贝）：

```
media-music-service/
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
├── requirements.txt      # Python 依赖（必拷）
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
- 配置：宿主机 `./config.yaml` 挂载进两个容器，是**唯一配置源**（核心服务 + MCP 适配器共用），改完 `docker compose restart` 生效
- **【常见坑】`config.yaml` 里的所有路径（`download_root`/`db_path`/`library_root` 等）必须填容器内路径**——即 compose volumes 冒号**右侧**的挂载点（如 `/app/downloads`、`/app/data/...`、`/library`）。填宿主机路径不会报错，服务会静默在容器临时层建目录：下载显示"成功"但宿主机上看不到文件、数据库重启即丢失
- **媒体库（可选，archive_album 归档目标）**：在 `docker-compose.yml` 的 volumes 里取消注释媒体库挂载行（如 `/vol02/1000-0-ba5fad3f/Music:/library:rw`），并把 `config.yaml` 的 `library_root` 设为 `/library`，重启生效。归档目录结构为 `{library_root}/{艺人}/{专辑}/`，多 Disc 专辑用 `CD1/CD2` 子目录
- **MCP HTTP 适配器（可选）**：启用前在 `config.yaml` 把 `mcp.transport` 改为 `http`、`mcp.service_url` 改为 `http://music-service:8765`，再 `docker compose --profile mcp up -d`。适配器配置全部来自 `config.yaml` 的 `mcp` 段，compose 里无需再设环境变量

## 六、常见问题

1. **构建时拉 Node.js / N_m3u8DL-RE 失败**：目标机器需能访问 nodejs.org 和 github.com；离线环境可改为构建前手动下载对应发行包放进镜像（调整 Dockerfile 用 `COPY` 替代 `curl`）。
2. **ARM 机器（如树莓派/部分 NAS）**：把 Dockerfile 中 `linux-x64` 改为 `linux-arm64`，Node.js 同理换 `node-v20.19.0-linux-arm64.tar.xz`。
3. **海外源（Spotify/YouTube Music 等）超时**：属网络环境限制，与镜像无关；可通过 musicdl 的代理配置或在宿主机/网关层解决。
4. **HLS/Apple Music 下载报错**：确认容器内 `N_m3u8DL-RE` 可用：`docker exec media-music-service N_m3u8DL-RE --version`。
5. **防火墙**：只需开放 `8765`（REST）；只有启用 MCP HTTP 适配器时才需 `8766`。

## 七、升级 musicdl

进入容器或重建镜像即可（平台接口适配由 musicdl 作者维护）：

```bash
# 方式一：改 requirements.txt 后重建
docker compose build --no-cache && docker compose up -d

# 方式二：容器内临时升级（重启失效，仅调试用）
docker exec media-music-service pip install -U "musicdl>=2.13.4,<3.0"
```
