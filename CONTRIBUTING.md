# 贡献指南

感谢参与 MediaMusicService 的建设。本项目定位**纯内网自用的音乐/有声读物搜索下载服务**，请围绕这一定位贡献。

## 先读这些

- [README.md](README.md)：项目简介、快速开始、合规声明
- [docs/design.md](docs/design.md)：总体架构、关键取舍、范围外事项
- [ROADMAP.md](ROADMAP.md)：里程碑与待办方向
- [docs/API.md](docs/API.md) / [docs/MCP.md](docs/MCP.md)：接口契约

## 核心原则（重要）

1. **不 fork、不修改 musicdl 源码**。平台接口适配由上游 `CharlesPikachu/musicdl` 维护，我们只消费其 PyPI 版本（`>=2.13.4,<3.0`）。遇到源失效，优先升级到最新 musicdl 验证，而不是自己补丁。
2. **业务逻辑只在核心服务一份**。MCP 适配器、MoviePilot 插件都是薄客户端，只调 REST API，不重复实现搜索/下载逻辑。
3. **保持轻量**。不引入 Celery/Redis 等重量级中间件；纯内网场景内存队列 + SQLite 足够。
4. **合规底线**。不增加任何绕过付费墙/DRM 的能力，不提供破解付费内容的功能。

## 开发环境

```bash
python -m venv venv
venv/Scripts/python.exe -m pip install -r requirements.txt   # Windows
venv/bin/pip install -r requirements.txt                      # Linux/macOS
```

启动核心服务：`uvicorn app.main:app --host 0.0.0.0 --port 8765 --app-dir .`

## 提交改动前请自测

- 改动搜索/下载相关：`curl 'http://127.0.0.1:8765/api/v1/search?keyword=周杰伦&limit=1'` 有结果，且能提交一次下载并落盘成功
- 改动 MCP 适配器：用 MCP 客户端（或 fastmcp Client）跑通"搜歌 → 下载 → 查状态"链路
- 升级 musicdl 后：回归 M1 主链路（搜索 + 下载 + 歌词/标签）

## 代码风格

- Python 3.11；遵循项目现有风格，不做无关重构
- 模块职责保持单一（见设计文档 §3.2 模块拆分表），新增能力优先放入对应模块而非堆在 `main.py`
- 相对路径一律锚定到项目根（参考 `app/config.py` 的做法），不依赖进程 CWD

## 提 Issue / PR

- **源失效类问题**：注明源名称、musicdl 版本、复现关键词/URL，先确认是否升级 musicdl 可解决
- **功能建议**：先对照 ROADMAP 与"范围外事项"（设计文档 §6），确认不在排除范围内
- PR 请聚焦单一目的，说明动机与验证方式
