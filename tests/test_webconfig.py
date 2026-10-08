"""Web 控制台配置接口测试：脱敏、掩码跳过、热应用、重启字段、校验失败。"""
import yaml
import pytest
from fastapi.testclient import TestClient

from app.config import FnosMusicConfig, SourceConfig, settings
from app.main import app
from app.webconfig import MASK

CFG_TEMPLATE = '''# 测试配置 注释甲
server:
  host: "0.0.0.0"
  port: 8765

# 默认源注释
default_sources:
  - MiguMusicClient

# 清理规则注释
cleanup:
  after_archive: true
  periodic: true
  interval_s: 21600
  max_size_gb: 10
  keep_hours: 24

# 每个源的下载线程数
num_threads: 5

# 源 cookies 注释乙
sources:
  QQMusicClient:
    search_cookies: "cookie-原值-secret"
    enabled: true
'''


@pytest.fixture()
def cfg_file(tmp_path, monkeypatch):
    """把配置写回引导到临时文件，避免测试污染真实 config.yaml。"""
    dst = tmp_path / "config.yaml"
    dst.write_text(CFG_TEMPLATE, encoding="utf-8")
    monkeypatch.setenv("MUSIC_SERVICE_CONFIG", str(dst))
    return dst


@pytest.fixture()
def client():
    return TestClient(app)


def test_get_masks_sensitive(client, monkeypatch):
    """GET 脱敏：响应中不出现明文 cookie/password/api_key；空值原样返回。"""
    monkeypatch.setattr(settings, "api_key", "super-secret-key")
    monkeypatch.setattr(settings, "sources", {
        "QQMusicClient": SourceConfig(search_cookies="cookie-原值-secret", quark_cookies=""),
    })
    monkeypatch.setattr(settings, "fnos_music",
                        FnosMusicConfig(base_url="https://fnos.local", username="u",
                                        password="pw-secret"))
    r = client.get("/api/v1/config", headers={"X-API-Key": "super-secret-key"})
    assert r.status_code == 200
    for plain in ("super-secret-key", "cookie-原值-secret", "pw-secret"):
        assert plain not in r.text
    d = r.json()
    assert d["api_key"] == MASK
    assert d["sources"]["QQMusicClient"]["search_cookies"] == MASK
    assert d["sources"]["QQMusicClient"]["quark_cookies"] == ""  # 空值不加掩码
    assert d["fnos_music"]["password"] == MASK
    assert d["fnos_music"]["username"] == "u"  # 非敏感字段原样


def test_put_masked_values_skipped(cfg_file, client, monkeypatch):
    """掩码回传视为未修改：yaml 与运行态中的原值都不动；同请求其他字段正常生效。"""
    monkeypatch.setattr(settings, "sources", {
        "QQMusicClient": SourceConfig(search_cookies="cookie-原值-secret"),
    })
    r = client.put("/api/v1/config", json={
        "api_key": MASK,
        "sources": {"QQMusicClient": {"search_cookies": MASK, "enabled": False}},
    })
    assert r.status_code == 200
    fields = r.json()["fields"]
    assert fields["api_key"] == "unchanged"
    assert fields["sources.QQMusicClient.search_cookies"] == "unchanged"
    assert fields["sources.QQMusicClient.enabled"] == "hot"
    # 运行态：cookies 原值保留，enabled 已热应用
    assert settings.sources["QQMusicClient"].search_cookies == "cookie-原值-secret"
    assert settings.sources["QQMusicClient"].enabled is False
    # yaml：cookies 原值保留且未被掩码覆盖
    text = cfg_file.read_text(encoding="utf-8")
    assert "cookie-原值-secret" in text
    assert MASK not in text
    data = yaml.safe_load(text)
    assert data["sources"]["QQMusicClient"]["enabled"] is False


def test_put_hot_apply_and_comments_preserved(cfg_file, client, monkeypatch):
    """热应用字段：PUT 后 settings 立即变化；yaml 回写保留中文注释。"""
    monkeypatch.setattr(settings, "default_sources", ["MiguMusicClient"])
    monkeypatch.setattr(settings, "archive_comment", "yangds整理")
    r = client.put("/api/v1/config", json={
        "default_sources": ["KuwoMusicClient", "MiguMusicClient"],
        "archive_comment": "新整理标记",
        "cleanup": {"interval_s": 600},
    })
    assert r.status_code == 200
    assert r.json()["fields"] == {"default_sources": "hot", "archive_comment": "hot",
                                  "cleanup": "hot"}
    assert settings.default_sources == ["KuwoMusicClient", "MiguMusicClient"]  # 立即生效
    assert settings.archive_comment == "新整理标记"
    assert settings.cleanup.interval_s == 600
    text = cfg_file.read_text(encoding="utf-8")
    assert "# 测试配置 注释甲" in text and "# 源 cookies 注释乙" in text  # 注释保留
    data = yaml.safe_load(text)
    assert data["default_sources"] == ["KuwoMusicClient", "MiguMusicClient"]
    assert data["cleanup"]["interval_s"] == 600
    assert data["cleanup"]["max_size_gb"] == 10  # 未传的嵌套字段不被覆盖


def test_put_restart_field_not_applied(cfg_file, client, monkeypatch):
    """需重启字段：只回写 yaml，不改运行态；host/port 写回 server: 段。"""
    monkeypatch.setattr(settings, "num_threads", 5)
    monkeypatch.setattr(settings, "port", 8765)
    r = client.put("/api/v1/config", json={"num_threads": 9, "port": 9999,
                                           "download_root": "/data/downloads"})
    assert r.status_code == 200
    fields = r.json()["fields"]
    assert fields == {"num_threads": "restart", "port": "restart",
                      "download_root": "restart"}
    assert settings.num_threads == 5  # 运行态不变
    assert settings.port == 8765
    data = yaml.safe_load(cfg_file.read_text(encoding="utf-8"))
    assert data["num_threads"] == 9
    assert data["server"]["port"] == 9999  # 写进 server 段而非顶层
    assert data["download_root"] == "/data/downloads"


def test_put_fnos_music_merges_and_resets_client(cfg_file, client, monkeypatch):
    """fnos_music 部分更新：与现有配置合并校验，掩码 password 跳过，热应用后重置客户端。"""
    calls = []
    monkeypatch.setattr("app.fnos.reset_client", lambda: calls.append(True))
    monkeypatch.setattr(settings, "fnos_music",
                        FnosMusicConfig(base_url="https://old", username="u",
                                        password="pw-原值"))
    r = client.put("/api/v1/config", json={
        "fnos_music": {"base_url": "https://new", "password": MASK},
    })
    assert r.status_code == 200
    assert r.json()["fields"] == {"fnos_music.base_url": "hot",
                                  "fnos_music.password": "unchanged"}
    assert calls  # 重置钩子已调
    assert settings.fnos_music.base_url == "https://new"
    assert settings.fnos_music.password == "pw-原值"  # 掩码未覆盖原值
    data = yaml.safe_load(cfg_file.read_text(encoding="utf-8"))
    assert data["fnos_music"]["base_url"] == "https://new"
    assert "password" not in data["fnos_music"]  # 掩码字段不写回


def test_put_invalid_returns_400(cfg_file, client):
    """校验失败返回 400 与错误明细：类型错误与未知字段都拦。"""
    r = client.put("/api/v1/config", json={"cleanup": {"interval_s": "not-an-int"}})
    assert r.status_code == 400
    assert r.json()["detail"]
    r = client.put("/api/v1/config", json={"no_such_field": 1})
    assert r.status_code == 400
    # 校验失败不落盘
    assert "not-an-int" not in cfg_file.read_text(encoding="utf-8")


def test_system_status_degrades_gracefully(client, monkeypatch):
    """系统状态：各部分失败降级为 error 字段，整体不抛。"""
    monkeypatch.setattr(settings, "fnos_music", None)
    r = client.get("/api/v1/system/status")
    assert r.status_code == 200
    d = r.json()
    assert "sources" in d and "tasks" in d and "download_dir" in d
    assert "qq_auth" in d and "fnos_music" in d
    assert d["fnos_music"] == {"configured": False, "reachable": False}
    assert "active" in d["tasks"]
    assert "size_gb" in d["download_dir"]
