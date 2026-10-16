# 飞牛音乐助手

将 MediaMusicService 加入飞牛桌面，在窗口中打开网页。入口使用飞牛统一网关，同域名、同协议，无需单独向浏览器开放 8765 端口。

![音乐助手图标](fpk/ICON_256.PNG)

## 安装

1. **先升级音乐服务镜像**，使用包含本目录配套 Web 适配的版本；已有旧镜像只安装 FPK 会打不开页面。按 [部署说明](../DEPLOY.md#飞牛桌面应用) 加载 `media-music-service:fnos-20261016` 并重建现有服务容器。
2. 在 NAS 本机确认 `http://127.0.0.1:8765/app/MUSICASSISTANT/api/v1/health` 返回 JSON，其中 `ok` 为 `true`。
3. 飞牛需要 Python 3。若系统没有 `python3`，在应用中心安装 `python312` 运行时；入口优先使用它。
4. 将 `artifacts/MUSICASSISTANT.fpk` 下载到电脑，在飞牛 **应用中心 → 手动安装** 中选择该文件。
5. 桌面点击 **音乐助手**，在飞牛窗口中打开服务；启用了 API Key 时使用原来的密钥登录。

现有 `http://<NAS>:8765/` 地址继续可用。入口通过固定的 `127.0.0.1:8765` 访问后端，因此服务必须部署在同一 NAS，并使用 host 网络或向宿主机映射 8765。

FPK 只负责桌面入口和代理，不包含后端、音乐或配置凭证。启动、停止和卸载这个应用不会停止 Docker 音乐服务，也不会删除下载、媒体库、数据库或配置。

## 从源码构建

在仓库根目录执行；使用参考项目中已有的 fnpack 1.2.3：

```bash
FNPACK="$HOME/github/nasctl/fnos-fpk/tools/fnpack" bash scripts/package-fpk.sh
```

也可以把工具放到 `fnos-fpk/tools/fnpack`，赋予执行权限后直接运行：

```bash
bash scripts/package-fpk.sh
```

输出为 `artifacts/MUSICASSISTANT.fpk` 和 `artifacts/MUSICASSISTANT.fpk.sha256`。可用第一个参数指定其他输出目录。构建在临时目录完成，无需在源码中保留空的 `wizard/` 目录。

图标源文件为 [`web/public/music-assistant.svg`](../web/public/music-assistant.svg)，包图标和入口图标各包含一份 64/256 像素 PNG。修改 SVG 后应重新渲染这四个 PNG 再构建；网页 favicon 直接使用 SVG。

## 工作方式

```text
飞牛桌面浏览器（HTTP 或 HTTPS）
  → /app/MUSICASSISTANT/
  → 飞牛统一网关 → /var/apps/MUSICASSISTANT/target/app.sock
  → Python 标准库代理 → 127.0.0.1:8765
```

代理只转发字节，后端识别网关前缀；保留 keep-alive、音频 Range 和流式传输。Web 根据当前入口设置资源、路由与 API 基址，深层页面刷新仍然能加载。

## 排查

- **显示 502**：入口已运行，但本机 8765 服务不可达。检查现有音乐容器及端口映射。
- **接口返回 HTML、405 或页面空白**：通常仍在运行旧镜像。先检查上述带前缀的健康接口，再确认已重建容器；仅重启旧容器不会加载新代码。
- **缺少 Python 或启动失败**：查看应用中心错误信息与 `/var/apps/MUSICASSISTANT/var/proxy.log`。
- **安装后没有新图标**：先确认应用为运行状态，再刷新飞牛桌面。覆盖安装后浏览器可能缓存旧图标，必要时重新登录或无痕访问。

本地已覆盖 Web 路径、代理转发与生命周期测试；飞牛桌面注册和真实网关登录需要在安装后核对。
