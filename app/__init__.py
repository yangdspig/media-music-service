"""包初始化：导入任何 app 子模块前先生效的全局补丁放这里。"""
import os as _os
import socket as _socket

# 部署环境（NAS）IPv6 出口不通但 DNS 会返回 AAAA 记录：Python 网络库串行尝试
# IPv6 地址会卡到内核 TCP 超时（约 2 分钟/次），musicdl 多源搜索、httpx 封面下载
# 都会被拖挂/拖慢。这里全局过滤 getaddrinfo 的 IPv6 结果，对所有库（requests、
# httpx/httpcore）生效；curl_cffi 自带 happy eyeballs 不受影响。
# 如环境 IPv6 正常，设 MUSIC_SERVICE_ENABLE_IPV6=1 关闭该补丁。
if _os.environ.get("MUSIC_SERVICE_ENABLE_IPV6") != "1":
    import urllib3.util.connection as _urllib3_conn
    _urllib3_conn.allowed_gai_family = lambda: _socket.AF_INET

    _orig_getaddrinfo = _socket.getaddrinfo

    def _ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        return [ai for ai in _orig_getaddrinfo(host, port, family, type, proto, flags)
                if ai[0] != _socket.AF_INET6]

    _socket.getaddrinfo = _ipv4_getaddrinfo

from .config import settings, Settings
from .schemas import Track, SourceInfo, DownloadTask

__all__ = ["settings", "Settings", "Track", "SourceInfo", "DownloadTask"]
