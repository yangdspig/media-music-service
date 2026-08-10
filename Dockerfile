# 注意：不要加 "# syntax=docker/dockerfile:1" 指令，否则 BuildKit 会拉取
# docker/dockerfile 前端镜像，可能因镜像加速器故障导致构建失败（内置前端已够用）
# MediaMusicService 镜像
# 基础镜像：python:3.11-slim（Debian），与 musicdl 官方推荐版本一致

FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Shanghai \
    # N_m3u8DL-RE 是 .NET 程序，slim 镜像没有 libicu，用 invariant 全球化模式运行
    DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1

WORKDIR /app

# ---- 系统依赖 + 外部命令行工具 ----
# - Node.js：musicdl 下载 YouTube 音乐需要
# - N_m3u8DL-RE：musicdl 处理 HLS 流（Apple Music 等）需要
# - ffmpeg：音频转码/混流（N_m3u8DL-RE 混流、WhisperLRC 前置）
ARG N_M3U8DL_VERSION=v0.5.1-beta
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates ffmpeg unzip xz-utils \
    && rm -rf /var/lib/apt/lists/* \
    # Node.js LTS（用官方预编译包，避免 apt 源版本过旧）
    && curl -fsSL https://nodejs.org/dist/v20.19.0/node-v20.19.0-linux-x64.tar.xz -o /tmp/node.tar.xz \
    && tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1 \
    && rm /tmp/node.tar.xz \
    # N_m3u8DL-RE（linux-x64 单文件，tar.gz 格式）
    && curl -fsSL "https://github.com/nilaoda/N_m3u8DL-RE/releases/download/${N_M3U8DL_VERSION}/N_m3u8DL-RE_${N_M3U8DL_VERSION}_linux-x64_20251029.tar.gz" -o /tmp/nre.tar.gz \
    && tar -xzf /tmp/nre.tar.gz -C /usr/local/bin \
    && chmod +x /usr/local/bin/N_m3u8DL-RE \
    && rm /tmp/nre.tar.gz

# ---- Python 依赖（单独一层，利用构建缓存）----
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---- 应用代码 ----
COPY app ./app
COPY mcp_adapter.py ./
COPY config.yaml ./

# 数据目录（下载 + SQLite），运行时挂载卷持久化
RUN mkdir -p /app/downloads /app/data
VOLUME ["/app/downloads", "/app/data"]

# 8765: REST API；8766: MCP HTTP（可选）
EXPOSE 8765 8766

# 默认启动核心 REST 服务
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8765", "--app-dir", "/app"]
